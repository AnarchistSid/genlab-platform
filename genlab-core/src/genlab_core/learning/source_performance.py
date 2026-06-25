"""Per-source bandit performance tracking (PR #571, 2026-06-25).

Phase B step 1 — the foundation for autonomous source management.
Today the system treats `source` as a CONTEXTUAL FEATURE in LinUCB
(one of 6 features driving hook-style selection) but NOT as a
discrete ARM with its own Beta(alpha, beta) reward distribution.

Without per-source arms, the system cannot answer:

  * Which YouTube source has produced the highest-reward content
    over the last 30 days?
  * Should we down-weight youtube_trending in favor of
    youtube_search at fetch time?
  * Are any sources producing content the operator consistently
    rejects?

This module adds that primitive. Each (niche, source) pair gets
its own Beta-distributed reward arm stored in the existing
``bandit_arms`` table. No schema change — we reuse the table
via an ``arm_id = 'source:<source_name>'`` convention.

## Public surface

  record_source_outcome(niche_id, source, reward, n_plays_delta=1)
    Update the (niche, source) arm with a Bayesian Beta update:
      alpha += reward
      beta  += (1 - reward)
      n_plays += n_plays_delta
    Reward should be in [0, 1]. Out-of-range values clamped.
    Called from the reward loop (future PR) when an insight
    collection completes for a published blueprint.

  get_source_performance(niche_id, source) -> SourcePerformance | None
    Read back the arm's posterior. None when the (niche, source)
    pair has no history yet (cold-start) OR DB unavailable.

  list_source_performance(niche_id) -> list[SourcePerformance]
    All sources for the niche, ordered by reward_mean DESC.
    Empty list on DB error. Used by the future dashboard card
    and the future soft-disable consumer.

## Storage convention — bandit_arms table reuse

  arm_id = f"source:{source}"   e.g. 'source:youtube_trending'
  alpha, beta — Beta posterior parameters (priors at 1, 1)
  n_plays — total observations
  extra — JSONB for future per-source metadata

This convention matches the existing patterns in bandit_arms
(hook-style arms use ``arm_id = f"hook_style:{name}"``, hour
arms use ``hour:{H}:{platform}``). Adding source: doesn't
conflict with any existing arm_id pattern.

## Foundation only

This PR ships the read/write helpers + tests. The reward-loop
WIRE (calling record_source_outcome on every reward window
close) ships in PR #571b. The dashboard surface (Mission
Control "your 10 best/worst sources" card) ships in PR #578.

The split is the same pattern as Phase A: ship the chokepoint
first, plug in consumers next. By the time the wires land,
operators can audit the API + check the math without worrying
about production behavior changing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourcePerformance:
    """Per-source posterior snapshot.

    Mirrors the bandit_arms row for one (niche, source) pair. Frozen
    so consumers (dashboard, source proposer) can't accidentally
    mutate state mid-evaluation.

    Fields:
      niche_id — RLS scope
      source — source name (e.g. 'youtube_trending')
      alpha, beta — Beta posterior parameters
      n_plays — total observations recorded
      reward_mean — alpha / (alpha + beta), the posterior mean
    """

    niche_id: str
    source: str
    alpha: float
    beta: float
    n_plays: int
    reward_mean: float


# Convention: prefix arm_id with 'source:' so source arms are
# distinguishable from existing arm_id patterns (hook_style:, hour:,
# etc). Future patterns: 'source_creator:<channel_id>' for finer-
# grained per-channel tracking.
_ARM_ID_PREFIX = "source:"


def _arm_id_for(source: str) -> str:
    """Compute the bandit_arms.arm_id for a given source name."""
    return f"{_ARM_ID_PREFIX}{source}"


def _connect():
    """Lazy-import + connect. None on missing DSN / error — callers
    fail-OPEN by returning None / [] from their public functions."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[source_perf] DATABASE_URL not set; tracking disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning("[source_perf] connection failed: %s", exc)
        return None


def _row_to_performance(row: dict) -> SourcePerformance:
    """Translate a bandit_arms row to SourcePerformance DTO. Strips
    the 'source:' prefix from arm_id to expose the operator-facing
    source name."""
    arm_id = row["arm_id"]
    source = arm_id[len(_ARM_ID_PREFIX) :] if arm_id.startswith(_ARM_ID_PREFIX) else arm_id
    alpha = float(row["alpha"])
    beta = float(row["beta"])
    return SourcePerformance(
        niche_id=row["niche_id"],
        source=source,
        alpha=alpha,
        beta=beta,
        n_plays=int(row["n_plays"] or 0),
        reward_mean=(alpha / (alpha + beta)) if (alpha + beta) > 0 else 0.0,
    )


def record_source_outcome(
    niche_id: str,
    source: str,
    reward: float,
    n_plays_delta: int = 1,
) -> bool:
    """Update the (niche, source) arm with a Bayesian Beta update.

    The Beta posterior update is:
      alpha_new = alpha + reward
      beta_new  = beta  + (1 - reward)

    Reward MUST be in [0, 1]. Out-of-range values are clamped + a
    WARNING logged — the caller bug shouldn't poison the bandit, but
    we want the noise visible for debugging.

    Returns True on successful write. Returns False fail-OPEN on
    DSN missing / connection error / invalid input — record_outcome
    is best-effort, not critical-path; a missed update just means
    the source's posterior is one observation behind reality.

    Idempotent INSERT via ON CONFLICT: first call for a (niche, source)
    pair creates the row with priors (1, 1) + the update; subsequent
    calls update the existing row.
    """
    if not niche_id or not source:
        logger.warning(
            "[source_perf] refusing to record: empty niche_id=%r or source=%r",
            niche_id,
            source,
        )
        return False
    # Clamp reward — never propagate a caller bug into the bandit
    if reward < 0:
        logger.warning("[source_perf] reward=%r below 0; clamping to 0", reward)
        reward = 0.0
    elif reward > 1:
        logger.warning("[source_perf] reward=%r above 1; clamping to 1", reward)
        reward = 1.0
    if n_plays_delta < 0:
        logger.warning("[source_perf] n_plays_delta=%r negative; clamping to 0", n_plays_delta)
        n_plays_delta = 0

    arm_id = _arm_id_for(source)
    conn_cm = _connect()
    if conn_cm is None:
        return False
    try:
        with conn_cm as conn:
            # UPSERT: insert with priors + the update if new, else
            # update the existing row. Beta(1,1) is the uniform prior;
            # the first call's `reward` becomes alpha=1+reward and
            # beta=1+(1-reward), preserving uniform-prior semantics.
            conn.execute(
                """
                INSERT INTO bandit_arms (niche_id, arm_id, alpha, beta, n_plays)
                VALUES (%s, %s, 1.0 + %s, 1.0 + %s, %s)
                ON CONFLICT (niche_id, arm_id) DO UPDATE
                SET alpha = bandit_arms.alpha + %s,
                    beta  = bandit_arms.beta  + %s,
                    n_plays = bandit_arms.n_plays + %s,
                    updated_at = NOW()
                """,
                (
                    niche_id,
                    arm_id,
                    reward,
                    1.0 - reward,
                    n_plays_delta,
                    reward,
                    1.0 - reward,
                    n_plays_delta,
                ),
            )
        return True
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[source_perf] write failed for (%r, %r): %s",
            niche_id,
            source,
            exc,
        )
        return False


def get_source_performance(niche_id: str, source: str) -> SourcePerformance | None:
    """Read the (niche, source) arm's posterior. None when the pair
    has no history (cold-start) OR DB unavailable.

    The cold-start None lets callers fall back to a default policy
    (e.g. 'allow the source by default; tighten only after data
    accumulates'). Distinguishing 'never seen' from 'seen but bad'
    is critical for new-source onboarding — you don't want to
    block a freshly-added source the first time it tries to publish.
    """
    if not niche_id or not source:
        return None
    arm_id = _arm_id_for(source)
    conn_cm = _connect()
    if conn_cm is None:
        return None
    try:
        with conn_cm as conn:
            row = conn.execute(
                """
                SELECT niche_id, arm_id, alpha, beta, n_plays
                FROM bandit_arms
                WHERE niche_id = %s AND arm_id = %s
                LIMIT 1
                """,
                (niche_id, arm_id),
            ).fetchone()
        if row is None:
            return None
        return _row_to_performance(row)
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[source_perf] get failed for (%r, %r): %s",
            niche_id,
            source,
            exc,
        )
        return None


def list_source_performance(niche_id: str) -> list[SourcePerformance]:
    """Return all source arms for the niche, ordered by reward_mean
    DESC. Empty list on DB error / empty niche / no source arms yet.

    The DESC ordering means the dashboard / proposer can take the
    top N directly without re-sorting. The "worst sources" view
    just reverses the list.

    Filters to ``arm_id LIKE 'source:%'`` so other arm types
    (hook_style:, hour:) aren't returned — the per-source view
    is its own surface.
    """
    if not niche_id:
        return []
    conn_cm = _connect()
    if conn_cm is None:
        return []
    try:
        with conn_cm as conn:
            rows = conn.execute(
                """
                SELECT niche_id, arm_id, alpha, beta, n_plays
                FROM bandit_arms
                WHERE niche_id = %s AND arm_id LIKE %s
                ORDER BY (alpha / NULLIF(alpha + beta, 0)) DESC NULLS LAST,
                         arm_id ASC
                """,
                (niche_id, f"{_ARM_ID_PREFIX}%"),
            ).fetchall()
        return [_row_to_performance(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[source_perf] list failed for %r: %s",
            niche_id,
            exc,
        )
        return []
