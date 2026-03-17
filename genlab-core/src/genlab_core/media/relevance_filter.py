"""Post-fetch content relevance filter.

Scores video candidates against niche-specific keyword lists and rejects
off-niche content. Uses positive keyword overlap scoring with negative
keyword hard-reject.

Config lives in each niche's sources.yaml under content_filter:
    content_filter:
      relevance_threshold: 0.3
      positive_keywords: [anime, manga, ...]
      negative_keywords: [mma, ufc, ...]
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RelevanceFilter:
    """Score and filter video candidates for niche relevance.

    Positive keywords contribute to a relevance score (0.0-1.0).
    Any negative keyword match triggers an immediate hard reject (score=0.0).
    Candidates below ``relevance_threshold`` are removed.
    """

    def __init__(self, niche_id: str, config: dict[str, Any]) -> None:
        self.niche_id = niche_id
        self.positive_keywords = [k.lower() for k in config.get("positive_keywords", [])]
        self.negative_keywords = [k.lower() for k in config.get("negative_keywords", [])]
        self.threshold = config.get("relevance_threshold", 0.3)

    def score(self, title: str, description: str = "") -> float:
        """Score relevance 0.0-1.0. Returns 0.0 on negative keyword match."""
        text = f"{title} {description}".lower()

        # Hard reject on negative keywords
        for neg in self.negative_keywords:
            if neg in text:
                return 0.0

        # No positive keywords configured — everything passes
        if not self.positive_keywords:
            return 1.0

        # Positive keyword overlap scoring
        # Score based on how many keywords match, normalized so 1-2 hits
        # is enough to pass typical thresholds (0.20-0.35).
        # Cap denominator at 3 so even large keyword lists are forgiving.
        hits = sum(1 for kw in self.positive_keywords if kw in text)
        denominator = min(max(len(self.positive_keywords) * 0.15, 1), 3)
        return min(1.0, hits / denominator)

    def filter(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter candidates, attaching relevance_score to each. Returns kept list."""
        kept: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for v in candidates:
            s = self.score(v.get("title", ""), v.get("description", ""))
            v["relevance_score"] = s
            if s >= self.threshold:
                kept.append(v)
            else:
                rejected.append(v)

        if rejected:
            logger.info(
                "[RelevanceFilter:%s] Rejected %d/%d candidates (threshold=%.2f): %s",
                self.niche_id,
                len(rejected),
                len(candidates),
                self.threshold,
                [r.get("title", "?")[:50] for r in rejected[:5]],
            )

        return kept
