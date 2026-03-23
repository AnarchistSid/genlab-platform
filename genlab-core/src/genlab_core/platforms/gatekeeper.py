"""Publishing gatekeeping — composable gates that run on raw blueprint dicts."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


@dataclass
class GateResult:
    allowed: bool
    reason: str
    gate_name: str


class PublishGatekeeper:
    """Evaluates a pipeline of gates against a blueprint dict.

    Gates operate on raw blueprint dicts (NOT PublishPayload) because
    gatekeeper runs BEFORE payload construction.
    """

    def __init__(self, config: dict = None, daily_cap=None, backlog=None):
        self._config = config or {}
        self._daily_cap = daily_cap
        self._backlog = backlog
        self._gates = [
            self._approval_gate,
            self._format_gate,
            self._schedule_gate,
            self._score_floor_gate,
            self._media_ready_gate,
            self._daily_cap_gate,
            self._cooldown_gate,
        ]

    def evaluate(self, blueprint: dict, platform: str) -> GateResult:
        """Run all gates. First failure wins."""
        for gate in self._gates:
            result = gate(blueprint, platform)
            if not result.allowed:
                return result
        return GateResult(allowed=True, reason="passed", gate_name="all")

    def _approval_gate(self, bp: dict, platform: str) -> GateResult:
        if bp.get("action_taken") == "approved":
            return GateResult(allowed=True, reason="approved", gate_name="approval_gate")
        # Express lane bypass: CRITICAL/HIGH urgency stories skip approval
        urgency = (bp.get("urgency_classification") or {}).get("urgency", "")
        if urgency in ("CRITICAL", "HIGH"):
            return GateResult(
                allowed=True,
                reason=f"express bypass ({urgency})",
                gate_name="approval_gate",
            )
        return GateResult(allowed=False, reason="Not approved", gate_name="approval_gate")

    def _format_gate(self, bp: dict, platform: str) -> GateResult:
        fmt = bp.get("format", "")
        if self._config.get("POLICY", {}).get("strict_creator_video_only"):
            if fmt not in ("reel", "video", "short"):
                return GateResult(
                    allowed=False,
                    reason=f"Format '{fmt}' not allowed (strict video policy)",
                    gate_name="format_gate",
                )
        return GateResult(allowed=True, reason="format ok", gate_name="format_gate")

    def _schedule_gate(self, bp: dict, platform: str) -> GateResult:
        scheduled = bp.get("scheduled_for")
        if not scheduled:
            return GateResult(allowed=True, reason="no schedule", gate_name="schedule_gate")
        try:
            dt = datetime.fromisoformat(scheduled)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            if dt > datetime.now(UTC):
                return GateResult(
                    allowed=False,
                    reason=f"Scheduled for {dt}",
                    gate_name="schedule_gate",
                )
        except (ValueError, TypeError):
            return GateResult(allowed=False, reason=f"Unparseable schedule: {scheduled}", gate_name="schedule_gate")
        return GateResult(allowed=True, reason="due", gate_name="schedule_gate")

    def _score_floor_gate(self, bp: dict, platform: str) -> GateResult:
        score = float(bp.get("priority_score", 0.5) or 0.5)
        floor = 0.3  # minimum to publish
        if score < floor:
            return GateResult(
                allowed=False,
                reason=f"Score {score} below floor {floor}",
                gate_name="score_floor_gate",
            )
        return GateResult(allowed=True, reason=f"Score {score}", gate_name="score_floor_gate")

    def _media_ready_gate(self, bp: dict, platform: str) -> GateResult:
        paths = bp.get("visual_paths", "[]")
        try:
            parsed = json.loads(paths) if isinstance(paths, str) else paths
        except (json.JSONDecodeError, TypeError):
            parsed = []
        if not parsed:
            return GateResult(allowed=False, reason="No media ready", gate_name="media_ready_gate")
        return GateResult(allowed=True, reason="media present", gate_name="media_ready_gate")

    def _daily_cap_gate(self, bp: dict, platform: str) -> GateResult:
        if self._daily_cap and not self._daily_cap.can_publish(platform):
            return GateResult(allowed=False, reason="Daily cap reached", gate_name="daily_cap_gate")
        return GateResult(allowed=True, reason="under cap", gate_name="daily_cap_gate")

    def _cooldown_gate(self, bp: dict, platform: str) -> GateResult:
        # Check if blueprint was recently attempted (within 5 minutes)
        publish_attempts = int(bp.get("publish_attempts", 0) or 0)
        if publish_attempts >= 3:
            return GateResult(
                allowed=False,
                reason=f"Max publish attempts reached ({publish_attempts})",
                gate_name="cooldown_gate",
            )
        return GateResult(allowed=True, reason="cooldown ok", gate_name="cooldown_gate")
