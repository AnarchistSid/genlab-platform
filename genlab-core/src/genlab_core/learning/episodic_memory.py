"""Episodic memory primitive for system events (Lever R1, MVP).

Today the agent has no "memory" of what worked. When a future LLM-as-
judge layer wants to answer "in the last week, hooks of style X
averaged engagement Y", there's no queryable surface — the data lives
in SharePoint analytics lists, Postgres tables, run_reports, and
calibration_logger, each with a different schema and access pattern.

Lever R1 ships the **data model + query primitive** for episodic
events. Each event is a typed record (event_type + niche_id +
blueprint_id + timestamp + payload). Pure-logic query/aggregation
helpers let callers filter by niche / event-type / date-range and
get back summary statistics or raw event lists.

The **backend** is deliberately not shipped here — the primitive
defines an ``EpisodicBackend`` Protocol that future PRs implement
(Postgres-backed with migration, in-memory for tests, JSON file for
ad-hoc operator use). This keeps THIS PR pure-Python, no migration,
no new dependencies — same "ship the primitive first, wire later"
pattern that proved itself across the 10 PRs of 2026-06-21.

## Event types (closed set)

- ``publish``: a blueprint was published to a platform
- ``reward_window_closed``: 48h reward window finalized for a published post
- ``operator_review``: operator clicked approve/reject in dashboard
- ``operator_edit``: operator PATCHed a blueprint's content
- ``bandit_pick``: bandit chose an arm for a blueprint
- ``experiment_assignment``: registered experiment assigned a blueprint
- ``post_bombed``: post engagement <30% of niche baseline
- ``unknown``: catch-all for events that don't fit above (filtered out
  of aggregations by default)

Closed-set discipline keeps downstream group-by aggregations clean
against free-text creativity (matches the Lever B / C / O / M /
vision_judge / experimentation pattern).

## Query semantics

``filter_events(events, *, niche_id=None, event_type=None,
since=None, until=None, limit=None)`` is a pure function. Filters
compose via AND. Caller invokes it directly on whatever event list
the backend produces — separation of concerns: backend = persistence,
filter = query logic, summarize = aggregation logic.

Run via:
    python -m genlab_core.learning.episodic_memory --help
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# Whitelisted event types. Closed-set discipline — free-text values
# are coerced to "unknown" on ingestion so aggregations stay clean.
EVENT_PUBLISH: Final[str] = "publish"
EVENT_REWARD_WINDOW_CLOSED: Final[str] = "reward_window_closed"
EVENT_OPERATOR_REVIEW: Final[str] = "operator_review"
EVENT_OPERATOR_EDIT: Final[str] = "operator_edit"
EVENT_BANDIT_PICK: Final[str] = "bandit_pick"
EVENT_EXPERIMENT_ASSIGNMENT: Final[str] = "experiment_assignment"
EVENT_POST_BOMBED: Final[str] = "post_bombed"
EVENT_UNKNOWN: Final[str] = "unknown"

_KNOWN_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        EVENT_PUBLISH,
        EVENT_REWARD_WINDOW_CLOSED,
        EVENT_OPERATOR_REVIEW,
        EVENT_OPERATOR_EDIT,
        EVENT_BANDIT_PICK,
        EVENT_EXPERIMENT_ASSIGNMENT,
        EVENT_POST_BOMBED,
        EVENT_UNKNOWN,
    }
)


def coerce_event_type(raw: str | None) -> str:
    """Coerce a free-text event_type to the whitelisted enum.

    Pure function. Unknown values → ``EVENT_UNKNOWN``. Keeps downstream
    aggregations clean against future code that emits new event_types
    without updating the whitelist.
    """
    if raw is None:
        return EVENT_UNKNOWN
    s = str(raw).strip()
    return s if s in _KNOWN_EVENT_TYPES else EVENT_UNKNOWN


@dataclass(frozen=True)
class EpisodicEvent:
    """One typed system event.

    The ``payload`` dict is intentionally untyped — different event
    types carry different fields (a ``publish`` event has
    ``platform``, a ``bandit_pick`` event has ``arm_id`` + ``score``,
    etc.). Consumers know their event_type's schema.

    ``timestamp`` is ISO-8601 UTC. Callers pass either a datetime
    (converted via .isoformat()) or a string; the dataclass stores
    the string form for serialization.
    """

    event_type: str  # one of EVENT_*, coerced via coerce_event_type
    niche_id: str
    blueprint_id: str
    timestamp: str  # ISO-8601 UTC
    payload: dict[str, Any] = field(default_factory=dict)


def new_event(
    *,
    event_type: str,
    niche_id: str,
    blueprint_id: str = "",
    payload: dict[str, Any] | None = None,
    when: datetime | None = None,
) -> EpisodicEvent:
    """Build an EpisodicEvent with sane defaults.

    Coerces event_type, defaults timestamp to now(UTC), defaults
    empty payload. Pure constructor — caller passes to the backend's
    ``record`` method when ready.
    """
    when = when or datetime.now(UTC)
    return EpisodicEvent(
        event_type=coerce_event_type(event_type),
        niche_id=str(niche_id or ""),
        blueprint_id=str(blueprint_id or ""),
        timestamp=when.isoformat(),
        payload=dict(payload) if payload else {},
    )


@runtime_checkable
class EpisodicBackend(Protocol):
    """Persistence protocol for episodic events.

    Concrete implementations:
    - ``InMemoryBackend``: test-only, no persistence
    - ``PostgresBackend``: future PR with migration for
      ``episodic_events`` table
    - ``JsonFileBackend``: optional ops-friendly impl for ad-hoc query

    Methods:
    - ``record(event)``: persist a single event
    - ``query(filters)``: return all events matching filters (sorted
      by timestamp ASC)
    """

    def record(self, event: EpisodicEvent) -> None: ...  # pragma: no cover
    def query(self, **filters: Any) -> list[EpisodicEvent]: ...  # pragma: no cover


def _parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse a timestamp string back to datetime, or pass datetime through.

    Returns None on parse failure — caller treats as "filter not applied"
    rather than crashing on a malformed timestamp.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def filter_events(
    events: list[EpisodicEvent],
    *,
    niche_id: str | None = None,
    event_type: str | None = None,
    since: str | datetime | None = None,
    until: str | datetime | None = None,
    limit: int | None = None,
) -> list[EpisodicEvent]:
    """Filter a list of events by AND of provided criteria.

    Pure function — no I/O, no backend dep. Caller fetches events
    from the backend (or any list source) and applies filters here.

    Filters compose via AND:
    - ``niche_id``: exact match
    - ``event_type``: exact match (coerced same way as ingestion)
    - ``since`` / ``until``: timestamp window (inclusive on both ends)
    - ``limit``: trim to first N after sort (most recent first by ts DESC)

    Returns sorted newest-first when ``limit`` is set, else input order.
    """
    target_type = coerce_event_type(event_type) if event_type is not None else None
    since_dt = _parse_dt(since)
    until_dt = _parse_dt(until)

    out: list[EpisodicEvent] = []
    for e in events:
        if niche_id is not None and e.niche_id != niche_id:
            continue
        if target_type is not None and e.event_type != target_type:
            continue
        if since_dt is not None or until_dt is not None:
            e_dt = _parse_dt(e.timestamp)
            if e_dt is None:
                # Malformed event timestamp — skip on any date filter
                continue
            if since_dt is not None and e_dt < since_dt:
                continue
            if until_dt is not None and e_dt > until_dt:
                continue
        out.append(e)

    if limit is not None and limit >= 0:
        # Sort newest-first when limiting so callers get the most
        # recent N rather than the first N
        out.sort(key=lambda x: x.timestamp, reverse=True)
        out = out[:limit]

    return out


def summarize_events(events: list[EpisodicEvent]) -> dict[str, Any]:
    """Aggregate a list of events into a stable-shape summary dict.

    Returns:
        {
            "total_events": N,
            "by_event_type": {event_type → count},
            "by_niche": {niche_id → count},
            "first_timestamp": "ISO-8601" | None,
            "last_timestamp": "ISO-8601" | None,
            "unique_blueprint_ids": N (count, not list — keeps shape compact),
        }

    Pure function. Dashboards consume this shape directly without
    per-period event parsing.
    """
    if not events:
        return {
            "total_events": 0,
            "by_event_type": {},
            "by_niche": {},
            "first_timestamp": None,
            "last_timestamp": None,
            "unique_blueprint_ids": 0,
        }

    by_event_type: dict[str, int] = {}
    by_niche: dict[str, int] = {}
    unique_bps: set[str] = set()
    timestamps: list[str] = []

    for e in events:
        by_event_type[e.event_type] = by_event_type.get(e.event_type, 0) + 1
        by_niche[e.niche_id] = by_niche.get(e.niche_id, 0) + 1
        if e.blueprint_id:
            unique_bps.add(e.blueprint_id)
        timestamps.append(e.timestamp)

    return {
        "total_events": len(events),
        "by_event_type": by_event_type,
        "by_niche": by_niche,
        "first_timestamp": min(timestamps),
        "last_timestamp": max(timestamps),
        "unique_blueprint_ids": len(unique_bps),
    }


class InMemoryBackend:
    """Test-only backend — keeps events in a Python list.

    NOT for production. The production backend (PostgresBackend below)
    persists durably via the ``episodic_events`` table (migration
    ``y5t6u7v8w9x0``).

    Implements the ``EpisodicBackend`` Protocol so callers can swap
    backends without touching call sites. The Protocol is verified
    via ``isinstance`` checks in tests.
    """

    def __init__(self) -> None:
        self._events: list[EpisodicEvent] = []

    def record(self, event: EpisodicEvent) -> None:
        self._events.append(event)

    def query(self, **filters: Any) -> list[EpisodicEvent]:
        return filter_events(self._events, **filters)

    def __len__(self) -> int:
        return len(self._events)


# Module-level Postgres pool for the backend. Lazy-init pattern mirrors
# ``metric_collector._get_pg_pool`` so two modules share connection-
# count budget within one process.
_PG_POOL: Any = None  # None=uninit, False=tried+unavailable, else ConnectionPool


def _get_pg_pool() -> Any:
    """Lazy-init a module-level Postgres ConnectionPool for episodic
    events. Returns None when DATABASE_URL is unset or pool creation
    failed — backend falls back to no-op so missing-Postgres envs
    (tests, dev, SharePoint-only legacy) still work.
    """
    global _PG_POOL
    if _PG_POOL is False:
        return None
    if _PG_POOL is not None:
        return _PG_POOL
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        _PG_POOL = False
        return None
    try:
        from psycopg_pool import ConnectionPool

        _PG_POOL = ConnectionPool(
            db_url,
            min_size=1,
            max_size=4,
            open=True,
        )
        return _PG_POOL
    except Exception as exc:  # noqa: BLE001 — defensive on dep-missing
        logger.debug("[episodic_memory] pool init failed: %s", exc)
        _PG_POOL = False
        return None


class PostgresBackend:
    """Production backend for episodic events.

    Persists to the ``episodic_events`` table (migration
    ``y5t6u7v8w9x0``). Satisfies the ``EpisodicBackend`` Protocol via
    duck typing.

    Fail-quiet contract: if Postgres is unavailable (no DATABASE_URL,
    pool init failed, query failed mid-call), ``record`` silently
    no-ops and ``query`` returns ``[]``. The agent's emit calls never
    block on storage hiccups.

    Thread-safe via the shared module-level ConnectionPool. Caller
    instantiation is cheap — no per-instance state beyond the
    optional ``niche_id`` for RLS tenant context.
    """

    def __init__(self, niche_id: str = "") -> None:
        # niche_id reserved for future RLS scope; harmless when unset
        # because filter_events still receives full DSN-level view
        self._niche_id = niche_id

    def record(self, event: EpisodicEvent) -> None:
        """Insert one event. Fail-quiet on any storage error."""
        pool = _get_pg_pool()
        if pool is None:
            return
        try:
            import json as _json

            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO episodic_events
                            (event_type, niche_id, blueprint_id,
                             "timestamp", payload)
                        VALUES (%s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            event.event_type,
                            event.niche_id,
                            event.blueprint_id or None,
                            event.timestamp,
                            _json.dumps(event.payload or {}),
                        ),
                    )
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.debug("[episodic_memory] record failed: %s", exc)

    def query(self, **filters: Any) -> list[EpisodicEvent]:
        """Query events from Postgres + apply filter_events semantics.

        Hot path: dashboards reading "last N events for niche X". Pulls
        a reasonable upper bound from Postgres, then delegates to
        ``filter_events`` for the in-process filter logic — keeps the
        SQL simple + reuses the well-tested pure-function filter.

        Returns ``[]`` on any storage failure.
        """
        pool = _get_pg_pool()
        if pool is None:
            return []
        try:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    # Pull recent rows. Niche filter pushed to SQL when
                    # provided; date filters intentionally NOT pushed
                    # so filter_events stays the single source of
                    # filter truth (no SQL/Python skew).
                    niche_id = filters.get("niche_id")
                    if niche_id is not None:
                        cur.execute(
                            """
                            SELECT event_type, niche_id, blueprint_id,
                                   "timestamp", payload
                            FROM episodic_events
                            WHERE niche_id = %s
                            ORDER BY "timestamp" DESC
                            LIMIT 5000
                            """,
                            (niche_id,),
                        )
                    else:
                        cur.execute(
                            """
                            SELECT event_type, niche_id, blueprint_id,
                                   "timestamp", payload
                            FROM episodic_events
                            ORDER BY "timestamp" DESC
                            LIMIT 5000
                            """
                        )
                    rows = cur.fetchall()

            events = [
                EpisodicEvent(
                    event_type=str(r[0]),
                    niche_id=str(r[1]),
                    blueprint_id=str(r[2] or ""),
                    timestamp=(r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3])),
                    payload=dict(r[4]) if isinstance(r[4], dict) else {},
                )
                for r in rows
            ]
            # Apply the rest of the filters (event_type / since / until /
            # limit) via the pure-function filter for consistency with
            # InMemoryBackend.
            return filter_events(events, **filters)
        except Exception as exc:  # noqa: BLE001 — fail-open
            logger.debug("[episodic_memory] query failed: %s", exc)
            return []


# ─── Module-level emit helper (callable from any code path) ──────────
#
# Pattern: emit sites do ``record_event(event_type=..., niche_id=...,
# blueprint_id=..., payload=...)`` and don't think about backend
# construction. The helper lazy-instantiates a module-level
# PostgresBackend on first call + reuses it for the process lifetime.
#
# Fail-quiet contract preserved: any storage / pool / network error
# is absorbed by PostgresBackend.record itself. The helper adds an
# extra catch around the helper-side logic (event construction, etc.)
# so a caller-side bug NEVER blocks the agent's main work.
#
# When DATABASE_URL is unset (local dev, tests without Postgres), the
# backend's pool init fails fast + record() no-ops. No env flag needed
# — the DATABASE_URL presence IS the natural on/off switch.

_DEFAULT_BACKEND: Any = None


def _get_default_backend() -> Any:
    """Return the lazy-init module-level PostgresBackend, or None.

    Returns None only if PostgresBackend import itself fails. Otherwise
    a backend instance — even one whose pool init will fail at first
    use (DATABASE_URL unset) — because that backend handles its own
    no-op contract.
    """
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        try:
            _DEFAULT_BACKEND = PostgresBackend()
        except Exception:  # noqa: BLE001 — never block on init
            return None
    return _DEFAULT_BACKEND


def record_event(
    *,
    event_type: str,
    niche_id: str,
    blueprint_id: str = "",
    payload: dict[str, Any] | None = None,
    when: datetime | None = None,
) -> None:
    """Module-level emit helper. Fire-and-forget; never blocks caller.

    Builds an EpisodicEvent + records via the shared default backend.
    Any exception (event construction, backend lookup, storage error)
    is caught and logged at debug. Returns None always.

    Typical usage::

        from genlab_core.learning.episodic_memory import (
            EVENT_POST_BOMBED, record_event,
        )
        record_event(
            event_type=EVENT_POST_BOMBED,
            niche_id=niche_id,
            blueprint_id=blueprint_id,
            payload={"reward_48h": reward, "threshold": threshold},
        )

    No env flag — when DATABASE_URL is unset, PostgresBackend silently
    no-ops, so this helper degrades correctly for local dev / tests.
    """
    try:
        backend = _get_default_backend()
        if backend is None:
            return
        event = new_event(
            event_type=event_type,
            niche_id=niche_id,
            blueprint_id=blueprint_id,
            payload=payload,
            when=when,
        )
        backend.record(event)
    except Exception as exc:  # noqa: BLE001 — fire-and-forget
        logger.debug("[episodic_memory] record_event helper error: %s", exc)


def _reset_default_backend_for_tests() -> None:
    """Test hook: clear the cached default backend so monkeypatched
    PostgresBackend mocks take effect."""
    global _DEFAULT_BACKEND
    _DEFAULT_BACKEND = None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Episodic memory primitive (Lever R1 MVP)")
    parser.add_argument(
        "--demo", action="store_true", help="Print a small demo of the in-memory backend"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.demo:
        backend = InMemoryBackend()
        backend.record(new_event(event_type=EVENT_PUBLISH, niche_id="gaming", blueprint_id="bp_1"))
        backend.record(new_event(event_type=EVENT_PUBLISH, niche_id="sports", blueprint_id="bp_2"))
        backend.record(
            new_event(
                event_type=EVENT_OPERATOR_REVIEW,
                niche_id="gaming",
                blueprint_id="bp_1",
                payload={"action": "approved"},
            )
        )
        print(f"Recorded {len(backend)} events.")
        print("Summary of gaming events:")
        gaming = backend.query(niche_id="gaming")
        import json

        print(json.dumps(summarize_events(gaming), indent=2))
    else:
        print("Use --demo to see a sample run.")
        print("Programmatic usage:")
        print(
            "  from genlab_core.learning.episodic_memory import ("
            "EpisodicEvent, InMemoryBackend, new_event, filter_events, summarize_events)"
        )
