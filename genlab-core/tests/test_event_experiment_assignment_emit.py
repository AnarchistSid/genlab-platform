"""Pin: _classify_arm emits EVENT_EXPERIMENT_ASSIGNMENT at the
moment ``assign_to_experiment`` returns a selection.

Context: PR #437 shipped the experiment registry; PR #438-ish wired
``_classify_arm`` to consult it; PR #441 shipped ``record_event``;
later PRs emitted EVENT_BANDIT_PICK, EVENT_PUBLISH, EVENT_OPERATOR_*,
EVENT_REWARD_WINDOW_CLOSED, EVENT_POST_BOMBED — leaving
EVENT_EXPERIMENT_ASSIGNMENT as the LAST dormant event type in the
8-event taxonomy. This PR closes that gap.

Why a separate emit when EVENT_BANDIT_PICK already fires post-
classification: BANDIT_PICK fires AFTER every classification call
(including legacy/fallthrough paths) and carries
``experiment_active: bool`` in its payload. That tells you
"experiment was registered" but NOT "the experiment branch won the
selection" — those are different facts. EXPERIMENT_ASSIGNMENT fires
ONLY when the experiment's deterministic assignment short-circuits
the bandit chain. The two together let a later query distinguish
"experiment registered, bandit happened to pick the same arm" from
"experiment forced the assignment."

These pins lock in:

  Happy path (3):
  1. env on + active experiment + assignment_id → record_event called
     once with EVENT_EXPERIMENT_ASSIGNMENT
  2. payload carries (experiment_id, arm_id, assignment_id,
     arms_in_experiment)
  3. assigned arm_id is still returned (emit doesn't change behavior)

  Fall-through paths (3):
  4. env unset → no emit (the experiment branch was never taken)
  5. active_experiment=None → no emit
  6. expired experiment → no emit (assign_to_experiment returns None
     before the emit site)

  Fail-OPEN (1):
  7. record_event raising mid-call → arm_id still returned + logged
     at debug (NEVER blocks classification)

  Source-level (1):
  8. Wire structurally present (defends against accidental delete)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from genlab_core.learning.experimentation import ABTest
from genlab_core.pipeline.stages.push_to_backlog import _classify_arm

_MULTI_MATCH_STORY = {"title": "Patch notes", "summary": "Season 2 launch"}
_MULTI_MATCH_CONTENT = {"hook": "Big patch dropping"}


def _open_experiment(arms=("patch_news", "trailer_reaction")) -> ABTest:
    """Always-active 50/50 experiment over two gaming arms."""
    return ABTest(
        experiment_id="patch_vs_trailer_2026_07",
        arms=list(arms),
        allocation={a: 1.0 / len(arms) for a in arms},
        start_date="2020-01-01T00:00:00+00:00",
        end_date=None,
    )


def _expired_experiment() -> ABTest:
    return ABTest(
        experiment_id="expired_test",
        arms=["a", "b"],
        allocation={"a": 0.5, "b": 0.5},
        start_date="2020-01-01T00:00:00+00:00",
        end_date="2020-12-31T00:00:00+00:00",
    )


class TestHappyPath:
    def test_emit_fires_once_when_assignment_succeeds(self, monkeypatch):
        """Pin: env on + active experiment + assignment_id →
        record_event called exactly once with EVENT_EXPERIMENT_ASSIGNMENT.
        This is the primary smoke pin — without it, the wire is dead."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")
        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        with patch(
            "genlab_core.learning.episodic_memory.record_event",
            side_effect=fake_record_event,
        ):
            _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                active_experiment=_open_experiment(),
                experiment_assignment_id="bp_emit_001",
            )

        assignment_events = [
            e for e in emitted if e.get("event_type") == "experiment_assignment"
        ]
        assert len(assignment_events) == 1

    def test_payload_carries_full_context(self, monkeypatch):
        """Pin: payload includes experiment_id (which test ran), arm_id
        (what was assigned), assignment_id (which blueprint), and
        arms_in_experiment (denominator for downstream query)."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")
        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        with patch(
            "genlab_core.learning.episodic_memory.record_event",
            side_effect=fake_record_event,
        ):
            _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5},
                active_experiment=_open_experiment(),
                experiment_assignment_id="bp_payload_test",
            )

        assignment_events = [
            e for e in emitted if e.get("event_type") == "experiment_assignment"
        ]
        assert assignment_events, "no EVENT_EXPERIMENT_ASSIGNMENT emitted"
        event = assignment_events[0]
        assert event["niche_id"] == "gaming"
        assert event["blueprint_id"] == "bp_payload_test"
        payload = event["payload"]
        assert payload["experiment_id"] == "patch_vs_trailer_2026_07"
        assert payload["arm_id"] in {"patch_news", "trailer_reaction"}
        assert payload["assignment_id"] == "bp_payload_test"
        assert payload["arms_in_experiment"] == 2

    def test_returns_assigned_arm_unchanged(self, monkeypatch):
        """Pin: the emit doesn't change classification behavior. Same
        assignment_id deterministically returns same arm with or without
        the emit wire in place."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")
        # No patching of record_event — let it real-call into the
        # default backend (which no-ops without DATABASE_URL).
        results = [
            _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                active_experiment=_open_experiment(),
                experiment_assignment_id="bp_unchanged_001",
            )
            for _ in range(10)
        ]
        # Deterministic: 10 calls → 1 unique result
        assert len(set(results)) == 1
        assert results[0] in {"patch_news", "trailer_reaction"}


class TestFallThroughNoEmit:
    def test_env_unset_no_emit(self, monkeypatch):
        """Pin: env unset → experiment branch is skipped entirely →
        emit never fires. Zero cost when experimentation is disabled."""
        monkeypatch.delenv("GENLAB_EXPERIMENTATION_ENABLED", raising=False)
        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        with patch(
            "genlab_core.learning.episodic_memory.record_event",
            side_effect=fake_record_event,
        ):
            _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                active_experiment=_open_experiment(),
                experiment_assignment_id="bp_disabled_001",
            )

        assert [
            e for e in emitted if e.get("event_type") == "experiment_assignment"
        ] == []

    def test_no_active_experiment_no_emit(self, monkeypatch):
        """Pin: active_experiment=None → the entire experiment branch
        is bypassed by the outer ``if active_experiment is not None``
        guard → emit never fires."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")
        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        with patch(
            "genlab_core.learning.episodic_memory.record_event",
            side_effect=fake_record_event,
        ):
            _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                active_experiment=None,
                experiment_assignment_id="bp_no_exp_001",
            )

        assert [
            e for e in emitted if e.get("event_type") == "experiment_assignment"
        ] == []

    def test_expired_experiment_no_emit(self, monkeypatch):
        """Pin: assign_to_experiment returns None (expired window) →
        the ``if sel is not None`` guard skips the emit. Emit fires
        ONLY when assignment actually succeeded."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")
        emitted = []

        def fake_record_event(**kwargs):
            emitted.append(kwargs)

        with patch(
            "genlab_core.learning.episodic_memory.record_event",
            side_effect=fake_record_event,
        ):
            _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                active_experiment=_expired_experiment(),
                experiment_assignment_id="bp_expired_001",
            )

        assert [
            e for e in emitted if e.get("event_type") == "experiment_assignment"
        ] == []


class TestFailOpen:
    def test_record_event_exception_does_not_break_classification(
        self, monkeypatch
    ):
        """Pin: record_event raising mid-call → classification still
        returns the assigned arm_id. The episodic emit MUST NEVER
        block the pipeline (the canonical 'episodic emit must never
        block X' invariant)."""
        monkeypatch.setenv("GENLAB_EXPERIMENTATION_ENABLED", "1")

        with patch(
            "genlab_core.learning.episodic_memory.record_event",
            side_effect=RuntimeError("simulated postgres outage"),
        ):
            arm_id = _classify_arm(
                "gaming",
                _MULTI_MATCH_STORY,
                _MULTI_MATCH_CONTENT,
                arm_boosts={"patch_news": 1.5, "trailer_reaction": 0.8},
                active_experiment=_open_experiment(),
                experiment_assignment_id="bp_failopen_001",
            )

        # Despite the simulated outage in the emit path, the assigned
        # arm came through — the wire is fail-OPEN.
        assert arm_id in {"patch_news", "trailer_reaction"}


class TestSourceWire:
    """Source-level pin: the emit wire is structurally present.
    Defends against accidental delete in a future refactor that
    happens to keep the runtime tests green via the silent-no-op
    fail-OPEN path."""

    def test_wire_imports_and_event_constant_present_in_source(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "pipeline"
            / "stages"
            / "push_to_backlog.py"
        ).read_text()
        assert "EVENT_EXPERIMENT_ASSIGNMENT" in src
        # Emit is co-located with the experiment-branch return
        assert "experiment_id" in src
        assert "arms_in_experiment" in src
        # Fail-OPEN canonical phrase
        assert (
            "episodic emit must never block classification" in src
        )
