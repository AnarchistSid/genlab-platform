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

    NOT for production. Concrete production backend (Postgres-backed)
    is a follow-up PR with the ``episodic_events`` table migration.

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
