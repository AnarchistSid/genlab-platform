"""Tests for PublishGatekeeper — each gate tested in isolation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from genlab_core.platforms.gatekeeper import PublishGatekeeper


@pytest.fixture
def gatekeeper():
    config = {
        "platforms": {"min_publish_gap_hours": 2},
        "POLICY": {"strict_creator_video_only": False},
    }
    daily_cap = MagicMock()
    daily_cap.can_publish.return_value = True
    backlog = MagicMock()
    return PublishGatekeeper(config=config, daily_cap=daily_cap, backlog=backlog)


class TestApprovalGate:
    def test_approved_passes(self, gatekeeper):
        bp = {"action_taken": "approved"}
        result = gatekeeper._approval_gate(bp, "instagram")
        assert result.allowed is True

    def test_not_approved_blocks(self, gatekeeper, monkeypatch):
        monkeypatch.delenv("SKIP_APPROVAL_GATE", raising=False)
        bp = {"action_taken": ""}
        result = gatekeeper._approval_gate(bp, "instagram")
        assert result.allowed is False
        assert result.gate_name == "approval_gate"

    def test_critical_urgency_no_longer_bypasses_approval(self, gatekeeper):
        """R-08 regression pin — express-lane CRITICAL/HIGH urgency
        previously bypassed the approval gate, letting a crafted RSS
        title with the right buzzwords auto-publish to all 5 channels.
        That bypass is removed; approval is now the sole pass-through."""
        bp = {
            "action_taken": "",
            "urgency_classification": {"urgency": "CRITICAL", "score": 0.95},
        }
        result = gatekeeper._approval_gate(bp, "instagram")
        assert result.allowed is False, "CRITICAL urgency must not bypass approval"
        assert result.reason == "Not approved"

    def test_high_urgency_no_longer_bypasses_approval(self, gatekeeper):
        """R-08 regression pin (HIGH variant)."""
        bp = {
            "action_taken": "",
            "urgency_classification": {"urgency": "HIGH", "score": 0.7},
        }
        result = gatekeeper._approval_gate(bp, "instagram")
        assert result.allowed is False

    def test_urgency_as_json_string_no_longer_bypasses(self, gatekeeper):
        """The legacy bypass parsed ``urgency_classification`` as a
        JSON string before checking the level. Verify that path is
        gone too — approval still required."""
        bp = {
            "action_taken": "",
            "urgency_classification": '{"urgency": "CRITICAL", "score": 0.9}',
        }
        result = gatekeeper._approval_gate(bp, "instagram")
        assert result.allowed is False


class TestScoreFloorGate:
    def test_above_floor_passes(self, gatekeeper):
        bp = {"priority_score": 0.8}
        result = gatekeeper._score_floor_gate(bp, "instagram")
        assert result.allowed is True

    def test_below_floor_blocks(self, gatekeeper):
        bp = {"priority_score": 0.1}
        result = gatekeeper._score_floor_gate(bp, "instagram")
        assert result.allowed is False

    def test_qc_penalty_zero_score_does_not_resurrect_to_default(self, gatekeeper):
        """Regression pin (PR follow-up to #588 engagement_rate audit):
        ``qc_gates.py:163`` legitimately writes ``priority_score = 0``
        when a blueprint fails QC and its starting score equals the
        ``SCORE_PENALTY`` (0.3). The historical
        ``float(bp.get("priority_score", 0.5) or 0.5)`` would silently
        resurrect that explicit 0 back to 0.5 — vaulting a QC-degraded
        blueprint over the 0.3 floor.

        Pinned: an explicit ``priority_score=0`` (or 0.0) must REJECT,
        not be resurrected."""
        bp_int_zero = {"priority_score": 0}
        result_int = gatekeeper._score_floor_gate(bp_int_zero, "instagram")
        assert result_int.allowed is False, (
            "explicit priority_score=0 (e.g. after qc_gates SCORE_PENALTY) "
            "must NOT be resurrected to 0.5"
        )

        bp_float_zero = {"priority_score": 0.0}
        result_float = gatekeeper._score_floor_gate(bp_float_zero, "instagram")
        assert result_float.allowed is False

    def test_missing_priority_score_uses_default_passes(self, gatekeeper):
        """Truly missing (no key in dict) still falls back to the 0.5
        neutral default and passes the 0.3 floor — preserves the
        cold-start behavior for blueprints written before priority_score
        was populated."""
        result = gatekeeper._score_floor_gate({}, "instagram")
        assert result.allowed is True

    def test_none_priority_score_uses_default(self, gatekeeper):
        """An explicit None (e.g. SQL NULL deserialized) is also
        treated as 'unknown' and falls back to 0.5 — but a numeric 0
        is preserved (see test above). This keeps the
        missing-vs-explicit-zero distinction clean."""
        bp = {"priority_score": None}
        result = gatekeeper._score_floor_gate(bp, "instagram")
        assert result.allowed is True

    def test_unparseable_priority_score_uses_default(self, gatekeeper):
        """A non-numeric value (string that can't parse) falls back to
        the 0.5 neutral default. Without this guard, the gate would
        crash and propagate up through ``evaluate``."""
        bp = {"priority_score": "n/a"}
        result = gatekeeper._score_floor_gate(bp, "instagram")
        assert result.allowed is True


class TestScheduleGate:
    def test_due_now_passes(self, gatekeeper):
        bp = {"scheduled_for": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        assert result.allowed is True

    def test_future_blocks(self, gatekeeper):
        bp = {"scheduled_for": (datetime.now(UTC) + timedelta(hours=5)).isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        assert result.allowed is False

    def test_stale_past_day_blocks(self, gatekeeper):
        """R-82: a >18h-stale slot (a prior day's cycle) must NOT stay eligible."""
        bp = {"scheduled_for": (datetime.now(UTC) - timedelta(hours=30)).isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        assert result.allowed is False
        assert "Stale" in result.reason

    def test_same_day_late_run_still_passes(self, gatekeeper):
        """A late/retry run hours after the slot (within the grace) still publishes."""
        bp = {"scheduled_for": (datetime.now(UTC) - timedelta(hours=4)).isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        assert result.allowed is True

    def test_naive_scheduled_for_is_assumed_utc(self, gatekeeper):
        """R-82: a naive timestamp is coerced to UTC (single authority), not IST."""
        naive = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None)
        bp = {"scheduled_for": naive.isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        # Read as UTC → 5 min past → due. (Read as IST it'd be ~5.5h future → blocked.)
        assert result.allowed is True


class TestDailyCapGate:
    def test_under_cap_passes(self, gatekeeper):
        bp = {}
        result = gatekeeper._daily_cap_gate(bp, "instagram")
        assert result.allowed is True

    def test_over_cap_blocks(self, gatekeeper):
        gatekeeper._daily_cap.can_publish.return_value = False
        bp = {}
        result = gatekeeper._daily_cap_gate(bp, "instagram")
        assert result.allowed is False


class TestEvaluateChain:
    def test_all_gates_pass(self, gatekeeper):
        bp = {
            "action_taken": "approved",
            "format": "reel",
            "scheduled_for": datetime.now(UTC).isoformat(),
            "priority_score": 0.8,
            "visual_paths": '["/tmp/video.mp4"]',
        }
        result = gatekeeper.evaluate(bp, "instagram")
        assert result.allowed is True
        assert result.gate_name == "all"

    def test_first_failure_wins(self, gatekeeper, monkeypatch):
        monkeypatch.delenv("SKIP_APPROVAL_GATE", raising=False)
        bp = {"action_taken": ""}  # Fails approval
        result = gatekeeper.evaluate(bp, "instagram")
        assert result.allowed is False
        assert result.gate_name == "approval_gate"
