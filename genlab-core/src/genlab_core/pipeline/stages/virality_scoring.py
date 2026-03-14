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
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ── Feature detectors (compiled once) ────────────────────────

_QUESTION = re.compile(r"^(what|why|how|when|where|who|which|is|are|do|does|can|could|will|would|should|did)\b", re.I)
_POP_CULTURE = re.compile(r"\b(openai|anthropic|google|meta|apple|nvidia|tesla|microsoft|amazon|netflix|disney|marvel)\b", re.I)
_NAMED_TOOL = re.compile(r"\b(chatgpt|claude|gemini|copilot|midjourney|dall[- ]?e|sora|cursor|devin|v0|bolt|replit|figma|notion)\b", re.I)
_NOSTALGIA = re.compile(r"\b(remember when|throwback|used to|back in|before [\w]+ existed|old school)\b", re.I)
_DOLLAR = re.compile(r"\$\s?\d+|\b\d+[BMK]\b|\bfunding\b|\brevenue\b|\bvaluation\b", re.I)
_BEFORE_AFTER = re.compile(r"\b(before|after|vs\.?|versus|compared to|transformation|went from)\b", re.I)
_CONTROVERSY = re.compile(r"\b(controversial|debate|backlash|outrage|divided|drama|scandal|accused|fired|banned|cancelled)\b", re.I)
_TUTORIAL = re.compile(r"\b(how to|tutorial|step[- ]by[- ]step|guide|learn|beginner|masterclass|tips|hack|trick)\b", re.I)
_LISTICLE = re.compile(r"\b(top\s+\d+|\d+\s+(ways|reasons|tips|tools|things|mistakes|secrets|hacks|steps|rules|signs))\b", re.I)

# Default weights (overridden by niche_config scoring_weights.virality_scoring)
DEFAULT_WEIGHTS: Dict[str, float] = {
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


class ViralityScoring:
    """Score blueprints for viral potential using deterministic text features.

    Reads: context['blueprints'], context['niche_config']
    Writes: context['blueprints'][*]['virality_score'], context['blueprints'][*]['virality_features']
    """

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        blueprints = context.get("blueprints", [])
        if not blueprints:
            logger.info("[ViralityScoring] No blueprints to score")
            return context

        config = context.get("niche_config", {})
        weights = (
            config.get("scoring_weights", {}).get("virality_scoring", DEFAULT_WEIGHTS)
        )

        scored = 0
        total_score = 0.0

        for bp in blueprints:
            try:
                features, score = self._score(bp, weights)
                bp["virality_score"] = round(score, 4)
                bp["virality_features"] = features
                scored += 1
                total_score += score
            except Exception:
                logger.exception(
                    "[ViralityScoring] Error scoring %s",
                    bp.get("candidate_id", "unknown"),
                )
                bp["virality_score"] = 0.0
                bp["virality_features"] = []

        avg = total_score / scored if scored else 0
        logger.info(
            "[ViralityScoring] Scored %d blueprints, avg=%.3f", scored, avg,
        )

        context.setdefault("run_stats", {})["virality"] = {
            "scored": scored,
            "avg_score": round(avg, 4),
        }

        return context

    @staticmethod
    def _extract_features(text: str) -> List[Tuple[str, bool]]:
        """Extract all 9 binary features from text."""
        return [
            ("hook_format_question", bool(_QUESTION.search(text))),
            ("pop_culture_reference", bool(_POP_CULTURE.search(text))),
            ("named_tool", bool(_NAMED_TOOL.search(text))),
            ("nostalgia_angle", bool(_NOSTALGIA.search(text))),
            ("dollar_amount", bool(_DOLLAR.search(text))),
            ("before_after", bool(_BEFORE_AFTER.search(text))),
            ("controversy_debate", bool(_CONTROVERSY.search(text))),
            ("tutorial_how_to", bool(_TUTORIAL.search(text))),
            ("listicle_number", bool(_LISTICLE.search(text))),
        ]

    def _score(
        self, bp: Dict[str, Any], weights: Dict[str, float],
    ) -> Tuple[List[str], float]:
        hook = bp.get("hook", "")
        body = bp.get("body", bp.get("caption", ""))
        text = f"{hook} {body}" if isinstance(body, str) else str(hook)

        features = self._extract_features(text)
        matched = [name for name, hit in features if hit]
        score = sum(weights.get(name, 0.1) for name in matched)

        # Clamp to [0, 1]
        score = min(1.0, max(0.0, score))

        return matched, score
