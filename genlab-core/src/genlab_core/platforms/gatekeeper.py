"""Publishing gatekeeping — composable gates that run on raw blueprint dicts."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# R-82: a blueprint whose scheduled slot is more than this far in the past has
# missed its publish window and must NOT stay eligible. The daily cadence has
# slots between ~02:30 and ~14:30 UTC and a late/retry run as late as ~10:30
# UTC, so the worst legitimate same-day-or-next-morning lateness is well under
# 18h, while a full prior-day cycle is ≥24h — 18h cleanly separates the two
# without a midnight-boundary cliff (a pure date check would wrongly kill a slot
# scheduled minutes before 00:00 UTC and flake tests that use "now − 5min").
_SCHEDULE_STALE_AFTER = timedelta(hours=18)


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

    # Score-floor hysteresis constants (2026-08-13, live-fire fix
    # for the "0.005 below cutoff loses today's slot" pattern that
    # took out gaming's daily publish). Defaults are conservative:
    # scores must be within 5% (0.05) of the floor AND the niche
    # must have starved forward queue (<2 scheduled next 48h) for
    # the relaxation to apply.
    _HYSTERESIS_BAND: float = 0.05
    _STARVED_QUEUE_THRESHOLD: int = 2
    _QUEUE_LOOKAHEAD_HOURS: int = 48

    def __init__(self, config: dict = None, daily_cap=None, backlog=None):
        self._config = config or {}
        self._daily_cap = daily_cap
        self._backlog = backlog
        # Per-instance cache: niche_id -> forward queue depth. Avoids
        # re-querying the backlog when multiple blueprints for the same
        # niche+platform are evaluated in one publisher pass. The
        # gatekeeper is short-lived (recreated per pass) so cache
        # freshness matches the pass's own consistency window.
        self._forward_queue_cache: dict[str, int] = {}
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
        """Approval is the SOLE pass-through for the approval gate.

        Audit R-08 — the legacy express-lane bypass let a blueprint
        through whenever ``urgency_classification.urgency`` was
        ``CRITICAL`` or ``HIGH``. That urgency was derived from a
        regex over fetched ``title``+``summary`` text (RSS / YouTube
        / Reddit — all attacker-controllable). A title containing
        "breaking" or "$1B" would auto-publish to ALL 5 channels with
        zero human review. The gate is now strict: only dashboard
        approval passes. ExpressLane still runs upstream to surface
        urgent stories at the top of the operator's review queue —
        but it no longer SKIPS the queue.
        """
        if bp.get("action_taken") == "approved":
            return GateResult(allowed=True, reason="approved", gate_name="approval_gate")
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
                # R-82: a naive scheduled_for is ambiguous — the gatekeeper reads
                # it as UTC, but the (dead) scheduling.is_due read it as IST, a
                # +5:30 latent trap. The pipeline writes tz-aware UTC today, so
                # this path shouldn't fire; if it does, coerce to UTC (the single
                # tz authority) and surface it loudly rather than silently.
                logger.warning("[gatekeeper] naive scheduled_for %r — assuming UTC", scheduled)
                dt = dt.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            if dt > now:
                return GateResult(
                    allowed=False,
                    reason=f"Scheduled for {dt}",
                    gate_name="schedule_gate",
                )
            # R-82: enforce an UPPER bound, not just "not future". Without it a
            # stale blueprint stays eligible forever, feeding the double-publish
            # triad (a prior day's slot getting re-picked). Today's due slot and
            # a same-day-or-next-morning late/retry run still pass; only a slot
            # >18h stale is blocked.
            if dt < now - _SCHEDULE_STALE_AFTER:
                return GateResult(
                    allowed=False,
                    reason=f"Stale schedule: {dt} is >{_SCHEDULE_STALE_AFTER} past",
                    gate_name="schedule_gate",
                )
        except (ValueError, TypeError):
            return GateResult(
                allowed=False,
                reason=f"Unparseable schedule: {scheduled}",
                gate_name="schedule_gate",
            )
        return GateResult(allowed=True, reason="due", gate_name="schedule_gate")

    def _score_floor_gate(self, bp: dict, platform: str) -> GateResult:
        # Post-engagement_rate-audit fix (PR follow-up to #588): the prior
        # ``bp.get("priority_score", 0.5) or 0.5`` resurrected an explicit
        # ``priority_score=0`` back to 0.5, silently bypassing the score
        # floor (default 0.3). ``qc_gates.py:163`` legitimately writes
        # ``priority_score = max(0, score - SCORE_PENALTY)`` on QC failure
        # — a starting score of 0.3 lands at 0.0 after penalty, and the
        # ``or 0.5`` resurrection then promoted that QC-degraded blueprint
        # above the floor. Use a try/except around ``float()`` so explicit
        # ``0`` survives but truly missing / unparseable values still get
        # the neutral 0.5 default.
        raw_score = bp.get("priority_score", 0.5)
        try:
            score = float(raw_score) if raw_score is not None else 0.5
        except (TypeError, ValueError):
            score = 0.5
        # Niche-level floor if config carries one, else fall back to the
        # hardcoded 0.3. The config path is
        # niche_config["publishing"]["score_floor"].
        niche_cfg = self._config.get("niche_config") if self._config else None
        floor = 0.3
        if isinstance(niche_cfg, dict):
            pub = niche_cfg.get("publishing") or {}
            if isinstance(pub, dict):
                try:
                    floor = float(pub.get("score_floor", 0.3))
                except (TypeError, ValueError):
                    floor = 0.3
        if score >= floor:
            return GateResult(
                allowed=True,
                reason=f"Score {score}",
                gate_name="score_floor_gate",
            )
        # Hysteresis: score is BELOW the strict floor. Allow when
        # (a) score is within _HYSTERESIS_BAND of the floor AND
        # (b) the niche's forward queue is starved. Prevents "0.005
        # below cutoff loses today's slot" pattern. When queue is
        # healthy, strict floor still applies — protects content
        # quality when we have alternatives.
        if score >= floor - self._HYSTERESIS_BAND:
            niche_id = str(bp.get("niche_id", "") or "")
            if niche_id and self._is_forward_queue_starved(niche_id):
                return GateResult(
                    allowed=True,
                    reason=(
                        f"Score {score} below floor {floor} but within "
                        f"{self._HYSTERESIS_BAND} hysteresis; niche "
                        f"forward queue starved (<{self._STARVED_QUEUE_THRESHOLD} "
                        f"in next {self._QUEUE_LOOKAHEAD_HOURS}h)"
                    ),
                    gate_name="score_floor_gate",
                )
        return GateResult(
            allowed=False,
            reason=f"Score {score} below floor {floor}",
            gate_name="score_floor_gate",
        )

    def _is_forward_queue_starved(self, niche_id: str) -> bool:
        """Query backlog for scheduled blueprints in the next
        `_QUEUE_LOOKAHEAD_HOURS`. Returns True when count is below
        `_STARVED_QUEUE_THRESHOLD`.

        Fail-CLOSED (return False = strict floor stays enforced) on
        any error — safer to reject a borderline blueprint than to
        accept a low-quality one because a DB read failed. Different
        from most fail-open observability wires; hysteresis relaxation
        is a quality-gate change and deserves conservative fallback.

        Cached per-instance for the publisher pass — one query per
        niche_id per gatekeeper instance.
        """
        if niche_id in self._forward_queue_cache:
            return (
                self._forward_queue_cache[niche_id] < self._STARVED_QUEUE_THRESHOLD
            )
        depth = self._query_forward_queue_depth(niche_id)
        if depth is None:
            # Fail-closed: caller sees "not starved" -> strict floor applies
            return False
        self._forward_queue_cache[niche_id] = depth
        return depth < self._STARVED_QUEUE_THRESHOLD

    def _query_forward_queue_depth(self, niche_id: str) -> int | None:
        """Count blueprints with scheduled_for in the next N hours.

        Returns None on any error (caller treats as not-starved =
        strict floor applies).
        """
        if not self._backlog:
            return None
        try:
            # BacklogClient.blueprints.all supports niche_id + a
            # SharePoint-style formula. Use a permissive query and
            # filter in-Python for portability across the SP/Postgres
            # backends the client abstracts over.
            rows = self._backlog.blueprints.all(
                niche_id=niche_id,
                max_records=50,
                formula="AND({scheduled_for}!='')",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "[gatekeeper] forward_queue query failed niche=%s: %s",
                niche_id, exc,
            )
            return None
        now = datetime.now(UTC)
        cutoff = now + timedelta(hours=self._QUEUE_LOOKAHEAD_HOURS)
        count = 0
        for row in rows:
            fields = row.get("fields", row) if isinstance(row, dict) else {}
            sched_raw = fields.get("scheduled_for") or fields.get("scheduledFor")
            if not sched_raw:
                continue
            try:
                # ISO-8601 with Z or +offset — Python 3.11+ handles both
                sched = datetime.fromisoformat(
                    str(sched_raw).replace("Z", "+00:00")
                )
                if sched.tzinfo is None:
                    sched = sched.replace(tzinfo=UTC)
            except (ValueError, TypeError):
                continue
            if now <= sched <= cutoff:
                count += 1
        return count

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
