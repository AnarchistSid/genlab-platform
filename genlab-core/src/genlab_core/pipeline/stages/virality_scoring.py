"""Pipeline stage: Score blueprints for viral potential.

Deterministic feature extraction — no LLM calls. Examines hook text and body
for 9 virality signals, then computes a weighted score using niche config
(config/scoring_weights.yaml → virality_scoring section).

Features:
  1. hook_format_question — Opens with a question
  2. pop_culture_reference — Names a known entity/brand
  3. named_tool — References a specific tool/product
  4. nostalgia_angle — "Remember when", throwback language
  5. dollar_amount — Mentions money ($X, revenue, funding)
  6. before_after — Transformation framing
  7. controversy_debate — Polarizing language
  8. tutorial_how_to — Educational framing
  9. listicle_number — "Top 5", "3 ways", etc.

Non-fatal: scoring failures leave virality_score at 0.0.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)

# ── Feature detectors ─────────────────────────────────────────
#
# DEFAULT_PATTERNS below are the historical AI-biased patterns (they were
# originally written for the ai_creators niche before the pipeline went
# multi-niche). Per-niche overrides are supported via niche_config:
#
#   virality_scoring:
#     patterns:
#       pop_culture_reference: "\\b(nba|nfl|lakers|barcelona|...)\\b"
#       named_tool: "\\b(ps5|xbox|switch|unreal|...)\\b"
#     weights:
#       listicle_number: 0.15
#
# Any key not present in the per-niche override falls back to DEFAULT_PATTERNS.
# This lets each niche ship its own vocabulary without touching code.

DEFAULT_PATTERNS: dict[str, str] = {
    "hook_format_question": r"^(what|why|how|when|where|who|which|is|are|do|does|can|could|will|would|should|did)\b",
    "pop_culture_reference": r"\b(openai|anthropic|google|meta|apple|nvidia|tesla|microsoft|amazon|netflix|disney|marvel)\b",
    "named_tool": r"\b(chatgpt|claude|gemini|copilot|midjourney|dall[- ]?e|sora|cursor|devin|v0|bolt|replit|figma|notion)\b",
    "nostalgia_angle": r"\b(remember when|throwback|used to|back in|before [\w]+ existed|old school)\b",
    "dollar_amount": r"\$\s?\d+|\b\d+[BMK]\b|\bfunding\b|\brevenue\b|\bvaluation\b",
    "before_after": r"\b(before|after|vs\.?|versus|compared to|transformation|went from)\b",
    "controversy_debate": r"\b(controversial|debate|backlash|outrage|divided|drama|scandal|accused|fired|banned|cancelled)\b",
    "tutorial_how_to": r"\b(how to|tutorial|step[- ]by[- ]step|guide|learn|beginner|masterclass|tips|hack|trick)\b",
    "listicle_number": r"\b(top\s+\d+|\d+\s+(ways|reasons|tips|tools|things|mistakes|secrets|hacks|steps|rules|signs))\b",
}

# Default weights (overridden by niche_config scoring_weights.virality_scoring)
DEFAULT_WEIGHTS: dict[str, float] = {
    "hook_format_question": 0.12,
    "pop_culture_reference": 0.10,
    "named_tool": 0.15,
    "nostalgia_angle": 0.08,
    "dollar_amount": 0.10,
    "before_after": 0.10,
    "controversy_debate": 0.12,
    "tutorial_how_to": 0.13,
    "listicle_number": 0.10,
}


def _compile_patterns(overrides: dict[str, str] | None) -> dict[str, re.Pattern[str]]:
    """Merge niche-specific pattern overrides with defaults and compile.

    Invalid regex patterns in a niche override log a warning and fall back
    to the default for that feature — a bad config should never crash
    the pipeline.
    """
    merged = dict(DEFAULT_PATTERNS)
    if overrides:
        for name, pat in overrides.items():
            if name not in DEFAULT_PATTERNS:
                logger.warning(
                    "[ViralityScoring] Unknown pattern key in niche override: %s",
                    name,
                )
                continue
            if not isinstance(pat, str) or not pat:
                continue
            merged[name] = pat
    compiled: dict[str, re.Pattern[str]] = {}
    for name, pat in merged.items():
        try:
            compiled[name] = re.compile(pat, re.I)
        except re.error as exc:
            logger.warning(
                "[ViralityScoring] Invalid regex for %s (%s) — using default",
                name,
                exc,
            )
            compiled[name] = re.compile(DEFAULT_PATTERNS[name], re.I)
    return compiled


class ViralityScoring:
    """Score stories for viral potential using deterministic text features.

    Reads: context['stories'], context['niche_config']
    Writes: context['stories'][*]['virality_score'], context['stories'][*]['virality_features']
    """

    def execute(self, context: StageContext) -> StageContext:
        blueprints = context.get("stories", [])
        if not blueprints:
            logger.info("[ViralityScoring] No stories to score")
            return context

        config = context.get("niche_config", {})
        virality_cfg = config.get("virality_scoring") or (
            config.get("scoring_weights", {}).get("virality_scoring", {})
        )
        if not isinstance(virality_cfg, dict):
            virality_cfg = {}

        # 2026-07-21: defensive WARN when a non-ai_creators niche falls
        # back to DEFAULT_PATTERNS. Silent fallback here caused months
        # of virality_score=0.0 on sports/gaming/movies/anime → blueprints
        # gate-rejected → stuck at VISUAL_READY. DEFAULT_PATTERNS are
        # AI-industry vocabulary; only ai_creators content matches them.
        # This WARN alerts operators immediately when a new niche is
        # added without its own virality_scoring section (rule #17 sibling
        # — never elevate silent fallback to fail-open without a log).
        patterns_override = virality_cfg.get("patterns")
        if not patterns_override:
            niche_id = context.get("niche_id", "unknown")
            if niche_id != "ai_creators":
                logger.warning(
                    "[ViralityScoring] niche=%s has NO virality_scoring.patterns "
                    "override — falling back to AI-industry DEFAULT_PATTERNS "
                    "which will not match this niche's vocabulary. Every "
                    "blueprint will likely score 0.0 and be gate-rejected. "
                    "Add a virality_scoring: section to the niche's "
                    "scoring_weights.yaml. See ClutchWire/config/"
                    "scoring_weights.yaml for the reference shape.",
                    niche_id,
                )

        weights = virality_cfg.get("weights") or (
            virality_cfg
            if all(isinstance(v, (int, float)) for v in virality_cfg.values())
            else DEFAULT_WEIGHTS
        )
        patterns = _compile_patterns(patterns_override)

        scored = 0
        total_score = 0.0

        for bp in blueprints:
            try:
                features, score = self._score(bp, weights, patterns)
                bp["virality_score"] = round(score, 4)
                bp["virality_features"] = features
                scored += 1
                total_score += score
            except Exception:
                # 2026-07-14 (scoring audit F1): set None instead of 0.0.
                # auto_approval_gate treats missing/None as "unknown"
                # (cold-start tolerance) but 0.0 as "failed virality
                # gate" — a broken regex compile / KeyError distorted
                # the gate verdict as "high confidence reject" instead
                # of "unknown, defer." Same YT #578 class-of-bug: silent
                # sentinel indistinguishable from real signal. Also
                # unify virality_features to [] (list) so downstream
                # json.dumps sees a stable shape.
                logger.exception(
                    "[ViralityScoring] Error scoring %s — setting virality_score=None (defer)",
                    bp.get("candidate_id", "unknown"),
                )
                bp["virality_score"] = None
                bp["virality_features"] = []

        avg = total_score / scored if scored else 0
        logger.info(
            "[ViralityScoring] Scored %d blueprints, avg=%.3f",
            scored,
            avg,
        )

        context.setdefault("run_stats", {})["virality"] = {
            "scored": scored,
            "avg_score": round(avg, 4),
        }

        return context

    @staticmethod
    def _extract_features(
        text: str,
        patterns: dict[str, re.Pattern[str]],
    ) -> list[tuple[str, bool]]:
        """Extract all binary features from text using the active pattern set."""
        return [(name, bool(pat.search(text))) for name, pat in patterns.items()]

    def _score(
        self,
        bp: dict[str, Any],
        weights: dict[str, float],
        patterns: dict[str, re.Pattern[str]],
    ) -> tuple[list[str], float]:
        content = bp.get("content") or {}
        hook = content.get("hook") or bp.get("hook", "")
        body = content.get("caption") or bp.get("body") or bp.get("caption", "")
        title = bp.get("title", "")
        text = " ".join(s for s in (hook, body, title) if isinstance(s, str) and s)

        features = self._extract_features(text, patterns)
        matched = [name for name, hit in features if hit]
        score = sum(weights.get(name, 0.1) for name in matched)

        # Clamp to [0, 1]
        score = min(1.0, max(0.0, score))

        return matched, score
