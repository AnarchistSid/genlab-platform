"""Post-publish root-cause analyzer for underperforming posts (Lever O).

Pre-2026-06-21 when a post bombed (engagement <30% of niche baseline)
the bandit treated it as negative reward — alpha+=0, beta+=1 — and
moved on. No analysis of WHY the post failed: was it the hook (generic
template)? The thumbnail (low-contrast)? Platform algorithm timing?
Niche fit? Operators learned nothing actionable from each failure.

Lever O ships an LLM-as-judge layer that runs on bombed posts.
Claude Haiku compares the bombed post against the same-niche recent
winners + the niche baseline + the post's own feature vector, then
returns a structured RCA verdict + recommendation. Pattern mirrors
Lever C (LLM-judge on borderline) and Lever K (hook critic).

Opt-in via ``GENLAB_POST_RCA_ENABLED=1`` env for safe shadow rollout.
Default disabled = no behavior change.

Data path:
1. Caller (typically metric_collector at the 48h reward window)
   detects underperformance via ``is_underperforming``
2. Caller builds the comparison set via ``build_rca_context``
3. Caller invokes ``analyze_post`` which returns an RCAResult
4. Caller persists / surfaces the result (future PR adds DB table)

Cost: ~1 Haiku call per bomb event. Bombs are ~10-20% of publishes
× ~1 publish/day × 5 niches = ~10 bombs/week = ~$0.001/week.

Run via:
    python -m genlab_core.learning.post_rca --help
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

logger = logging.getLogger(__name__)


# Default threshold: a post is "underperforming" when its engagement
# rate falls below ``baseline_rate * (1 - threshold)``. So with
# threshold=0.30, a post needs to underperform by 30%+ vs baseline
# to qualify for RCA. Tunable per-caller.
_DEFAULT_UNDERPERFORMANCE_THRESHOLD: Final[float] = 0.30

# Minimum baseline rate before underperformance is meaningful — without
# this, a niche with baseline=0.005 (5 engagements per 1000 views) would
# flag any post with baseline=0.003 as a 40% miss, which is just noise.
_MIN_BASELINE_FOR_RCA: Final[float] = 0.01


@dataclass(frozen=True)
class RCAResult:
    """Structured output from the post-bomb RCA layer.

    The ``likely_cause`` field is one of a small enumerated set so
    downstream aggregation (future trend dashboard) can group + count.
    The ``recommendation`` field is free-form for operator readability.
    """

    blueprint_id: str
    niche_id: str
    likely_cause: (
        str  # "weak_hook" | "off_niche" | "timing" | "thumbnail" | "platform_drift" | "unknown"
    )
    confidence: float  # 0..1, LLM's self-reported confidence
    reasoning: str  # one-sentence explanation
    recommendation: str  # one-sentence action operator could take
    computed_at: str


def is_underperforming(
    post_engagement_rate: float,
    niche_baseline_rate: float,
    *,
    threshold: float = _DEFAULT_UNDERPERFORMANCE_THRESHOLD,
) -> bool:
    """Pure-function decision: is this post bad enough to RCA?

    Returns True when the post's engagement rate is below
    ``baseline_rate * (1 - threshold)`` AND the baseline itself is
    above ``_MIN_BASELINE_FOR_RCA`` (so we don't RCA niches with too
    little data to compare against meaningfully).

    Examples (default threshold=0.30):
        baseline=0.05, post=0.03 → 40% miss → True
        baseline=0.05, post=0.04 → 20% miss → False (below threshold)
        baseline=0.005, post=0.003 → 40% miss → False (baseline too small)
    """
    if niche_baseline_rate < _MIN_BASELINE_FOR_RCA:
        return False
    if post_engagement_rate < 0 or niche_baseline_rate <= 0:
        return False
    miss_pct = (niche_baseline_rate - post_engagement_rate) / niche_baseline_rate
    return miss_pct >= threshold


def build_rca_context(
    bombed_post: dict[str, Any],
    niche_winners: list[dict[str, Any]],
    niche_baseline_rate: float,
) -> dict[str, Any]:
    """Assemble the LLM input from the bombed post + reference set.

    Pure function (no I/O). Returns a structured dict the LLM caller
    can serialize into a prompt. Splitting context-building from the
    LLM call lets us test the prompt shape without mocking Anthropic.

    ``niche_winners`` should be 3-5 recent high-performing posts in
    the same niche — gives the LLM concrete examples to compare against.
    """
    return {
        "bombed": {
            "blueprint_id": bombed_post.get("blueprint_id", ""),
            "niche_id": bombed_post.get("niche_id", ""),
            "hook": (bombed_post.get("hook_text") or "")[:200],
            "title": (bombed_post.get("title") or "")[:200],
            "platform": bombed_post.get("platform", ""),
            "published_at": bombed_post.get("published_at", ""),
            "engagement_rate": bombed_post.get("engagement_rate", 0.0),
        },
        "baseline_rate": niche_baseline_rate,
        "winners": [
            {
                "hook": (w.get("hook_text") or "")[:200],
                "title": (w.get("title") or "")[:200],
                "engagement_rate": w.get("engagement_rate", 0.0),
            }
            for w in niche_winners[:5]
        ],
    }


_RCA_SYSTEM_PROMPT = """You analyze why a social media post underperformed.

You see:
- The bombed post (hook, title, platform, engagement rate)
- The niche's baseline engagement rate
- 3-5 recent winners in the same niche for comparison

Diagnose the most likely cause. Return ONLY a JSON object:
{
  "likely_cause": "<one of: weak_hook | off_niche | timing | thumbnail | platform_drift | unknown>",
  "confidence": <float 0..1>,
  "reasoning": "<one-sentence explanation citing specific differences>",
  "recommendation": "<one-sentence action>"
}

Categories:
- weak_hook: hook is generic, templated, or doesn't match winner-hook patterns
- off_niche: content topic doesn't fit the niche's typical winners
- timing: post timing seems off (very recent, wrong day/hour patterns)
- thumbnail: visual signals suggest thumbnail/cover image issue
- platform_drift: post seems algo-suppressed (good content but no reach)
- unknown: insufficient evidence to attribute confidently

Use the lowest-cost category that fits — don't reach for `platform_drift`
unless winners and bombed post are visually + content-similar.
"""


def analyze_post(
    bombed_post: dict[str, Any],
    niche_winners: list[dict[str, Any]],
    niche_baseline_rate: float,
) -> RCAResult | None:
    """Run the LLM-driven RCA on a bombed post.

    Returns ``None`` when:
    - Opt-in env flag ``GENLAB_POST_RCA_ENABLED`` is not "1"
    - No ANTHROPIC_API_KEY
    - LLM call fails (network, parsing, etc.)

    Fail-open by design: pipeline / metric_collector calls this
    best-effort; a failure must never block reward updates or
    downstream learning.

    Cost: ~1 Haiku call per invocation (~$0.0002). Caller decides
    when to invoke (typically only on ``is_underperforming==True``).
    """
    if os.environ.get("GENLAB_POST_RCA_ENABLED", "0") != "1":
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    try:
        import anthropic
    except ImportError:
        return None

    context = build_rca_context(bombed_post, niche_winners, niche_baseline_rate)
    blueprint_id = str(context["bombed"].get("blueprint_id") or "")
    niche_id = str(context["bombed"].get("niche_id") or "")

    try:
        # U-01: route through prompt-cache helper. The RCA system
        # prompt is ~1000 chars today (below the 4000-char default
        # threshold) so the helper passes it through as plain string
        # — no behavior change. Wired now so the call site is future-
        # proof if the prompt grows or the threshold drops.
        from genlab_core.llm.prompt_cache import with_prompt_cache

        # 2026-07-21: OpenAI fallback on Anthropic exhaustion.
        from genlab_core.llm.fallback import (
            call_openai_fallback as _call_openai_fallback,
            cb_is_open as _cb_is_open,
            cb_record_exhaustion as _cb_record_exhaustion,
            cb_record_success as _cb_record_success,
            fallback_enabled as _fallback_enabled,
            should_fallback as _should_fallback,
        )

        user = json.dumps(context, indent=2)
        _openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
        raw = ""

        if _fallback_enabled() and _cb_is_open() and _openai_key:
            raw = _call_openai_fallback(
                _RCA_SYSTEM_PROMPT, user, 200, 0.0, _openai_key
            )
        else:
            try:
                client = anthropic.Anthropic(api_key=api_key)
                response = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=200,
                    temperature=0.0,  # Deterministic verdict for same inputs
                    system=with_prompt_cache(_RCA_SYSTEM_PROMPT),
                    messages=[{"role": "user", "content": user}],
                )
            except Exception as anthropic_exc:
                if _fallback_enabled() and _should_fallback(anthropic_exc) and _openai_key:
                    _cb_record_exhaustion()
                    # 2026-07-23: classify_llm_error attributes the failure.
                    from genlab_core.llm.errors import classify_llm_error

                    logger.warning(
                        "[post_rca] Anthropic failed (reason=%s) → OpenAI fallback",
                        classify_llm_error(anthropic_exc),
                    )
                    raw = _call_openai_fallback(
                        _RCA_SYSTEM_PROMPT, user, 200, 0.0, _openai_key
                    )
                else:
                    raise
            else:
                _cb_record_success()
                # Cost tracking — match the pattern in other LLM call sites
                try:
                    from genlab_core.intelligence.cost_accumulator import (
                        record_anthropic_usage,
                    )

                    record_anthropic_usage("claude-haiku-4-5-20251001", response)
                except Exception:
                    pass  # cost tracking failures must NEVER block the RCA

                raw = response.content[0].text.strip() if response.content else ""

        raw = raw.strip() if raw else ""
        if raw.startswith("```"):
            raw = raw.strip("`").strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()

        parsed = json.loads(raw)
        likely_cause = str(parsed.get("likely_cause", "unknown")).strip()[:32] or "unknown"
        # Whitelist categories so a free-text LLM response can't
        # poison downstream aggregations.
        if likely_cause not in {
            "weak_hook",
            "off_niche",
            "timing",
            "thumbnail",
            "platform_drift",
            "unknown",
        }:
            likely_cause = "unknown"

        confidence = float(parsed.get("confidence", 0.5) or 0.5)
        confidence = max(0.0, min(1.0, confidence))  # clamp to [0, 1]

        reasoning = str(parsed.get("reasoning", "")).strip()[:240] or "no_reasoning"
        recommendation = str(parsed.get("recommendation", "")).strip()[:240] or "no_recommendation"

        logger.info(
            "[post_rca] bp=%s niche=%s cause=%s conf=%.2f reason=%s",
            blueprint_id,
            niche_id,
            likely_cause,
            confidence,
            reasoning[:80],
        )

        return RCAResult(
            blueprint_id=blueprint_id,
            niche_id=niche_id,
            likely_cause=likely_cause,
            confidence=round(confidence, 3),
            reasoning=reasoning,
            recommendation=recommendation,
            computed_at=datetime.now(UTC).isoformat(),
        )
    except (json.JSONDecodeError, KeyError, AttributeError, IndexError) as exc:
        logger.debug("[post_rca] malformed LLM response: %s", exc)
        return None
    except Exception as exc:
        # API error, network error, etc. — fail open.
        logger.debug("[post_rca] LLM call failed: %s", exc)
        return None


if __name__ == "__main__":
    # CLI entry — operators can manually RCA a single bombed post
    # via this entry point. The wiring into metric_collector auto-trigger
    # is a separate PR that lets operators see RCA value before paying
    # for it on every bomb.
    import argparse

    parser = argparse.ArgumentParser(description="Post-bomb root-cause analyzer (Lever O)")
    parser.add_argument(
        "--enabled-check", action="store_true", help="Just print whether opt-in env is set"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.enabled_check:
        from genlab_core.settings import env_true

        enabled = env_true("GENLAB_POST_RCA_ENABLED")
        print(f"GENLAB_POST_RCA_ENABLED: {enabled}")
        if not enabled:
            print("Set GENLAB_POST_RCA_ENABLED=1 to activate the RCA layer.")
    else:
        print("Use --enabled-check to verify opt-in env state.")
        print("Programmatic usage:")
        print("  from genlab_core.learning.post_rca import analyze_post, is_underperforming")
