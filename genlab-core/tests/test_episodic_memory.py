"""Pin: episodic memory primitive (Lever R1 MVP).

Today the agent has no queryable "memory" of what worked. Lever R1
ships the typed event model + pure-function query/aggregation
helpers + a test-only InMemoryBackend implementing the
EpisodicBackend Protocol. Production Postgres backend is a follow-up
PR with the migration.

These pins lock in:

  coerce_event_type (3):
  1. Known event types pass through
  2. None / empty → "unknown"
  3. Free-text "made_up_value" → "unknown" (whitelist enforcement)

  new_event (3):
  4. Default timestamp set to now(UTC) when when=None
  5. Custom when= datetime preserved (ISO format)
  6. None payload defaults to empty dict (not None)

  filter_events (5):
  7. No filters → all events returned in input order
  8. niche_id filter exact match
  9. event_type filter exact match (coerced)
  10. since/until window filter (inclusive)
  11. limit returns most-recent N (sorted DESC by timestamp)

  summarize_events (2):
  12. Empty list → zero-shape summary
  13. Aggregations correct (by_event_type, by_niche, unique_blueprint_ids,
      first/last timestamp)

  InMemoryBackend Protocol compliance (2):
  14. Backend satisfies EpisodicBackend Protocol via isinstance check
  15. record() + query() round-trip preserves event order
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from genlab_core.learning.episodic_memory import (
    EVENT_BANDIT_PICK,
    EVENT_OPERATOR_REVIEW,
    EVENT_POST_BOMBED,
    EVENT_PUBLISH,
    EVENT_UNKNOWN,
    EpisodicBackend,
    InMemoryBackend,
    coerce_event_type,
    filter_events,
    new_event,
    summarize_events,
)


class TestCoerceEventType:
    def test_known_types_pass_through(self):
        for known in (EVENT_PUBLISH, EVENT_OPERATOR_REVIEW, EVENT_BANDIT_PICK):
            assert coerce_event_type(known) == known

    def test_none_and_empty_coerce_to_unknown(self):
        assert coerce_event_type(None) == EVENT_UNKNOWN
        assert coerce_event_type("") == EVENT_UNKNOWN
        assert coerce_event_type("   ") == EVENT_UNKNOWN

    def test_free_text_coerces_to_unknown(self):
        """Pin: whitelist enforcement keeps downstream aggregations
        clean against future code that emits new event types without
        updating the closed set."""
        assert coerce_event_type("made_up_event") == EVENT_UNKNOWN
        assert coerce_event_type("WeirdCaseType") == EVENT_UNKNOWN


class TestNewEvent:
    def test_default_timestamp_set_to_now(self):
        before = datetime.now(UTC) - timedelta(seconds=1)
        e = new_event(event_type=EVENT_PUBLISH, niche_id="gaming")
        after = datetime.now(UTC) + timedelta(seconds=1)
        # Parse the event's timestamp + check it's in [before, after]
        e_dt = datetime.fromisoformat(e.timestamp)
        assert before <= e_dt <= after

    def test_custom_when_preserved(self):
        custom = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        e = new_event(event_type=EVENT_PUBLISH, niche_id="gaming", when=custom)
        assert e.timestamp == custom.isoformat()

    def test_none_payload_becomes_empty_dict(self):
        """Pin: payload defaults to {}, NOT None. Downstream consumers
        can rely on dict shape without None checks."""
        e = new_event(event_type=EVENT_PUBLISH, niche_id="gaming", payload=None)
        assert e.payload == {}


def _sample_events() -> list:
    """5 events across 2 niches + 3 event types + spread over 3 days."""
    base = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    return [
        new_event(
            event_type=EVENT_PUBLISH,
            niche_id="gaming",
            blueprint_id="bp_1",
            when=base - timedelta(days=2),
        ),
        new_event(
            event_type=EVENT_PUBLISH,
            niche_id="sports",
            blueprint_id="bp_2",
            when=base - timedelta(days=1),
        ),
        new_event(
            event_type=EVENT_OPERATOR_REVIEW,
            niche_id="gaming",
            blueprint_id="bp_1",
            when=base,
            payload={"action": "approved"},
        ),
        new_event(
            event_type=EVENT_POST_BOMBED,
            niche_id="gaming",
            blueprint_id="bp_1",
            when=base + timedelta(hours=1),
        ),
        new_event(
            event_type=EVENT_PUBLISH,
            niche_id="gaming",
            blueprint_id="bp_3",
            when=base + timedelta(days=1),
        ),
    ]


class TestFilterEvents:
    def test_no_filters_returns_all_in_input_order(self):
        events = _sample_events()
        result = filter_events(events)
        assert len(result) == 5
        # Input order preserved (no limit → no sort)
        assert [e.blueprint_id for e in result] == ["bp_1", "bp_2", "bp_1", "bp_1", "bp_3"]

    def test_niche_id_filter_exact_match(self):
        events = _sample_events()
        gaming_only = filter_events(events, niche_id="gaming")
        # 4 gaming events (bp_1×3 + bp_3)
        assert len(gaming_only) == 4
        assert all(e.niche_id == "gaming" for e in gaming_only)

    def test_event_type_filter_exact_match(self):
        events = _sample_events()
        publishes = filter_events(events, event_type=EVENT_PUBLISH)
        # 3 publish events
        assert len(publishes) == 3
        assert all(e.event_type == EVENT_PUBLISH for e in publishes)

    def test_since_until_window(self):
        events = _sample_events()
        base = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
        # Window: just the events on day 15 + 16 (inclusive)
        window = filter_events(events, since=base, until=base + timedelta(days=1))
        # Should include the operator_review, post_bombed, and last publish
        assert len(window) == 3

    def test_limit_returns_most_recent_n(self):
        """Pin: limit sorts newest-first then trims so callers get the
        most recent N — not the first N in input order."""
        events = _sample_events()
        # 5 events sorted by timestamp ASC → newest is the bp_3 publish
        most_recent = filter_events(events, limit=2)
        assert len(most_recent) == 2
        # Newest first: bp_3 publish (day +1) then post_bombed (day 0 +1h)
        assert most_recent[0].blueprint_id == "bp_3"
        assert most_recent[1].event_type == EVENT_POST_BOMBED


class TestSummarizeEvents:
    def test_empty_returns_zero_shape(self):
        result = summarize_events([])
        assert result == {
            "total_events": 0,
            "by_event_type": {},
            "by_niche": {},
            "first_timestamp": None,
            "last_timestamp": None,
            "unique_blueprint_ids": 0,
        }

    def test_aggregations_correct(self):
        events = _sample_events()
        s = summarize_events(events)
        assert s["total_events"] == 5
        assert s["by_event_type"] == {
            EVENT_PUBLISH: 3,
            EVENT_OPERATOR_REVIEW: 1,
            EVENT_POST_BOMBED: 1,
        }
        assert s["by_niche"] == {"gaming": 4, "sports": 1}
        # Unique blueprint_ids: bp_1, bp_2, bp_3
        assert s["unique_blueprint_ids"] == 3
        # First + last timestamps from the spread
        assert s["first_timestamp"] < s["last_timestamp"]


class TestInMemoryBackend:
    def test_satisfies_protocol_via_isinstance(self):
        """Pin: ``isinstance(backend, EpisodicBackend)`` is True. The
        Protocol's runtime_checkable decorator means future backends
        (PostgresBackend, JsonFileBackend) just need to implement the
        record + query methods — no explicit subclass declaration."""
        backend = InMemoryBackend()
        assert isinstance(backend, EpisodicBackend)

    def test_record_and_query_round_trip(self):
        backend = InMemoryBackend()
        events = _sample_events()
        for e in events:
            backend.record(e)
        assert len(backend) == 5

        # query with niche filter
        gaming = backend.query(niche_id="gaming")
        assert len(gaming) == 4
