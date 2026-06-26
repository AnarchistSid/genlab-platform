"""Click-rationale taxonomy classifier (Engine 1.4).

The 2026-06-22 autonomy audit noted that the ``feedback_category``
column on ``auto_approval_calibration`` (migration
``x4s5t6u7v8w9_calibration_feedback_category.py``) exists but is
NULL on almost every row. Operators have to manually pick a category
from the review form's dropdown, and most click through without
selecting one — the categorical signal is essentially absent.

This module is the auto-classifier that closes that gap. On every
operator rejection it calls Claude Haiku with a structured prompt
that includes the hook text, the source story summary, and the
niche name; Haiku returns a JSON verdict
``{"category", "confidence", "rationale"}``. The classifier returns
the (category, confidence) pair. The caller (calibration_helper)
writes the category into the calibration row when confidence is
high enough.

Design notes:

- **Opt-in via env flag.** ``GENLAB_RATIONALE_CLASSIFIER_ENABLED=1``
  is required before any Haiku call fires. Default OFF so the
  calibration write path keeps its existing behaviour (NULL
  feedback_category) until the operator explicitly turns this on.
- **Confidence floor 0.7.** Anything below is returned as
  ``("uncategorized", 0.0)`` so the calibration writer leaves the
  column NULL. The operator can still pick a category manually via
  the existing UI dropdown. The threshold is overridable through
  ``GENLAB_RATIONALE_CONFIDENCE_THRESHOLD``.
- **Fail-OPEN throughout.** Any Haiku/JSON/parsing error returns
  ``("uncategorized", 0.0)``. This module MUST NEVER raise to the
  calibration writer — calibration data accumulating SLOWER (because
  classification failed) defeats the entire point of the helper.
- **Whitelisted output.** Haiku is asked to return one of the 6
  canonical categories. If it returns anything outside that set,
  the result is downgraded to ``"uncategorized"`` so downstream
  aggregation (Engine 2.4 rubric synthesis, per-category gate
  tuning) only sees clean enum values.

Cost: ~1 Haiku call per rejection (~$0.0001). At ~3 rejections/day
× 5 niches × 365 days = ~$0.55/year. Well inside the
``<$100/year`` budget set by Engine 1.4.

See docs/AGENT-LEARNING-ENGINES.md §1.4 for the rationale.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Final

logger = logging.getLogger(__name__)


# Canonical 6-category taxonomy. Pinned + locked.
# Order matches the order in docs/AGENT-LEARNING-ENGINES.md §1.4
# AND the order in dashboard/frontend/src/components/review/ ISSUE_OPTIONS,
# AND the order in genlab_core/learning/rejection_rag.py:_CATEGORY_ADVICE.
# Any new category MUST land in all four sites together.
CANONICAL_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "weak_hook",
        "too_generic",
        "unsupported_claim",
        "bad_fit",
        "too_long",
        "low_value",
    }
)

# Sentinel returned when classification cannot produce a high-confidence
# verdict. The calibration writer treats this as "leave feedback_category
# NULL" — matches existing behaviour for callers that don't pass a
# category at all.
UNCATEGORIZED: Final[str] = "uncategorized"

# Defaults — overridable via env.
_DEFAULT_CONFIDENCE_THRESHOLD: Final[float] = 0.7
_HAIKU_MODEL: Final[str] = "claude-haiku-4-5-20251001"

_ENV_ENABLED: Final[str] = "GENLAB_RATIONALE_CLASSIFIER_ENABLED"
_ENV_THRESHOLD: Final[str] = "GENLAB_RATIONALE_CONFIDENCE_THRESHOLD"


# System prompt asks Haiku to produce JSON in a fixed shape. Same pattern
# as post_rca.py — JSON-only output is the cheapest reliable structured
# response from Haiku (Opus-style structured output isn't worth the
# 8x cost premium at this volume).
_CLASSIFIER_SYSTEM_PROMPT = """You categorize WHY an operator rejected a short-form social video blueprint.

You see:
- The proposed hook text (≤60 chars target)
- The first 500 characters of the source story summary
- The niche name (e.g. "gaming", "anime", "ai_creators")

Pick the SINGLE best-fitting category from this fixed set:

- weak_hook: hook is vague, generic, or lacks specificity — could apply to any story
- too_generic: hook uses template patterns ("X just happened", "this is huge", "you won't believe")
- unsupported_claim: hook makes a claim not backed by the source story
- bad_fit: content doesn't match niche conventions (e.g. MMA appearing in anime, finance in gaming)
- too_long: hook exceeds the 60-char platform limit OR feels verbose / front-loaded with context
- low_value: content is technically valid but unlikely to drive engagement (boring, obvious, stale)

Return ONLY a JSON object — no markdown fences, no prose:
{
  "category": "<one of the 6 categories above>",
  "confidence": <float 0..1, your own self-assessed certainty>,
  "rationale": "<one short sentence explaining the choice>"
}

Bias toward `weak_hook` over `low_value` when the issue is hook craft.
Bias toward `bad_fit` over `unsupported_claim` when the topic itself is off-niche.
If multiple categories fit, pick the one a human operator would pick first.
"""


def _read_threshold() -> float:
    """Resolve the confidence threshold from env, fall back to default.

    Returns the default on any parse error — invalid env values must
    never make the classifier accept low-confidence verdicts that
    would pollute the calibration table.
    """
    raw = os.environ.get(_ENV_THRESHOLD)
    if not raw:
        return _DEFAULT_CONFIDENCE_THRESHOLD
    try:
        value = float(raw)
        if 0.0 <= value <= 1.0:
            return value
        logger.warning(
            "[rationale] %s=%r out of [0,1] range, using default %.2f",
            _ENV_THRESHOLD,
            raw,
            _DEFAULT_CONFIDENCE_THRESHOLD,
        )
    except (TypeError, ValueError):
        logger.warning(
            "[rationale] %s=%r not parseable as float, using default %.2f",
            _ENV_THRESHOLD,
            raw,
            _DEFAULT_CONFIDENCE_THRESHOLD,
        )
    return _DEFAULT_CONFIDENCE_THRESHOLD


def _is_enabled() -> bool:
    """Return True only when the env flag is explicitly set to ``"1"``.

    Matches the post_rca opt-in pattern. ``"true"``/``"yes"`` are
    NOT accepted — keeps the activation gesture unambiguous.
    """
    return os.environ.get(_ENV_ENABLED, "0").strip() == "1"


def _extract_hook(blueprint: dict[str, Any]) -> str:
    """Pull the hook text out of a blueprint dict, defensively.

    Blueprints come from two storage backends (Postgres + the legacy
    SharePoint path) with slightly different field layouts. Prefer
    a top-level ``hook_text`` key but fall back to nested
    ``fields.hook_text``. Returns empty string when neither is present
    — the prompt then includes an explicit "(no hook provided)"
    fragment so Haiku still has the niche + summary to work with.
    """
    if not isinstance(blueprint, dict):
        return ""
    direct = blueprint.get("hook_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    fields = blueprint.get("fields")
    if isinstance(fields, dict):
        nested = fields.get("hook_text")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ""


def _extract_summary(blueprint: dict[str, Any]) -> str:
    """Pull the source story summary, trimmed to 500 chars.

    Tries ``story_summary``, ``summary``, and ``story_description`` in
    order (different fetcher backends populate different fields).
    The 500-char cap is the prompt-cost limiter: any longer doesn't
    help Haiku categorize — the first sentences carry the story angle.
    """
    if not isinstance(blueprint, dict):
        return ""
    candidates = ("story_summary", "summary", "story_description")
    for key in candidates:
        direct = blueprint.get(key)
        if isinstance(direct, str) and direct.strip():
            return direct.strip()[:500]
        fields = blueprint.get("fields")
        if isinstance(fields, dict):
            nested = fields.get(key)
            if isinstance(nested, str) and nested.strip():
                return nested.strip()[:500]
    return ""


def _build_user_message(blueprint: dict[str, Any], niche_id: str) -> str:
    """Compose the JSON user message for the Haiku call.

    Separated for testability — `test_includes_niche_name_in_prompt`
    asserts the niche is present without mocking the SDK.
    """
    hook = _extract_hook(blueprint)
    summary = _extract_summary(blueprint)
    payload: dict[str, Any] = {
        "niche": niche_id,
        "hook": hook or "(no hook provided)",
        "source_summary": summary or "(no source summary available)",
    }
    return json.dumps(payload, indent=2)


def _parse_haiku_response(raw: str) -> tuple[str, float]:
    """Parse Haiku's response into (category, confidence).

    Strips optional ```json fences. Returns ``(UNCATEGORIZED, 0.0)``
    on any parse failure or out-of-set category. Confidence is
    clamped to [0, 1]. Whitelisting matches the post_rca pattern —
    free-text LLM output never lands directly in downstream
    aggregations.
    """
    if not raw:
        return (UNCATEGORIZED, 0.0)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        logger.debug("[rationale] non-JSON Haiku response: %r", raw[:120])
        return (UNCATEGORIZED, 0.0)
    if not isinstance(parsed, dict):
        return (UNCATEGORIZED, 0.0)
    category_raw = parsed.get("category")
    confidence_raw = parsed.get("confidence", 0.0)
    category = str(category_raw).strip() if category_raw is not None else ""
    if category not in CANONICAL_CATEGORIES:
        logger.debug(
            "[rationale] Haiku returned out-of-set category %r — downgrading to uncategorized",
            category,
        )
        return (UNCATEGORIZED, 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return (category, confidence)


def classify_rejection(
    blueprint: dict[str, Any],
    niche_id: str,
) -> tuple[str, float]:
    """Classify why an operator rejected this blueprint.

    Returns a ``(category, confidence)`` tuple where ``category`` is
    one of the 6 canonical categories OR ``"uncategorized"`` and
    confidence is in ``[0.0, 1.0]``.

    The contract guarantees:

    - Returns ``(UNCATEGORIZED, 0.0)`` when the env flag is not set
      (today's default) — callers can wire this in without changing
      behaviour until they explicitly enable.
    - Returns ``(UNCATEGORIZED, 0.0)`` when ``ANTHROPIC_API_KEY`` is
      missing (CI, local dev).
    - Returns ``(UNCATEGORIZED, 0.0)`` on any Haiku / parsing /
      network failure (fail-OPEN).
    - Returns ``(UNCATEGORIZED, 0.0)`` when Haiku's self-reported
      confidence falls below the configured threshold (default 0.7).
    - Returns ``(category, confidence)`` only when confidence is at
      or above the threshold AND the category is in the canonical
      set. The calibration writer should only populate
      ``feedback_category`` when ``category != UNCATEGORIZED``.

    Cost: ~$0.0001/call (Haiku, ~200 tokens). Caller decides when
    to invoke (typically only on operator_action == "rejected").
    """
    if not _is_enabled():
        return (UNCATEGORIZED, 0.0)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return (UNCATEGORIZED, 0.0)

    try:
        import anthropic
    except ImportError:
        logger.debug("[rationale] anthropic SDK not installed, skipping classification")
        return (UNCATEGORIZED, 0.0)

    niche = (niche_id or "").strip() or "unknown"
    user_message = _build_user_message(blueprint, niche)
    threshold = _read_threshold()

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=_HAIKU_MODEL,
            max_tokens=200,
            temperature=0.0,  # Deterministic verdict for same inputs.
            system=_CLASSIFIER_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        # Cost tracking — mirrors post_rca.py pattern. Tracker failures
        # must NEVER block the classifier; the calibration write path
        # depends on this returning cleanly.
        try:
            from genlab_core.intelligence.cost_accumulator import record_anthropic_usage

            record_anthropic_usage(_HAIKU_MODEL, response)
        except Exception:
            pass

        raw_text = ""
        if response.content:
            first = response.content[0]
            raw_text = getattr(first, "text", "") or ""

        category, confidence = _parse_haiku_response(raw_text)
        if category == UNCATEGORIZED:
            return (UNCATEGORIZED, 0.0)

        if confidence < threshold:
            logger.info(
                "[rationale] low-confidence verdict (%s @ %.2f < %.2f) — returning uncategorized",
                category,
                confidence,
                threshold,
            )
            return (UNCATEGORIZED, 0.0)

        logger.info(
            "[rationale] niche=%s category=%s confidence=%.2f",
            niche,
            category,
            confidence,
        )
        return (category, round(confidence, 3))
    except Exception as exc:  # noqa: BLE001 — fail-OPEN by design
        # anthropic.APIError, network errors, JSON errors after first
        # successful parse — all collapsed into the same fail-open
        # return. Calibration writer treats this as "no auto-category"
        # and leaves the column NULL, preserving current behaviour.
        logger.debug("[rationale] classification failed: %s", exc)
        return (UNCATEGORIZED, 0.0)
