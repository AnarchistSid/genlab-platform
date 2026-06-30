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

  record_source_outcome(niche_id, source, reward, n_plays_delta=1, platform=None)
    Update the (niche, source[, platform]) arm with a Bayesian Beta update:
      alpha += reward
      beta  += (1 - reward)
      n_plays += n_plays_delta
    Reward should be in [0, 1]. Out-of-range values clamped.
    Called from the reward loop when an insight collection completes
    for a published blueprint. ``platform`` added 2026-06-30 for the
    per-platform bandit split.

  get_source_performance(niche_id, source, platform=None) -> SourcePerformance | None
    Read back the arm's posterior. None when the arm has no history
    yet (cold-start) OR DB unavailable.

  list_source_performance(niche_id, platform=None) -> list[SourcePerformance]
    Source arms for the niche, ordered by reward_mean DESC. When
    ``platform`` is provided, returns only per-platform arms for
    that platform; when None, returns ALL arms (aggregated +
    per-platform). Empty list on DB error.

## Storage convention — bandit_arms table reuse

  arm_id = f"source:{source}"                       e.g. 'source:youtube_trending'
  arm_id = f"source:{source}__{platform}"           e.g. 'source:youtube_trending__instagram'

  alpha, beta — Beta posterior parameters (priors at 1, 1)
  n_plays — total observations
  extra — JSONB for future per-source metadata

This convention matches the existing patterns in bandit_arms
(hook-style arms use ``arm_id = f"hook_style:{name}"``, hour
arms use ``hour:{H}:{platform}``, content-type arms use
``{content_type}__{platform}`` per ``bandit_platform_split``).
Adding source: doesn't conflict with any existing arm_id pattern.

## Per-platform split (2026-06-30)

Driven by pending_feedback data showing the SAME blueprints get
38× higher rewards on Facebook (avg 0.114) than YouTube (avg 0.003).
Aggregating across platforms destroys that signal — the bandit can
never learn "this source is great for Facebook but terrible for
YouTube." Per-platform arms split the (niche, source) row into
N rows: (niche, source), (niche, source__facebook),
(niche, source__instagram), (niche, source__youtube), ...

The legacy aggregated arms from PR #571b are NEVER mutated by the
per-platform path — they sit untouched as historical record. New
writes from ``metric_collector`` go to per-platform arms only; the
backfill script also creates per-platform arms with its own
``platform_backfilled_at`` idempotency stamp (independent of the
PR #571b ``bandit_backfilled_at`` stamp).

## Foundation only

This PR ships the read/write helpers + tests. The reward-loop
WIRE (calling record_source_outcome on every reward window
close) shipped in PR #571b. The dashboard surface (Mission
Control "your 10 best/worst sources" card) shipped in PR #578.
The per-platform split shipped 2026-06-30.

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

    Mirrors the bandit_arms row for one (niche, source[, platform])
    arm. Frozen so consumers (dashboard, source proposer) can't
    accidentally mutate state mid-evaluation.

    Fields:
      niche_id — RLS scope
      source — source name (e.g. 'youtube_trending')
      platform — platform name when this is a per-platform arm
                 (e.g. 'instagram'); None for the legacy aggregated arm.
                 Added 2026-06-30 for per-platform bandit split.
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
    platform: str | None = None


# Convention: prefix arm_id with 'source:' so source arms are
# distinguishable from existing arm_id patterns (hook_style:, hour:,
# etc). Future patterns: 'source_creator:<channel_id>' for finer-
# grained per-channel tracking.
_ARM_ID_PREFIX = "source:"

# Per-platform suffix separator. Mirrors the canonical
# ``content_type__platform`` format already used at
# ``meta_prior.py:100`` and ``bandit_platform_split.platform_arm_id``.
# A future ``source:youtube_trending__instagram`` arm is parseable as
# ``(source='youtube_trending', platform='instagram')`` via rpartition
# on ``"__"`` after stripping the ``source:`` prefix.
_PLATFORM_SEPARATOR = "__"


def _arm_id_for(source: str, platform: str | None = None) -> str:
    """Compute the bandit_arms.arm_id for a given source name.

    When ``platform`` is None (legacy), returns the aggregated arm id
    (``source:<source>``) — backward-compatible with PR #571.

    When ``platform`` is provided, returns the per-platform arm id
    (``source:<source>__<platform>``). This is the per-platform bandit
    split shipped after the 2026-06-30 data-driven discovery that
    Facebook avg reward (0.114) is 38× YouTube avg reward (0.003) for
    the same blueprints — aggregating across platforms destroys the
    signal.
    """
    if platform:
        return f"{_ARM_ID_PREFIX}{source}{_PLATFORM_SEPARATOR}{platform}"
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


def parse_source_arm_id(arm_id: str) -> tuple[str, str | None]:
    """Inverse of :func:`_arm_id_for`. Returns ``(source, platform_or_None)``.

    - ``"source:youtube_trending"`` → ``("youtube_trending", None)``
    - ``"source:youtube_trending__instagram"`` → ``("youtube_trending", "instagram")``
    - ``"source:reddit:aivideo__facebook"`` → ``("reddit:aivideo", "facebook")``

    Uses ``rpartition`` so source names that themselves contain ``__``
    (rare but possible — e.g. a hypothetical ``foo__bar`` source) are
    only split on the LAST ``__``, mirroring the convention used by
    ``bandit_platform_split.parse_platform_arm_id``.

    Unlike that helper, we don't gate on a known-platform allowlist —
    per-platform source arms span all 5 publishing platforms (the
    2026-06-30 data showed Facebook ↔ YouTube reward asymmetry across
    every active platform), so the validation surface is "any non-empty
    trailing segment after ``__``".
    """
    stripped = arm_id[len(_ARM_ID_PREFIX) :] if arm_id.startswith(_ARM_ID_PREFIX) else arm_id
    if _PLATFORM_SEPARATOR not in stripped:
        return stripped, None
    base, _, platform = stripped.rpartition(_PLATFORM_SEPARATOR)
    if not base or not platform:
        # Defensive: malformed arm_id ("source:__instagram" or
        # "source:foo__") — treat as no platform split so the row
        # surfaces as a legacy aggregated arm rather than crashing.
        return stripped, None
    return base, platform


def _row_to_performance(row: dict) -> SourcePerformance:
    """Translate a bandit_arms row to SourcePerformance DTO. Strips
    the 'source:' prefix from arm_id and splits any ``__platform``
    suffix into the dataclass ``platform`` field."""
    source, platform = parse_source_arm_id(row["arm_id"])
    alpha = float(row["alpha"])
    beta = float(row["beta"])
    return SourcePerformance(
        niche_id=row["niche_id"],
        source=source,
        platform=platform,
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
    platform: str | None = None,
) -> bool:
    """Update the (niche, source[, platform]) arm with a Bayesian Beta update.

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

    Idempotent INSERT via ON CONFLICT: first call for a (niche, arm_id)
    pair creates the row with priors (1, 1) + the update; subsequent
    calls update the existing row.

    Per-platform vs aggregated
    --------------------------
    When ``platform`` is provided (e.g. ``"instagram"``), the arm_id is
    ``source:<source>__<platform>`` and ONLY that per-platform arm is
    written. The legacy aggregated ``source:<source>`` row is never
    touched.

    When ``platform`` is None (legacy behaviour for callers that haven't
    been updated yet), the arm_id is ``source:<source>`` — the original
    aggregated arm shape from PR #571. This preserves backward compat
    for any external caller still on the old signature.

    The 2026-06-30 per-platform split was driven by data showing
    Facebook avg reward (0.114) ≈ 38× YouTube avg reward (0.003) for
    the same blueprints. Aggregating destroys that signal.
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

    # Normalize empty-string platform to None so callers can pass
    # ``platform=task_record.platform`` without pre-validating; this
    # mirrors how _update_source_arm_reward normalizes ``source``.
    platform_norm = (platform or "").strip() or None
    arm_id = _arm_id_for(source, platform=platform_norm)
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


def get_source_performance(
    niche_id: str,
    source: str,
    platform: str | None = None,
) -> SourcePerformance | None:
    """Read the (niche, source[, platform]) arm's posterior. None when
    the arm has no history (cold-start) OR DB unavailable.

    The cold-start None lets callers fall back to a default policy
    (e.g. 'allow the source by default; tighten only after data
    accumulates'). Distinguishing 'never seen' from 'seen but bad'
    is critical for new-source onboarding — you don't want to
    block a freshly-added source the first time it tries to publish.

    When ``platform`` is provided, looks up the per-platform arm
    (``source:<source>__<platform>``). When None, looks up the legacy
    aggregated arm (``source:<source>``). Existing PR #571 callers
    that don't pass ``platform`` are byte-identical to before.
    """
    if not niche_id or not source:
        return None
    platform_norm = (platform or "").strip() or None
    arm_id = _arm_id_for(source, platform=platform_norm)
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


def list_source_performance(
    niche_id: str,
    platform: str | None = None,
) -> list[SourcePerformance]:
    """Return source arms for the niche, ordered by reward_mean DESC.
    Empty list on DB error / empty niche / no source arms yet.

    The DESC ordering means the dashboard / proposer can take the
    top N directly without re-sorting. The "worst sources" view
    just reverses the list.

    Filters to ``arm_id LIKE 'source:%'`` so other arm types
    (hook_style:, hour:) aren't returned — the per-source view
    is its own surface.

    Platform filter (added 2026-06-30)
    ----------------------------------
    * ``platform=None`` (default, legacy) → returns ALL source arms,
      both aggregated (``source:<source>``) AND per-platform
      (``source:<source>__<platform>``). Same behaviour as PR #571 for
      DBs that haven't yet accumulated per-platform rows.
    * ``platform="instagram"`` (any non-empty string) → returns ONLY
      the per-platform arms for that platform via ``arm_id LIKE
      'source:%__<platform>'``. Used by the dashboard's
      ``?platform=instagram`` filter.
    * ``platform=""`` → empty filter ignored (treated as None) so
      callers can pass ``platform=request.args.get('platform')``
      without pre-validating.
    """
    if not niche_id:
        return []
    platform_norm = (platform or "").strip() or None
    conn_cm = _connect()
    if conn_cm is None:
        return []
    try:
        with conn_cm as conn:
            if platform_norm is None:
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
            else:
                # Per-platform filter: match arm_ids ending with the
                # exact platform suffix. The ``escape`` arg isn't needed
                # because platforms are strict identifiers ([a-z_]+).
                rows = conn.execute(
                    """
                    SELECT niche_id, arm_id, alpha, beta, n_plays
                    FROM bandit_arms
                    WHERE niche_id = %s AND arm_id LIKE %s
                    ORDER BY (alpha / NULLIF(alpha + beta, 0)) DESC NULLS LAST,
                             arm_id ASC
                    """,
                    (
                        niche_id,
                        f"{_ARM_ID_PREFIX}%{_PLATFORM_SEPARATOR}{platform_norm}",
                    ),
                ).fetchall()
        return [_row_to_performance(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[source_perf] list failed for %r: %s",
            niche_id,
            exc,
        )
        return []
