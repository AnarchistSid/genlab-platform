"""Shadow reviewer: LLM pass that produces a would-approve verdict
for a scheduled blueprint.

Motivating problem: the AUTO #2 enforcement ratchet needs ≥30 fresh
calibration samples per niche at ≥90% agreement to widen enrollment.
Fresh samples come from ``calibration_logger.log()`` — which fires
only on operator dashboard clicks. The nightly_scheduler auto-approves
90% of throughput, so operator rarely opens the review queue → 24 days
since the last calibration write as of 2026-07-23.

Solution: a scheduled shadow reviewer runs an LLM pass on every
VISUAL_READY + scheduled blueprint that hasn't been shadow-reviewed
yet. It produces a would-approve verdict + confidence + short
explanation. Writes to ``auto_approval_calibration`` with
``source='shadow_reviewer'`` — a new column added by the paired
Alembic migration (2026-07-23) that keeps the LLM's verdicts
distinguishable from real operator clicks.

Discipline
==========

* **Never confuses shadow with operator.** The migration + logger
  changes tag every shadow write with ``source='shadow_reviewer'``.
  The ``stats()`` reader gets a ``source_filter`` param that defaults
  to ``'operator'`` — so pre-existing enrollment logic that reads
  agreement rate keeps looking at operator signal only.
* **Fail-open per blueprint.** LLM errors are attributed via
  ``classify_llm_error`` and swallowed; the runner continues to the
  next blueprint. A cold Anthropic outage produces zero shadow rows
  that day — no fake data.
* **Idempotent.** Each blueprint is shadow-reviewed at most once:
  ``SELECT 1 FROM auto_approval_calibration WHERE blueprint_id = %s
  AND source = 'shadow_reviewer'`` guards insert.
* **Flag-gated.** ``GENLAB_SHADOW_REVIEWER_ENABLED`` must be exactly
  ``"true"``/``"TRUE"``/``"True"`` — same strict pattern as the other
  ancillary intelligence flags. Off by default until first-week
  eyeballing confirms verdicts look sane.

Prompt shape
============

The prompt asks the LLM to answer as if it were a professional
social-media manager evaluating whether a blueprint is ready to ship.
Structured JSON output (`{"would_approve": bool, "confidence": float,
"reason": str}`) so parse failures fall through to
``LLM_ERROR_INVALID_REQUEST`` cleanly.

See:
* ``[[class-of-bug-signal-loss-through-merged-failure-paths]]``
* Rule #22: "Never treat 'agreement %' alone as calibration signal —
  always look at the confusion matrix." The shadow source column lets
  the confusion matrix be computed per-source, so shadow's high
  agreement rate (if it happens) doesn't accidentally trigger
  enrollment for the operator signal path.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Final

logger = logging.getLogger(__name__)

# Flag: same strict-match pattern as GENLAB_LINUCB_STOCHASTIC_ENABLED
# (see linucb.py:_temporal_context_enabled for the discipline rationale).
_ENABLE_ENV_VAR: Final[str] = "GENLAB_SHADOW_REVIEWER_ENABLED"

# Source tag persisted to auto_approval_calibration.source
SHADOW_SOURCE_TAG: Final[str] = "shadow_reviewer"


def is_enabled() -> bool:
    """Return True iff the shadow reviewer is authorised to run.

    Strict exact-match on 'true'/'TRUE'/'True' — matches the pattern
    used by GENLAB_LINUCB_STOCHASTIC_ENABLED / GENLAB_TEMPORAL_CONTEXT_
    ENABLED / GENLAB_TREND_ANTICIPATION_ENABLED. Do NOT migrate to
    env_true — the strict pattern prevents the "1"/"yes"/"on" ambiguity
    that bit the AUTO #2 rollout (see auto_approval_gate.py rollout
    log for the same lesson)."""
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


@dataclass(frozen=True)
class ShadowVerdict:
    """Output of a single shadow review pass.

    Fields
    ------
    would_approve : bool
        The LLM's yes/no verdict on whether the blueprint is ready to
        ship as-is.
    confidence : float in [0, 1]
        The LLM's self-reported confidence. 0.5 = coin-flip; caller
        may downgrade to 'skip' below a threshold to avoid noisy
        writes.
    reason : str
        Human-readable short explanation. Persisted to the reason
        field so operators reviewing the shadow's disagreements can
        see the rationale.
    error_reason : str
        "" on success. Otherwise a classify_llm_error category
        ('credit_exhausted', 'rate_limit', etc.) — the caller uses
        this to decide whether to log-and-skip or short-circuit the
        whole batch (e.g. don't burn through 50 blueprints when the
        API is 401).
    """

    would_approve: bool
    confidence: float
    reason: str
    error_reason: str = ""

    @property
    def is_error(self) -> bool:
        return bool(self.error_reason)


def _build_prompt(blueprint: dict[str, Any]) -> tuple[str, str]:
    """Return (system, user) prompt for the shadow review LLM call.

    Kept as a module-level function so tests can inspect the prompt
    shape without invoking the LLM.
    """
    niche_id = blueprint.get("niche_id", "unknown")
    hook = str(blueprint.get("hook") or blueprint.get("hook_text") or "")[:200]
    extra = blueprint.get("extra") if isinstance(blueprint.get("extra"), dict) else {}
    composite = extra.get("composite_score")
    virality = extra.get("virality_score")
    duration = extra.get("duration_seconds") or extra.get("duration")

    system = (
        "You are a professional short-form video social-media manager. "
        "Evaluate whether the described video reel is READY TO SHIP as-is "
        "to short-form platforms (Instagram Reels, YouTube Shorts, Facebook "
        "Reels, Threads). Answer with STRICT JSON:\n"
        '{"would_approve": true|false, "confidence": 0.0-1.0, "reason": "short 1-sentence rationale"}\n\n'
        "Criteria:\n"
        "- Hook is specific, story-referenced, and under 60 characters.\n"
        "- No generic template phrases ('Something big happened', 'players need to see this').\n"
        "- No LLM-refusal preambles ('I need the...', 'I cannot...').\n"
        "- Bare titles ('Grand Theft Auto V') without a verb/hook signal reject.\n"
        "- Missing hook or empty caption reject.\n"
        "Return only JSON, no explanation before or after."
    )

    user = (
        f"Niche: {niche_id}\n"
        f"Hook: {hook!r}\n"
        f"Composite score: {composite}\n"
        f"Virality score: {virality}\n"
        f"Duration: {duration}s\n"
    )
    return system, user


def evaluate_blueprint(blueprint: dict[str, Any]) -> ShadowVerdict | None:
    """Run one shadow review pass on ``blueprint``.

    Returns:
        ShadowVerdict on success (or on a classified LLM error).
        None when the feature flag is off — caller treats as skip.

    Discipline:
        * When the flag is off, returns None (never writes anything).
        * When Anthropic is misconfigured (no API key), returns
          ShadowVerdict with error_reason='auth' — caller records the
          error to reason field so operator sees WHY shadow stopped
          working, not silent zero-writes.
        * When the LLM returns non-JSON, returns ShadowVerdict with
          error_reason='invalid_request'.
        * When Anthropic 429s or exhausts credit, returns
          ShadowVerdict with error_reason='rate_limit'/'credit_exhausted'.
    """
    if not is_enabled():
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ShadowVerdict(
            would_approve=False,
            confidence=0.0,
            reason="Anthropic API key not configured",
            error_reason="auth",
        )

    try:
        import anthropic  # noqa: F401 — imported for the classifier's benefit
    except ImportError:
        return ShadowVerdict(
            would_approve=False,
            confidence=0.0,
            reason="anthropic SDK not installed",
            error_reason="connection",
        )

    from genlab_core.llm.errors import classify_llm_error

    system, user = _build_prompt(blueprint)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            temperature=0.0,  # Deterministic verdict.
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = response.content[0].text.strip() if response.content else ""
    except Exception as exc:
        reason_cat = classify_llm_error(exc)
        logger.warning(
            "[shadow_reviewer] LLM call failed (reason=%s): %s",
            reason_cat,
            exc,
            exc_info=True,
        )
        return ShadowVerdict(
            would_approve=False,
            confidence=0.0,
            reason=f"LLM error: {reason_cat}",
            error_reason=reason_cat,
        )

    # Strip code-fence artifacts the LLM sometimes emits.
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.startswith("json"):
            raw = raw[4:].strip()

    try:
        parsed = json.loads(raw)
        would_approve = bool(parsed.get("would_approve", False))
        confidence = float(parsed.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))
        reason = str(parsed.get("reason", "")).strip()[:300] or "no_reason"
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning(
            "[shadow_reviewer] JSON parse failed: %s — raw=%r",
            exc,
            raw[:200],
        )
        return ShadowVerdict(
            would_approve=False,
            confidence=0.0,
            reason=f"non-JSON response: {raw[:120]}",
            error_reason="invalid_request",
        )

    return ShadowVerdict(
        would_approve=would_approve,
        confidence=confidence,
        reason=reason,
        error_reason="",
    )


__all__ = [
    "SHADOW_SOURCE_TAG",
    "ShadowVerdict",
    "evaluate_blueprint",
    "is_enabled",
]
