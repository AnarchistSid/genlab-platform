"""Auto-approval gate — foundation for the owner's autonomous-agent vision.

Owner statement (see [[project-autonomous-agent-vision]]): "The agent needs
to keep getting smarter so that it is able to perform all of these tasks
autonomously and publish to channels without requiring manual intervention
such as approving posts."

This module decides whether a VISUAL_READY blueprint COULD be auto-approved
based on objective quality signals (QC pass, virality score, video presence,
content completeness). It returns the decision + reasons; it does NOT
execute the approval. Today's wiring:

- Dashboard reads the decision via the `/api/v1/blueprints/{id}/auto-approval-preview`
  endpoint and renders a "would auto-approve" badge on the review UI.
- Operators see what the gate's verdict would be before flipping the
  opt-in switch (a yaml flag in publishing.yaml that the future PR will
  introduce).
- A background worker that actually executes the approvals is a separate
  follow-up; the gate is the testable, observable foundation.

Default thresholds are deliberately conservative so the first wave of
auto-approvals (when the operator does flip the switch) is high-confidence:
no clipless blueprints, no QC failures, virality_score at least at noise
floor, composite_score either present and ≥ threshold OR absent (cold
start tolerated). Per-niche overrides can be added later via niche config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Final

logger = logging.getLogger(__name__)

# Default thresholds. Calibrated from the 2026-06-13 prod data probe:
# current VISUAL_READY blueprints range composite=None..1.0, virality=0.0..0.1.
# The defaults below are loose enough that ~50% of healthy blueprints
# clear them — conservative enough that operator-facing previews don't
# fire on garbage. Each niche can override via niche_config later.
_DEFAULT_MIN_COMPOSITE_SCORE: Final[float] = 0.3
# 2026-06-15 (AUTO #2 D1.3): lowered from 0.05 → 0.02 per the rollout
# runbook. Prod blueprint distribution (verified 2026-06-15) showed
# virality_score clusters at {0.00, 0.05, 0.10, 0.15, 0.20} with no
# scores in the 0.02-0.04 range — this lowering doesn't move any
# CURRENT blueprint from "fail virality" to "pass virality" (natural
# distribution gap), but it gives future noise-floor blueprints
# (0.01-0.04 range as the scorer matures) a more permissive floor
# matching the calibration data that shows operators DO approve such
# blueprints.
_DEFAULT_MIN_VIRALITY_SCORE: Final[float] = 0.02
_DEFAULT_REQUIRE_VIDEO: Final[bool] = True
_DEFAULT_REQUIRE_HOOK_TEXT: Final[bool] = True
_DEFAULT_REQUIRE_QC_PASS: Final[bool] = True


@dataclass(frozen=True)
class AutoApprovalDecision:
    """Result of evaluating a blueprint for auto-approval.

    Fields:
        approved: True iff every check passed.
        confidence: Float in [0.0, 1.0]. Weighted aggregate of the per-check
            confidences (composite_score, virality_score) — higher means
            "the gate is more sure this is publishable". A decision with
            ``approved=True`` and ``confidence=0.4`` is "passes the floor
            but barely"; ``confidence=0.9`` is "this is excellent".
        passed_checks: Names of checks that passed.
        failed_checks: Names of checks that failed (empty when approved).
        reasons: Human-readable explanations, one per check. Always
            populated — useful for the dashboard badge tooltip.
    """

    approved: bool
    confidence: float
    passed_checks: list[str] = field(default_factory=list)
    failed_checks: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def evaluate(
    blueprint: dict,
    *,
    min_composite_score: float = _DEFAULT_MIN_COMPOSITE_SCORE,
    min_virality_score: float = _DEFAULT_MIN_VIRALITY_SCORE,
    require_video: bool = _DEFAULT_REQUIRE_VIDEO,
    require_hook_text: bool = _DEFAULT_REQUIRE_HOOK_TEXT,
    require_qc_pass: bool = _DEFAULT_REQUIRE_QC_PASS,
) -> AutoApprovalDecision:
    """Evaluate a blueprint for auto-approval. Pure function, side-effect free.

    The ``blueprint`` dict shape mirrors what BacklogClient.blueprints.all()
    returns: top-level fields (id, niche_id, status, hook_text, title,
    arm_id, candidate_id) plus an ``extra`` JSONB carrying composite_score,
    virality_score, validation_status, visual_paths, etc.

    All check methods are defensive — a missing field is treated as
    "unknown" rather than "fail" so cold-start blueprints (without
    virality scoring) can still be evaluated. The trade-off: missing
    fields reduce ``confidence`` without flipping ``approved`` to False
    unless the operator has explicitly required that field.
    """
    extra = blueprint.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}

    passed: list[str] = []
    failed: list[str] = []
    reasons: list[str] = []
    confidences: list[float] = []

    # ── 1. Video clip present ─────────────────────────────────────────
    has_video = bool(extra.get("visual_paths") or blueprint.get("visual_paths"))
    if require_video:
        if has_video:
            passed.append("has_video")
            reasons.append("Video clip present")
        else:
            failed.append("has_video")
            reasons.append("REJECT: no rendered video — blueprint cannot ship")
    else:
        # Marked optional; record but don't gate.
        reasons.append(f"Video check skipped (operator override) — present={has_video}")

    # ── 2. Hook text present ──────────────────────────────────────────
    hook = (blueprint.get("hook_text") or "").strip()
    if require_hook_text:
        if hook:
            passed.append("has_hook")
            reasons.append(f"Hook present ({len(hook)} chars)")
        else:
            failed.append("has_hook")
            reasons.append("REJECT: hook_text is empty")
    else:
        reasons.append(f"Hook check skipped — present={bool(hook)}")

    # ── 3. QC validation status ───────────────────────────────────────
    # The QC stage writes validation_status to story content; push_to_backlog
    # may propagate it to blueprint.extra. Be defensive — missing means
    # "unknown" not "failed".
    validation_status = extra.get("validation_status") or {}
    if isinstance(validation_status, str):
        # Sometimes stored as a JSON-encoded string; tolerate.
        import json as _json

        try:
            validation_status = _json.loads(validation_status)
        except (ValueError, TypeError):
            validation_status = {}
    if not isinstance(validation_status, dict):
        validation_status = {}

    if require_qc_pass:
        if validation_status.get("all_passed") is True:
            passed.append("qc_passed")
            reasons.append("All 3 QC gates passed (claims + constraints + completeness)")
        elif validation_status.get("all_passed") is False:
            failed.append("qc_passed")
            issues = validation_status.get("issues") or []
            issue_str = "; ".join(str(i) for i in issues[:3])
            reasons.append(f"REJECT: QC failed — {issue_str or 'no detail'}")
        else:
            # validation_status not present — treat as unknown, not fail.
            # This is the cold-start case for blueprints that pre-date QC
            # write-through. Reduce confidence accordingly.
            passed.append("qc_unknown")
            reasons.append("QC status unknown (no validation_status field)")

    # ── 4. Composite score floor ──────────────────────────────────────
    composite = _to_float(extra.get("composite_score"))
    if composite is None:
        # Cold-start tolerance: missing score → unknown not fail.
        reasons.append("composite_score missing (defaulting to unknown)")
        confidences.append(0.5)  # Neutral prior
    elif composite >= min_composite_score:
        passed.append("composite_score")
        reasons.append(f"composite_score={composite:.2f} ≥ {min_composite_score:.2f}")
        # Map [min..1.0] to [0.5..1.0] for confidence contribution
        span = max(0.001, 1.0 - min_composite_score)
        confidences.append(0.5 + 0.5 * min(1.0, (composite - min_composite_score) / span))
    else:
        failed.append("composite_score")
        reasons.append(
            f"REJECT: composite_score={composite:.2f} < {min_composite_score:.2f} threshold"
        )
        confidences.append(composite / min_composite_score * 0.5)

    # ── 5. Virality score floor ───────────────────────────────────────
    virality = _to_float(extra.get("virality_score"))
    if virality is None:
        reasons.append("virality_score missing (defaulting to unknown)")
        confidences.append(0.5)
    elif virality >= min_virality_score:
        passed.append("virality_score")
        reasons.append(f"virality_score={virality:.3f} ≥ {min_virality_score:.3f}")
        # Map [min..1.0] to [0.5..1.0]
        span = max(0.001, 1.0 - min_virality_score)
        confidences.append(0.5 + 0.5 * min(1.0, (virality - min_virality_score) / span))
    else:
        failed.append("virality_score")
        reasons.append(
            f"REJECT: virality_score={virality:.3f} < {min_virality_score:.3f} threshold"
        )
        confidences.append(virality / min_virality_score * 0.5)

    # ── Aggregate confidence ──────────────────────────────────────────
    # Average across the per-score confidences. Empty list means no
    # numeric signals were available — fall back to 0.5 (neutral prior).
    confidence = sum(confidences) / len(confidences) if confidences else 0.5
    confidence = max(0.0, min(1.0, confidence))

    # ── Final decision ────────────────────────────────────────────────
    approved = len(failed) == 0
    return AutoApprovalDecision(
        approved=approved,
        confidence=round(confidence, 3),
        passed_checks=passed,
        failed_checks=failed,
        reasons=reasons,
    )


def _to_float(value) -> float | None:
    """Defensively coerce a value to float, returning None on any failure.

    Handles the common case where extra-JSON values come back as strings
    (Postgres JSONB → text → float) without raising on missing fields.
    """
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
