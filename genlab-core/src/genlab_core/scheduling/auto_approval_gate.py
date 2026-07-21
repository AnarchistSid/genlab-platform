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

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Final

logger = logging.getLogger(__name__)


def _decode_visual_paths(raw: Any) -> list:
    """Best-effort decode of a visual_paths field into a list.

    The Postgres path stores ``visual_paths`` as a JSON-encoded
    string in many blueprints. `bool` of any non-empty string is True,
    so a naive `bool(extra["visual_paths"])` check passes for the
    string ``"[]"`` (an empty list serialized) even though no video
    actually exists.

    Mirrors ``calibration_helper._safe_visual_paths`` and
    ``auto_approver._safe_json_list``. Returns [] on any failure.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, list) else []
        except (ValueError, TypeError):
            return []
    return []


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
    min_composite_score: float | None = None,
    min_virality_score: float | None = None,
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

    2026-06-21 (Lever A): when ``min_composite_score`` / ``min_virality_score``
    is None (the default), the gate resolves thresholds via:
        (1) per-niche override from ``gate_tuner.get_overrides_for_niche()``
        (2) the module-level ``_DEFAULT_*`` constants on miss
    Explicit kwargs override both — preserves operator-CLI / test injection
    while letting the calibration feedback loop adapt thresholds per niche.
    """
    extra = blueprint.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}

    # ── Resolve thresholds: explicit kwarg > Strategist accepted proposal ──
    # ──                     > gate_tuner niche override > default        ──
    # Lazy import: gate_tuner imports calibration_logger which imports psycopg.
    # The gate is in many import paths that shouldn't require DB libs.
    if min_composite_score is None or min_virality_score is None:
        niche_id = (blueprint.get("niche_id") or "").strip()

        try:
            from genlab_core.learning.gate_tuner import get_overrides_for_niche

            override_composite, override_virality = get_overrides_for_niche(niche_id)
        except Exception as exc:
            # The override system MUST NEVER block evaluation. Fall back
            # to defaults silently — the gate continues to work exactly
            # as it did pre-Lever-A.
            logger.debug("[gate] override lookup failed (%s); using defaults", exc)
            override_composite, override_virality = None, None

        # PR Strategist-3: an operator-accepted Strategist gate_threshold
        # proposal WINS over gate_tuner because the operator's manual
        # accept is more deliberate than the auto-tuning calibration.
        # Fail-closed: if strategy_phase can't be loaded, we silently
        # fall back to gate_tuner + default. This is the SAME safety
        # contract as gate_tuner itself.
        try:
            from genlab_core.scheduling.strategy_phase import get_phase_config

            phase_config = get_phase_config(niche_id)
            if phase_config.gate_threshold_override is not None:
                override_composite = phase_config.gate_threshold_override
                logger.debug(
                    "[gate] Strategist proposal override niche=%s threshold=%.3f",
                    niche_id,
                    override_composite,
                )
        except Exception as exc:
            logger.debug("[gate] Strategist override lookup failed (%s)", exc)

        if min_composite_score is None:
            min_composite_score = (
                override_composite
                if override_composite is not None
                else _DEFAULT_MIN_COMPOSITE_SCORE
            )
        if min_virality_score is None:
            min_virality_score = (
                override_virality if override_virality is not None else _DEFAULT_MIN_VIRALITY_SCORE
            )

    passed: list[str] = []
    failed: list[str] = []
    reasons: list[str] = []
    confidences: list[float] = []

    # ── 1. Video clip present ─────────────────────────────────────────
    # 2026-06-15 audit fix: defensively decode visual_paths inside the
    # gate. The 4 caller-side wrapper-builders (auto_approver,
    # calibration_helper, review_server, dashboard preview) all decode
    # JSON strings to lists — but ONLY when ``blueprint["extra"]`` is
    # NOT a dict. On the Postgres path EVERY blueprint has ``extra`` as
    # a dict, so the caller-side decode short-circuits and
    # ``extra["visual_paths"]`` is whatever shape the original write
    # put there — often the literal JSON string ``"[]"`` or
    # ``'["/x.mp4"]'``. ``bool`` of any non-empty string is True, so
    # this check used to falsely pass ``has_video=True`` for blueprints
    # with empty visual_paths arrays. Decoding inline closes that hole
    # regardless of which caller-side build/skip the wrapper.
    has_video = bool(
        _decode_visual_paths(extra.get("visual_paths"))
        or _decode_visual_paths(blueprint.get("visual_paths"))
    )
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
        # 2026-07-14 range-fix: real ai_creators virality_score
        # distribution sits at 0.10-0.20 with 0.30 being 'strong'.
        # Prior mapping used span=[min..1.0]=[0.05..1.0]=0.95 → good
        # scores of 0.15 only got confidence 0.55. That structurally
        # dragged the 6-check mean below 0.80 → auto-approver produced
        # 0 approvals for the ai_creators niche for weeks despite
        # PR #786 lowering the threshold from 0.85 → 0.80.
        #
        # New mapping uses ``virality_soft_ceiling`` (default 0.30 —
        # the empirical top-of-distribution) as the value that maps to
        # confidence=1.0. Scores between min and ceiling map linearly
        # to [0.5..1.0]. Above ceiling (rare, meaningful outliers)
        # also produce confidence=1.0 — the mapping saturates.
        #
        # Effect: virality=0.15 now yields conf=0.70 instead of 0.55.
        # Combined with hook_classifier_score fix (Task #52), the
        # 6-check mean should regularly clear 0.80 on healthy content.
        virality_soft_ceiling = 0.30
        span = max(0.001, virality_soft_ceiling - min_virality_score)
        normalized = min(1.0, (virality - min_virality_score) / span)
        confidences.append(0.5 + 0.5 * normalized)
    else:
        failed.append("virality_score")
        reasons.append(
            f"REJECT: virality_score={virality:.3f} < {min_virality_score:.3f} threshold"
        )
        confidences.append(virality / min_virality_score * 0.5)

    # ── 6. Hook classifier score (soft signal) ────────────────────────
    # 2026-06-22 Loop 2 close: Lever D1 (PR #420) activated the XGBoost
    # hook classifier in BaseHookStrategy — every hook now gets a
    # [0,1] predicted-engagement score. PR #420 set the score on the
    # story dict but no consumer read it. This 6th check turns the
    # score into a SOFT signal feeding the gate's confidence aggregate:
    #
    #   - missing: cold-start tolerant — NO contribution to confidence
    #     aggregate (preserves pre-Lever-D1 confidence math). Many
    #     in-flight blueprints predate the wire; their confidence
    #     scores must remain unchanged so historical thresholds still
    #     apply identically.
    #   - >= 0.5: passing — score contributes positively to confidence
    #   - < 0.5: still passing (not a hard reject — score is noisy)
    #     but contributes the raw score as confidence (low score drags
    #     the average toward reject without forcing failed_checks).
    #
    # Intentional design: NEVER fails the gate on its own — the
    # classifier is high-recall, low-precision and a hard reject
    # would over-suppress good hooks the model under-rates. The
    # signal flows through the confidence aggregate where it
    # competes with composite + virality.
    hook_clf = _to_float(extra.get("hook_classifier_score"))
    # 2026-07-17: under-trained-model uncertainty band. Empirical
    # prod distribution (session-2026-07-17 audit round 3):
    #     ai_creators: n=6, avg=0.167, max=0.316 (0/6 ≥ 0.5)
    #     anime:       n=14, avg=0.261, max=0.653 (2/14 ≥ 0.5)
    #     gaming:      n=23, avg=0.293, max=0.705 (4/23 ≥ 0.5)
    #     movies:      n=21, avg=0.227, max=0.502 (1/21 ≥ 0.5)
    #     sports:      n=16, avg=0.251, max=0.531 (1/16 ≥ 0.5)
    # The XGBoost model was trained at MIN_EXAMPLES=50 → the raw
    # probas cluster in [0.05, 0.35]. Treating a 0.17 score as
    # "17% confidence" is WRONG — the model has no useful signal.
    # That drag was the structural block on auto-approver Week 1→2
    # ramp (~92% calibration agreement but 0 approvals for 20+ days
    # because the mean confidence stayed below 0.80).
    #
    # Fix: skip contribution when the model's output is in the
    # uncertainty band [0, HOOK_CLF_STRONG_FLOOR). The model is
    # only informative when it produces a strong-positive signal
    # (≥0.5, or configurably ≥0.4). Below that, treat as no-signal
    # (same as the None cold-start path).
    #
    # Long-term fix is model recalibration + more training data.
    # This gate change unblocks auto-approver in the interim without
    # weakening its rejection criteria.
    HOOK_CLF_STRONG_FLOOR = 0.4
    if hook_clf is None:
        reasons.append("hook_classifier_score missing (no contribution)")
        # Intentionally NO append to confidences — see docstring above.
    else:
        clamped = max(0.0, min(1.0, hook_clf))
        if clamped >= 0.5:
            passed.append("hook_classifier_score")
            reasons.append(f"hook_classifier_score={clamped:.2f} ≥ 0.50 (predicted strong)")
            confidences.append(clamped)
        elif clamped >= HOOK_CLF_STRONG_FLOOR:
            # In the borderline band [0.4, 0.5) — contribute the raw
            # score so a middle-ground model opinion has some weight
            # without dominating.
            reasons.append(
                f"hook_classifier_score={clamped:.2f} < 0.50 (borderline soft signal)"
            )
            confidences.append(clamped)
        else:
            # Below the noise floor — the model has no useful signal
            # for this hook. Skip contribution to preserve confidence
            # math for well-formed blueprints. Same discipline as
            # cold-start None case.
            reasons.append(
                f"hook_classifier_score={clamped:.2f} < {HOOK_CLF_STRONG_FLOOR:.2f} "
                "(under-trained-model uncertainty, no contribution)"
            )

    # ── Aggregate confidence ──────────────────────────────────────────
    # Average across the per-score confidences. Empty list means no
    # numeric signals were available — fall back to 0.5 (neutral prior).
    confidence = sum(confidences) / len(confidences) if confidences else 0.5
    confidence = max(0.0, min(1.0, confidence))

    # ── Final decision ────────────────────────────────────────────────
    approved = len(failed) == 0
    decision = AutoApprovalDecision(
        approved=approved,
        confidence=round(confidence, 3),
        passed_checks=passed,
        failed_checks=failed,
        reasons=reasons,
    )

    # ── Lever C (2026-06-21): LLM-as-judge on borderline cases ────────
    # When the gate's confidence is borderline (0.3..0.7), the 5 binary
    # heuristics aren't capturing enough signal to make a clear call.
    # Ask Claude Haiku to reason about the blueprint with the gate's
    # signals as context — the LLM verdict can override the rule-based
    # decision (with audit trail in the reasons list).
    #
    # Opt-in via GENLAB_LLM_JUDGE_ENABLED=1 env for safe shadow rollout.
    # Default disabled = same behavior as pre-Lever-C (rule-based only).
    # Cost: ~$0.0002 per borderline blueprint × ~1/niche/day × 5 niches
    # = ~$1/year at most-permissive activation.
    try:
        if 0.3 <= confidence <= 0.7:
            judged = _llm_judge_borderline(blueprint, decision)
            if judged is not None:
                decision = judged
    except Exception as exc:
        # LLM judge MUST NEVER block the gate — fall through to the
        # rule-based decision silently. Logged at debug so a flaky
        # judge doesn't drown the logs.
        logger.debug("[gate] LLM judge raised (using rule-based decision): %s", exc)

    return decision


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


# ────────────────────────────────────────────────────────────────────
# Lever C (2026-06-21): LLM-as-judge on borderline gate decisions
# ────────────────────────────────────────────────────────────────────


_LLM_JUDGE_SYSTEM_PROMPT = """You evaluate whether a content blueprint should be auto-approved for publishing.

You see the rule-based gate's signals — which numeric checks passed/failed, and the gate's confidence (0..1).
You're asked to reason about the BORDERLINE cases the rule-based gate is least sure about.

A blueprint should be APPROVED when:
- It has a video clip and a hook
- The hook is grounded in the source story (not a generic template, not hallucinated)
- The content is on-niche and brand-safe
- The quality signals (composite_score, virality_score) are reasonable for the niche

A blueprint should be REJECTED when:
- The hook is generic / weak / hallucinated
- The content is off-niche or low-quality
- The quality signals strongly suggest the post will underperform
- There's a brand-safety or content-policy concern

Respond with ONLY a JSON object:
{"approved": true|false, "reason": "<one short phrase explaining your verdict>"}
"""


def _llm_judge_borderline(
    blueprint: dict,
    rule_decision: AutoApprovalDecision,
) -> AutoApprovalDecision | None:
    """Ask Claude Haiku to reason about a borderline gate decision.

    Returns:
        - An updated AutoApprovalDecision when the LLM judge fires
          (with the judge's verdict + audit-trail entry in the reasons list)
        - None when the judge is disabled OR fails — caller uses the
          rule-based decision unchanged

    Opt-in via ``GENLAB_LLM_JUDGE_ENABLED=1`` env var. Default disabled
    so the mechanism can ship + be tested in shadow mode before being
    activated in production. Once shadow data shows the judge improves
    operator-gate agreement without false positives, flip the env var
    per-niche (initially) then globally.

    Why this is a separate function (not inlined in evaluate()):
    - Cleanly testable in isolation (no need to build the full
      rule-based decision flow)
    - Single place to wrap the LLM API call + JSON parsing
    - Easy to extend later with niche-specific judge prompts
    - Mirror of Lever K's ``_critique_hook_grounded`` design

    The judge sees:
    - hook_text
    - niche_id
    - rule-based gate signals (passed_checks, failed_checks, confidence)
    - composite_score + virality_score from extra

    The judge does NOT see:
    - The full video file (G1/G2 will add that)
    - Calibration data from past operator decisions on similar blueprints
      (a future iteration could RAG-retrieve them)

    Cost: ~1 Haiku call per borderline blueprint (~$0.0002). Only fires
    on ~10% of blueprints (the borderline ones), so the steady-state
    cost is dominated by happy-path runs that skip the judge entirely.
    """
    import os

    if os.environ.get("GENLAB_LLM_JUDGE_ENABLED", "0") != "1":
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    try:
        niche_id = (blueprint.get("niche_id") or "unknown").strip()
        hook = (blueprint.get("hook_text") or "")[:200]
        extra = blueprint.get("extra") if isinstance(blueprint.get("extra"), dict) else {}
        composite = extra.get("composite_score")
        virality = extra.get("virality_score")

        signals = (
            f"Niche: {niche_id}\n"
            f"Hook: {hook!r}\n"
            f"Composite score: {composite}\n"
            f"Virality score: {virality}\n"
            f"Rule-based gate signals — passed: {rule_decision.passed_checks}, "
            f"failed: {rule_decision.failed_checks}\n"
            f"Rule-based confidence: {rule_decision.confidence:.2f}\n"
        )

        # 2026-06-22 — route via DecisionRouter so borderline-confidence
        # decisions (0.4-0.6 band) escalate from Haiku to Sonnet
        # automatically. This is the highest-stakes LLM call in the
        # pipeline (the gate's verdict ships content); the extra
        # reasoning capacity is worth the ~15x cost on the ~20% of
        # blueprints that actually need it. Decisions outside the
        # escalation band stay on Haiku (most of the volume).
        from genlab_core.cost.decision_router import route_decision

        model = route_decision("gate_judge", confidence=rule_decision.confidence)

        # 2026-06-22 — scratchpad context. The weekly Opus reflection
        # (PR #474) synthesizes 7 days of operator reviews + edits +
        # bombed posts into actionable markdown. Prepending it to the
        # judge's system prompt gives borderline-confidence decisions
        # the same "what we learned this week" context the hook
        # generator has. Fail-OPEN: missing scratchpad = empty string
        # = no prepend, judge proceeds with stock prompt.
        try:
            from genlab_core.learning.scratchpad import read_scratchpad

            scratchpad = read_scratchpad()
        except Exception:  # noqa: BLE001 — scratchpad is augmentation
            scratchpad = ""
        if scratchpad:
            judge_system = (
                "## Recent learnings (scratchpad)\n\n"
                + scratchpad
                + "\n\n---\n\n"
                + _LLM_JUDGE_SYSTEM_PROMPT
            )
        else:
            judge_system = _LLM_JUDGE_SYSTEM_PROMPT

        # U-01: cache the judge system prompt. Same blueprint-by-
        # blueprint within a run reuses the same scratchpad-augmented
        # rubric. The scratchpad prepend pushes the prompt well past
        # the 4000-char threshold; without scratchpad the helper
        # auto-skips (zero behavior change).
        from genlab_core.llm.prompt_cache import with_prompt_cache

        # 2026-07-21: fallback to OpenAI on Anthropic exhaustion. The LLM
        # judge is the highest-stakes call in the pipeline (its verdict
        # ships content); losing it during Anthropic outages means
        # falling back to rule-based scoring for every borderline
        # decision. Shared circuit breaker via `genlab_core.llm.fallback`
        # so this counts toward the same exhaustion budget as other
        # sites — total Anthropic burn is bounded.
        from genlab_core.llm.fallback import (
            call_openai_fallback,
            cb_is_open,
            cb_record_exhaustion,
            cb_record_success,
            fallback_enabled,
            should_fallback,
        )

        openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        cached_judge_system = with_prompt_cache(judge_system)
        raw = ""

        if fallback_enabled() and cb_is_open() and openai_key:
            # CB-open fast path: straight to OpenAI, no Anthropic attempt.
            logger.debug("[gate] LLM judge CB open — routing to OpenAI")
            raw = call_openai_fallback(
                judge_system,
                signals,
                max_tokens=80,
                temperature=0.0,
                api_key=openai_key,
            )
        else:
            try:
                client = anthropic.Anthropic(api_key=api_key)
                response = client.messages.create(
                    model=model,
                    max_tokens=80,
                    temperature=0.0,  # Deterministic verdict for the same inputs
                    system=cached_judge_system,
                    messages=[{"role": "user", "content": signals}],
                )
            except Exception as anthropic_exc:
                if not (fallback_enabled() and should_fallback(anthropic_exc) and openai_key):
                    # Re-raise — either not exhaustion-class, fallback disabled,
                    # or OpenAI not configured. Outer try/except in this fn
                    # catches + returns None → judge disabled for this call.
                    raise
                cb_record_exhaustion()
                logger.warning(
                    "[gate] LLM judge Anthropic %s → OpenAI fallback: %s",
                    type(anthropic_exc).__name__,
                    str(anthropic_exc)[:120],
                )
                raw = call_openai_fallback(
                    judge_system,
                    signals,
                    max_tokens=80,
                    temperature=0.0,
                    api_key=openai_key,
                )
            else:
                cb_record_success()
                # Cost tracking — match the pattern used elsewhere
                try:
                    from genlab_core.intelligence.cost_accumulator import (
                        record_anthropic_usage,
                    )

                    record_anthropic_usage(model, response)
                except Exception:
                    pass  # Cost tracking failures MUST NOT block the judge

                raw = response.content[0].text.strip() if response.content else ""

        raw = raw.strip() if raw else ""
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        import json as _json

        parsed = _json.loads(raw)
        llm_approved = bool(parsed.get("approved", rule_decision.approved))
        llm_reason = str(parsed.get("reason", "")).strip()[:200] or "no_reason"

        # Audit-trail entry in the reasons list — operators can grep
        # for "[LLM judge]" in dashboard previews to see where the
        # judge overrode the rule-based decision.
        marker = "OVERRIDE" if llm_approved != rule_decision.approved else "AGREE"
        audit_entry = f"[LLM judge {marker}] {llm_reason}"

        logger.info(
            "[gate] LLM judge fired for niche=%s rule_decision=%s rule_conf=%.2f "
            "llm_decision=%s reason=%s",
            niche_id,
            rule_decision.approved,
            rule_decision.confidence,
            llm_approved,
            llm_reason,
        )

        return AutoApprovalDecision(
            approved=llm_approved,
            # Bump confidence to 0.85 when LLM agrees, 0.7 when it
            # overrides — the LLM verdict on borderline cases is
            # higher-confidence than the rule-based 0.3..0.7 range.
            confidence=0.85 if llm_approved == rule_decision.approved else 0.7,
            passed_checks=rule_decision.passed_checks,
            failed_checks=rule_decision.failed_checks,
            reasons=[*rule_decision.reasons, audit_entry],
        )
    except Exception as exc:
        # WARNING (not DEBUG): AUTO #2 enforcement now depends on the
        # LLM judge for borderline cases; chronic silent failure would
        # invisibly shift the enforcement mix back to rule-only. Any
        # failure (API error, JSON parse, etc.) → return None so the
        # caller uses the unchanged rule-based decision.
        logger.warning("[gate] LLM judge failed (using rule-based): %s", exc)
        return None
