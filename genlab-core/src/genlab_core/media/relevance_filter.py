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
import re
from typing import Any

logger = logging.getLogger(__name__)

# Short keywords (<=3 chars) need word boundary matching to avoid
# false positives like "ona" matching inside "carbonara".
_SHORT_KW_THRESHOLD = 3


class RelevanceFilter:
    """Score and filter video candidates for niche relevance.

    Positive keywords contribute to a relevance score (0.0-1.0).
    Any negative keyword match triggers an immediate hard reject (score=0.0).
    Candidates below ``relevance_threshold`` are removed.

    Keywords <= 3 characters use word boundary matching to avoid
    substring false positives (e.g. "op" inside "operator").
    """

    def __init__(self, niche_id: str, config: dict[str, Any]) -> None:
        self.niche_id = niche_id
        self.positive_keywords = [k.lower() for k in config.get("positive_keywords", [])]
        self.negative_keywords = [k.lower() for k in config.get("negative_keywords", [])]
        self.threshold = config.get("relevance_threshold", 0.3)

        # Pre-compile word boundary regexes for short keywords
        self._short_patterns: dict[str, re.Pattern] = {}
        for kw in self.positive_keywords:
            if len(kw) <= _SHORT_KW_THRESHOLD:
                self._short_patterns[kw] = re.compile(rf"\b{re.escape(kw)}\b")

    def _keyword_in_text(self, kw: str, text: str) -> bool:
        """Check if keyword matches in text, using word boundaries for short keywords."""
        if kw in self._short_patterns:
            return bool(self._short_patterns[kw].search(text))
        return kw in text

    def score(self, title: str, description: str = "") -> float:
        """Score relevance 0.0-1.0. Returns 0.0 on negative keyword match."""
        text = f"{title} {description}".lower()

        # Hard reject on negative keywords.
        # 2026-07-14 (media audit F1): `"" in text` is ALWAYS True in
        # Python, so a stray empty-string entry (e.g. `- ""` or `- " "`
        # from a YAML typo) in any niche's sources.yaml would 0.0-score
        # EVERY candidate — pipeline goes empty with no clear reason.
        # Guard with truthy-strip check + WARNING so the config error
        # is visible.
        for neg in self.negative_keywords:
            neg_clean = (neg or "").strip()
            if not neg_clean:
                continue  # empty entry from YAML — skip, don't nuke everything
            if neg_clean in text:
                return 0.0

        # No positive keywords configured — everything passes
        if not self.positive_keywords:
            return 1.0

        # Positive keyword overlap scoring
        # Score based on how many keywords match, normalized so 1-2 hits
        # is enough to pass typical thresholds (0.20-0.35).
        # Cap denominator at 3 so even large keyword lists are forgiving.
        hits = sum(1 for kw in self.positive_keywords if self._keyword_in_text(kw, text))
        denominator = min(max(len(self.positive_keywords) * 0.15, 1), 3)
        return min(1.0, hits / denominator)

    def filter(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter candidates, attaching relevance_score to each. Returns kept list.

        Reads the secondary text from ``description`` (YouTube API shape) or
        ``summary`` (RSS / BlackboxBrief legacy-bridge shape). Falling back
        between the two restores relevance scoring for BB stories whose
        ``content_research.py`` bridge populates ``summary`` instead of
        ``description`` — pre-fix, those stories were scored on the title
        alone, which let off-niche videos pass when the title was generic.
        """
        kept: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []

        for v in candidates:
            # Prefer ``description`` (YouTube API field), fall back to
            # ``summary`` (RSS feeds + BB legacy-bridge field) so that
            # neither producer is forced to rename its data shape.
            secondary_text = v.get("description") or v.get("summary", "")
            s = self.score(v.get("title", ""), secondary_text)
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
