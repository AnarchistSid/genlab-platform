"""SpliceReel hook generation strategy.

Inherits shared hook generation from BaseHookStrategy. Provides film-specific:
- ``_classify_story()`` — lifecycle-aware routing (pre_release, opening_weekend, etc.)
- ``_substitute_placeholders()`` — {film_title}, {franchise}, {rt_score} resolution
- ``_extract_film_name()`` — RSS headline film-name extraction
"""

from __future__ import annotations

import re
from pathlib import Path

from genlab_core.strategies import BaseHookStrategy

NICHE_ROOT = Path(__file__).resolve().parent.parent

# Lifecycle -> preferred story categories
_LIFECYCLE_CATEGORIES: dict[str, list[str]] = {
    "pre_release": ["trailer_drop", "franchise_news"],
    "opening_weekend": ["box_office_win", "box_office_miss"],
    "long_tail": ["critical_acclaim", "critical_bomb", "award_nomination", "streaming_premiere"],
    "unknown": ["default"],
}


class MovieHookStrategy(BaseHookStrategy):
    """Generate lifecycle-aware hooks for film content."""

    _title_fallback_label = "Movie moment"

    def __init__(self) -> None:
        super().__init__(niche_id="movies", niche_root=NICHE_ROOT)

    def _classify_story(self, story: dict) -> str:
        """Determine story category using lifecycle stage and content signals."""
        lifecycle = story.get("lifecycle_stage", "unknown")
        is_trailer = story.get("is_trailer_drop", False)
        is_box_office = story.get("is_box_office_news", False)
        is_awards = story.get("is_award_news", False)
        franchise = story.get("franchise", "")
        title = (story.get("title", "") + " " + story.get("summary", "")).lower()

        if is_trailer:
            return "trailer_drop"
        if is_awards:
            return "award_nomination"
        if is_box_office:
            return "box_office_win"
        if franchise and lifecycle == "pre_release":
            return "franchise_news"
        if any(w in title for w in ["streaming", "netflix", "prime", "disney+", "hulu"]):
            return "streaming_premiere"

        preferred = _LIFECYCLE_CATEGORIES.get(lifecycle, ["default"])
        return preferred[0]

    @staticmethod
    def _extract_film_name(raw: str) -> str:
        """Extract the film name from an RSS headline."""
        quoted = re.findall(
            r"['\u2018\u2019\u201c\u201d\"]+([^'\u2018\u2019\u201c\u201d\"]{2,40})['\u2018\u2019\u201c\u201d\"]+",
            raw,
        )
        if quoted:
            return quoted[0].strip()
        prefixes = ["Box Office:", "Review:", "Exclusive:", "Breaking:", "Report:"]
        cleaned = raw
        for p in prefixes:
            if cleaned.startswith(p):
                cleaned = cleaned[len(p) :].strip()
        return cleaned

    def _substitute_placeholders(self, formula: str, story: dict) -> str:
        raw_title = story.get("film_title", "") or story.get("title", "")
        extracted = self._extract_film_name(raw_title) if raw_title else ""
        film_title = self._truncate_at_word(extracted, 30) if extracted else "this film"
        franchise = story.get("franchise", "")
        rt_score = story.get("rt_score")

        subs = {
            "film_title": film_title,
            "film": film_title,
            "franchise": franchise or "the franchise",
            "director": "the director",
            "rt_score": str(rt_score) if rt_score is not None else "??",
            "platform": "streaming",
            "announcement": "a major announcement",
            "amount": "???",
            "year": "2026",
        }

        result = formula
        for key, value in subs.items():
            result = result.replace(f"{{{key}}}", value)
        return result
