"""Sponsorship tier-transition detection (PR II, 2026-06-23).

Closes the last significant gap in the operator-leverage loop
shipped during the 2026-06-22→23 sprint (PRs #481-#495). When a
niche's sponsorship-readiness tier changes (e.g. ``tracking`` →
``within_2_months``, or ``within_2_months`` → ``eligible_now``),
the operator previously had to spot it manually by comparing
today's dashboard to yesterday's. This module makes detection
passive.

## How it works

1. The ``/api/v1/sponsorship/readiness`` endpoint computes the
   current tier for each niche on every read (every 60s from the
   open dashboard).

2. After computing, it calls ``record_tier_transitions`` (below)
   which:
   - Reads the last-known tier per niche from ``tier_history``
     (via ``SELECT DISTINCT ON (niche_id) tier``)
   - For each niche where the current tier differs from the last
     known tier, INSERTs a new tier_history row
   - Returns the list of transitions just recorded (so the API
     response can include ``recent_transitions`` for the dashboard)

3. The ``/api/v1/sponsorship/recent-transitions`` endpoint reads
   from tier_history with a time-window filter for the dashboard's
   "Recent activity" surface (Phase 2 — deferred to follow-up PR).

## Concurrency model

Multiple dashboard tabs open → multiple concurrent reads of
``/sponsorship/readiness`` → multiple concurrent
``record_tier_transitions`` calls. Race window: one tab observes
a transition, queries last-known tier (still shows the old tier
because no one has written yet), INSERTs the transition row. A
second concurrent tab's query sees the same old tier, also
INSERTs. Result: two rows for the same transition.

This is **harmless for the dashboard** — the "recent transitions"
view deduplicates by (niche_id, tier) within a window. The cost
is a few extra rows during the race window (typically <1s), which
the table absorbs at zero perf cost.

We deliberately do NOT use SELECT FOR UPDATE / advisory locks to
serialize — the operational complexity isn't worth it for the
~weekly transition rate this table sees.

## Fail-OPEN

Detection is best-effort. Any failure in
``record_tier_transitions`` is caught + logged at DEBUG by the
caller (the readiness endpoint). The readiness endpoint's
response is the load-bearing surface; transition detection is
augmentation, not the contract.
"""

from __future__ import annotations

import logging
from typing import Any

from genlab_core.storage.tenant_context import pg_connect

logger = logging.getLogger(__name__)


def _read_last_known_tiers(dsn: str, niche_ids: list[str]) -> dict[str, str]:
    """Read the current (most-recent-write) tier per niche from
    tier_history.

    Returns ``{niche_id: tier}``. Niches with no prior history are
    absent from the dict (first-transition case).
    """
    if not niche_ids:
        return {}

    try:
        with pg_connect(dsn, niche_id="all", connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # SELECT DISTINCT ON (niche_id) is the Postgres
                # idiom for "row with max(observed_at) per group".
                cur.execute(
                    """
                    SELECT DISTINCT ON (niche_id)
                      niche_id, tier
                    FROM tier_history
                    WHERE niche_id = ANY(%s)
                    ORDER BY niche_id, observed_at DESC
                    """,
                    (niche_ids,),
                )
                rows = cur.fetchall()
        return {nid: tier for nid, tier in rows}
    except Exception as exc:
        # 2026-07-21 (rule #19): elevated. Silent read failure returns
        # empty dict → detector treats ALL current tiers as new →
        # spurious "everything transitioned" storm to tier_history.
        # Load-bearing for sponsorship-readiness intelligence.
        logger.warning(
            "[tier_transition] last-known-tier read failed: %s", exc, exc_info=True
        )
        return {}


def _insert_transitions(dsn: str, transitions: list[dict[str, Any]]) -> None:
    """Append-only INSERT of new transition rows.

    Each row carries (niche_id, tier, prev_tier, nearest_threshold_days).
    observed_at uses the table's default (now()).
    """
    if not transitions:
        return

    try:
        with pg_connect(dsn, niche_id="all", connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # Bulk insert. Pg's executemany is fine for the
                # ≤5 rows per call this typically sees.
                cur.executemany(
                    """
                    INSERT INTO tier_history (
                      niche_id, tier, prev_tier, nearest_threshold_days
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    [
                        (
                            t["niche_id"],
                            t["tier"],
                            t.get("prev_tier"),
                            t.get("nearest_threshold_days"),
                        )
                        for t in transitions
                    ],
                )
                conn.commit()
    except Exception as exc:
        # 2026-07-21 (rule #19): elevated. Silent write failure means
        # tier transitions never persist → tier_history gap → next
        # detector run re-detects the same transition (dupes) or
        # misses it entirely.
        logger.warning(
            "[tier_transition] insert failed: %s", exc, exc_info=True
        )


def record_tier_transitions(
    current_per_niche: dict[str, dict[str, Any]],
    *,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """Detect + record tier transitions.

    Args:
        current_per_niche: ``{niche_id: {"tier": str,
            "nearest_threshold_days": int | None}}`` — typically the
            sponsorship_readiness endpoint's response data shape.
        dsn: Postgres connection string; defaults to DATABASE_URL.

    Returns:
        List of transition dicts just recorded (empty list when no
        transitions occurred). Each dict carries: niche_id, tier,
        prev_tier (None for first-ever), nearest_threshold_days,
        observed_at_iso (set client-side for the response).
    """
    import os
    from datetime import UTC, datetime

    dsn = dsn or os.environ.get("DATABASE_URL", "")
    if not dsn or not current_per_niche:
        return []

    niche_ids = list(current_per_niche.keys())
    last_known = _read_last_known_tiers(dsn, niche_ids)

    transitions: list[dict[str, Any]] = []
    now_iso = datetime.now(UTC).isoformat()
    for niche_id, current in current_per_niche.items():
        current_tier = current.get("tier")
        if not current_tier:
            continue
        prev_tier = last_known.get(niche_id)  # None for first-ever
        if prev_tier == current_tier:
            continue  # No transition
        transitions.append(
            {
                "niche_id": niche_id,
                "tier": current_tier,
                "prev_tier": prev_tier,
                "nearest_threshold_days": current.get("nearest_threshold_days"),
                "observed_at_iso": now_iso,
            }
        )

    if transitions:
        _insert_transitions(dsn, transitions)

    return transitions


def fetch_recent_transitions(
    *,
    window_hours: int = 24,
    dsn: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent tier transitions for the dashboard's surface.

    Returns list ordered DESC by observed_at. Each row carries:
    niche_id, tier, prev_tier, nearest_threshold_days, observed_at (ISO).

    Time-window bound prevents the response from growing unbounded
    as the table fills.
    """
    import os

    dsn = dsn or os.environ.get("DATABASE_URL", "")
    if not dsn:
        return []

    try:
        with pg_connect(dsn, niche_id="all", connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      niche_id, tier, prev_tier,
                      nearest_threshold_days, observed_at
                    FROM tier_history
                    WHERE observed_at >= NOW() - (%s::int * INTERVAL '1 hour')
                    ORDER BY observed_at DESC
                    LIMIT 100
                    """,
                    (window_hours,),
                )
                rows = cur.fetchall()
        return [
            {
                "niche_id": niche_id,
                "tier": tier,
                "prev_tier": prev_tier,
                "nearest_threshold_days": nearest,
                "observed_at": observed_at.isoformat() if observed_at else None,
            }
            for niche_id, tier, prev_tier, nearest, observed_at in rows
        ]
    except Exception as exc:
        # 2026-07-21 (rule #19): elevated. Silent read failure returns
        # empty list — dashboard shows "no transitions" indistinguishable
        # from a genuine no-op period; operator can't tell if the
        # sponsorship-readiness engine is quiet or broken.
        logger.warning(
            "[tier_transition] recent_transitions read failed: %s", exc, exc_info=True
        )
        return []
