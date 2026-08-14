"""LLM parser: strategist testable_prediction → ExperimentSpec.

Motivating chain:
* auto_promote_hypotheses (2026-07-23 morning): 12 medium/high
  causal_hypotheses now live in learning_findings, feeding the writer.
* auto_experiment scaffold (2026-07-23 evening): auto_experiments
  table + queue/start/check/complete lifecycle in place, but nothing
  populates the queue.
* This module (2026-07-23 late): parse each hypothesis'
  ``testable_prediction`` field into a structured ExperimentSpec so
  the scheduler can queue + run + measure the experiment
  automatically. Operator reviews OUTCOMES not hypotheses — much
  smaller cognitive load.

Discipline
==========

* **Confidence filter mirrors the sibling promoter.** Medium/high
  only. Low-confidence hypotheses stay for operator review.
* **Strict JSON output.** The prompt asks the LLM to return a fixed
  schema. Parse failures → None (skip). Any downstream code sees
  either a valid ExperimentSpec or nothing.
* **classify_llm_error attribution.** Credit exhaustion, rate limit,
  auth errors are all recognised — the caller can short-circuit the
  batch on fatal errors instead of burning through 20 hypotheses
  against a dead API.
* **Idempotent at queue-write.** Callers use
  ``queue_pending_experiment`` which has ON CONFLICT DO NOTHING on
  (source_report_id, hypothesis_index). Re-running is a no-op.

Prompt shape
============

System: "You are an experiment-spec parser..."
User: The testable_prediction text + niche_id + list of existing arm_ids

Returns strict JSON:
    {"arms": ["control", "treatment"], "niche_id": "gaming",
     "expected_metric_shift": 0.05, "duration_days": 14,
     "notes": "one-line rationale"}

Or on unparseable prediction:
    {"unparseable": true, "reason": "no numeric prediction"}

Model: Claude Haiku 4.5 — cheap, deterministic (temperature=0),
tokens < 200 in/out per call = ~$0.0001.

See:
* [[class-of-bug-signal-loss-through-merged-failure-paths]] —
  classify_llm_error is the standard attribution helper here.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Final

from genlab_core.scheduling.auto_experiment import ExperimentSpec

logger = logging.getLogger(__name__)


# Model — matches the pattern in shadow_reviewer.py and the LLM judge.
# Claude Haiku 4.5 is the cheapest available option; temperature=0
# makes the parse deterministic for the same input.
_MODEL: Final[str] = "claude-haiku-4-5-20251001"
_MAX_TOKENS: Final[int] = 300

# Confidence tiers accepted from the strategist. Mirror
# auto_promote_hypotheses to keep the pipeline coherent.
_ACCEPTED_CONFIDENCES: Final[frozenset[str]] = frozenset({"medium", "high"})


def is_confidence_acceptable(confidence: str) -> bool:
    """Public helper — matches the auto_promote_hypotheses filter."""
    return str(confidence).strip().lower() in _ACCEPTED_CONFIDENCES


def _build_prompt(
    prediction: str,
    niche_id: str,
    existing_arm_ids: list[str],
    hypothesis: str = "",
) -> tuple[str, str]:
    """Return (system, user) prompt strings.

    Module-level so tests can inspect the prompt shape without
    invoking the LLM.
    """
    system = (
        "You are an experiment-spec parser for a bandit-driven social-media "
        "content system. Given a `testable_prediction` describing an A/B "
        "experiment hypothesis, return STRICT JSON matching this schema:\n"
        "{\n"
        '  "arms": ["control_arm_id", "treatment_arm_id"],\n'
        '  "niche_id": "one of ai_creators, anime, gaming, movies, sports",\n'
        '  "expected_metric_shift": FLOAT in [0.0, 1.0],\n'
        '  "duration_days": INT in [7, 30],\n'
        '  "notes": "one-sentence rationale (< 200 chars)"\n'
        "}\n\n"
        "If the prediction lacks a specific numeric target OR clear arm "
        "identities, return {\"unparseable\": true, \"reason\": \"one-line why\"}\n\n"
        "Rules:\n"
        "- STRICT arm requirement: BOTH arms in your response MUST appear "
        "EXACTLY (case-sensitive) in the existing_arm_ids list below. Do NOT "
        "invent new arm names, do NOT reference metric names like "
        "'reward_binary_success' as arms, do NOT append suffixes like "
        "'__tiktok_instagram' to existing arm names. If the prediction "
        "requires an arm that doesn't exist, return "
        "{\"unparseable\": true, \"reason\": \"needs arm X which doesn't exist\"}\n"
        "- expected_metric_shift is the LIFT the prediction targets, not the "
        "absolute value. E.g. 'reward >= 0.20 vs baseline 0.10' → shift 0.10.\n"
        "- duration_days: pick 7 for style/hook experiments, 14 for content_type, "
        "30 for source experiments. Default 14 when unclear.\n"
        "- Return only JSON, no prose."
    )

    # Pass the FULL arm list (up to 100). Previously capped at [:15]
    # which hid most real arms — the LLM couldn't tell which
    # arm_ids actually existed vs which it was inventing.
    arm_list = existing_arm_ids[:100]
    user = (
        f"testable_prediction: {prediction!r}\n\n"
        f"niche_id: {niche_id}\n\n"
        f"existing_arm_ids ({len(arm_list)} of {len(existing_arm_ids)} real bandit arms):\n"
        f"{arm_list}\n\n"
    )
    if hypothesis:
        user += f"parent hypothesis: {hypothesis[:400]!r}\n"
    return system, user


def parse_testable_prediction(
    prediction: str,
    niche_id: str,
    existing_arm_ids: list[str],
    *,
    hypothesis: str = "",
) -> tuple[ExperimentSpec | None, str]:
    """Parse a testable_prediction into an ExperimentSpec.

    Returns (spec, error_reason). On success, spec is populated and
    error_reason is "". On failure, spec is None and error_reason is
    one of:
      - "unparseable" — the LLM said the prediction was too vague
      - "not_configured" — Anthropic API key missing
      - "sdk_missing" — anthropic package not installed
      - "credit_exhausted" / "rate_limit" / "auth" / etc. — from
        classify_llm_error
      - "invalid_json" — LLM returned non-JSON or missing fields
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None, "not_configured"

    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None, "sdk_missing"

    from genlab_core.llm.errors import classify_llm_error

    system, user = _build_prompt(prediction, niche_id, existing_arm_ids, hypothesis)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            temperature=0.0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = response.content[0].text.strip() if response.content else ""
    except Exception as exc:
        reason = classify_llm_error(exc)
        logger.warning(
            "[experiment_parser] LLM call failed (reason=%s): %s",
            reason,
            exc,
        )
        return None, reason

    # Strip code fences.
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "[experiment_parser] JSON parse failed — raw=%r", raw[:200]
        )
        return None, "invalid_json"

    # Unparseable-by-LLM case.
    if isinstance(parsed, dict) and parsed.get("unparseable"):
        return None, f"unparseable:{parsed.get('reason', 'no_reason')}"

    # Validate required fields.
    if not isinstance(parsed, dict):
        return None, "invalid_json"
    arms = parsed.get("arms")
    if not isinstance(arms, list) or len(arms) < 2:
        return None, "invalid_json:missing_arms"
    try:
        shift = float(parsed.get("expected_metric_shift", 0.0))
    except (TypeError, ValueError):
        return None, "invalid_json:non_numeric_shift"
    shift = max(0.0, min(1.0, shift))
    try:
        duration = int(parsed.get("duration_days", 14))
    except (TypeError, ValueError):
        duration = 14
    duration = max(7, min(30, duration))

    spec = ExperimentSpec(
        arms=[str(a).strip() for a in arms if isinstance(a, (str, int, float)) and str(a).strip()],
        niche_id=str(parsed.get("niche_id", niche_id)).strip() or niche_id,
        expected_metric_shift=shift,
        duration_days=duration,
        notes=str(parsed.get("notes", ""))[:400],
    )

    # Post-parse validation (2026-08-14 follow-up): reject specs
    # whose arms aren't in the real bandit arm list. This is the
    # belt to the prompt's suspenders — even with the tightened
    # prompt above, LLMs occasionally hallucinate; validation is
    # the hard stop. Discovered when the Phase 3.D analyzer showed
    # 30 of 42 running experiments had zero samples because
    # their arm_ids didn't exist in bandit_arms.
    #
    # Skip the check if the caller didn't pass any existing arms
    # (test / cold-start case) — otherwise we'd reject every spec.
    if existing_arm_ids:
        real = frozenset(existing_arm_ids)
        invalid = [a for a in spec.arms if a not in real]
        if invalid:
            logger.warning(
                "[experiment_parser] LLM invented arm_ids not in bandit: %s "
                "(niche=%s, spec.arms=%s) — rejecting spec",
                invalid, niche_id, spec.arms,
            )
            return None, f"invalid_arms:{','.join(invalid)[:100]}"

    return spec, ""


__all__ = [
    "is_confidence_acceptable",
    "parse_testable_prediction",
]
