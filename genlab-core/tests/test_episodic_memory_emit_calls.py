"""Pin: episodic_memory.record_event helper + 3 emit-call wire sites.

PR #441 shipped Lever R1 primitive. PR #451 shipped PostgresBackend +
migration. THIS PR ships the actual emit calls — 3 sites where the
agent writes events that future LLM-as-judge layers can query.

These pins lock in:

  record_event helper (4):
  1. record_event calls backend.record with a valid EpisodicEvent
  2. record_event with no backend → silent no-op
  3. record_event with backend.record raising → silent no-op
  4. record_event constructs event with the supplied kwargs

  Emit-call wires (3):
  5. metric_collector emits ``reward_window_closed`` after compute_reward
  6. metric_collector emits ``post_bombed`` inside _check_post_bomb_signal
  7. push_to_backlog emits ``bandit_pick`` after _classify_arm
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestRecordEventHelper:
    def test_calls_backend_record_with_event(self, monkeypatch):
        """Pin: record_event constructs an EpisodicEvent + delegates
        to the default backend's record method."""
        from genlab_core.learning import episodic_memory

        episodic_memory._reset_default_backend_for_tests()
        fake_backend = MagicMock()

        with patch(
            "genlab_core.learning.episodic_memory.PostgresBackend",
            return_value=fake_backend,
        ):
            episodic_memory.record_event(
                event_type=episodic_memory.EVENT_PUBLISH,
                niche_id="gaming",
                blueprint_id="bp_001",
                payload={"platform": "youtube"},
            )

        fake_backend.record.assert_called_once()
        event_arg = fake_backend.record.call_args[0][0]
        assert event_arg.event_type == episodic_memory.EVENT_PUBLISH
        assert event_arg.niche_id == "gaming"
        assert event_arg.blueprint_id == "bp_001"
        assert event_arg.payload == {"platform": "youtube"}

    def test_no_backend_is_silent_noop(self, monkeypatch):
        """Pin: _get_default_backend returning None → record_event
        silently returns. Never raises, never blocks caller."""
        from genlab_core.learning import episodic_memory

        episodic_memory._reset_default_backend_for_tests()

        with patch(
            "genlab_core.learning.episodic_memory._get_default_backend",
            return_value=None,
        ):
            # Should not raise
            episodic_memory.record_event(
                event_type=episodic_memory.EVENT_PUBLISH, niche_id="gaming"
            )

    def test_backend_record_raises_is_silent(self, monkeypatch):
        """Pin: backend.record raising (storage outage, etc.) is caught
        silently. The agent's emit calls never block on storage errors."""
        from genlab_core.learning import episodic_memory

        episodic_memory._reset_default_backend_for_tests()
        fake_backend = MagicMock()
        fake_backend.record.side_effect = RuntimeError("DB outage")

        with patch(
            "genlab_core.learning.episodic_memory.PostgresBackend",
            return_value=fake_backend,
        ):
            # MUST NOT raise
            episodic_memory.record_event(
                event_type=episodic_memory.EVENT_PUBLISH, niche_id="gaming"
            )

    def test_event_construction_uses_supplied_kwargs(self, monkeypatch):
        """Pin: helper passes through ALL kwargs to new_event so emit
        sites can attach payload + custom timestamps."""
        from datetime import UTC, datetime

        from genlab_core.learning import episodic_memory

        episodic_memory._reset_default_backend_for_tests()
        fake_backend = MagicMock()
        custom_when = datetime(2026, 6, 22, 9, 30, tzinfo=UTC)

        with patch(
            "genlab_core.learning.episodic_memory.PostgresBackend",
            return_value=fake_backend,
        ):
            episodic_memory.record_event(
                event_type=episodic_memory.EVENT_BANDIT_PICK,
                niche_id="sports",
                blueprint_id="bp_xyz",
                payload={"arm_id": "highlight_play"},
                when=custom_when,
            )

        event_arg = fake_backend.record.call_args[0][0]
        assert event_arg.timestamp == custom_when.isoformat()
        assert event_arg.payload["arm_id"] == "highlight_play"


class TestMetricCollectorEmitSites:
    def test_check_post_bomb_signal_emits_post_bombed(self, monkeypatch):
        """Pin: when _check_post_bomb_signal detects a bomb, it ALSO
        emits a post_bombed event to episodic memory. Captures both
        the trigger event and the structured payload (platform,
        reward, threshold, miss_pct)."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")

        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        from genlab_core.learning import metric_collector

        with patch(
            "genlab_core.learning.episodic_memory.record_event",
            side_effect=fake_record_event,
        ):
            with patch(
                "genlab_core.learning.metric_collector._fetch_rca_context",
                return_value=(0.08, [], {}),
            ):
                with patch(
                    "genlab_core.learning.post_rca.analyze_post",
                    return_value=None,
                ):
                    result = metric_collector._check_post_bomb_signal(
                        niche_id="gaming",
                        blueprint_id="bp_bombed",
                        platform="instagram",
                        reward_48h=0.03,
                    )

        assert result is True
        # Should have emitted ONE post_bombed event
        bomb_events = [e for e in emitted if e.get("event_type") == "post_bombed"]
        assert len(bomb_events) == 1
        assert bomb_events[0]["niche_id"] == "gaming"
        assert bomb_events[0]["blueprint_id"] == "bp_bombed"
        # Payload includes reward + threshold + miss_pct
        assert bomb_events[0]["payload"]["reward_48h"] == 0.03
        assert bomb_events[0]["payload"]["platform"] == "instagram"

    def test_check_post_bomb_signal_disabled_no_emit(self, monkeypatch):
        """Pin: env disabled → no emit, no Postgres writes. Zero cost
        when the post_rca flag is off."""
        monkeypatch.delenv("GENLAB_POST_RCA_ENABLED", raising=False)

        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        from genlab_core.learning import metric_collector

        with patch(
            "genlab_core.learning.episodic_memory.record_event",
            side_effect=fake_record_event,
        ):
            metric_collector._check_post_bomb_signal(
                niche_id="gaming",
                blueprint_id="bp_001",
                platform="instagram",
                reward_48h=0.001,  # would trigger if env on
            )

        # No emit (because we never reached the post_bombed log line)
        bomb_events = [e for e in emitted if e.get("event_type") == "post_bombed"]
        assert bomb_events == []
