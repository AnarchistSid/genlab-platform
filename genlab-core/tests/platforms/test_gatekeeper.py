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


class TestScoreFloorGate:
    def test_above_floor_passes(self, gatekeeper):
        bp = {"priority_score": 0.8}
        result = gatekeeper._score_floor_gate(bp, "instagram")
        assert result.allowed is True

    def test_below_floor_blocks(self, gatekeeper):
        bp = {"priority_score": 0.1}
        result = gatekeeper._score_floor_gate(bp, "instagram")
        assert result.allowed is False


class TestScheduleGate:
    def test_due_now_passes(self, gatekeeper):
        bp = {"scheduled_for": (datetime.now(UTC) - timedelta(minutes=5)).isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        assert result.allowed is True

    def test_future_blocks(self, gatekeeper):
        bp = {"scheduled_for": (datetime.now(UTC) + timedelta(hours=5)).isoformat()}
        result = gatekeeper._schedule_gate(bp, "instagram")
        assert result.allowed is False


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
