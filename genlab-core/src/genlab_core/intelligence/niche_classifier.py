"""NicheClassifier -- multi-label niche scoring for content routing.

Scores incoming stories against all 5 niche keyword profiles simultaneously,
returning the best-fit niches (max 2) for each story.

Scoring components:
    - Keyword relevance (0-0.6): positive keyword hits in title+description
    - Source affinity (+0.15): if source_affinity list contains this niche_id and >=1 keyword hit
    - Category match (+0.15): if youtube_category matches this niche
    - Negative keyword hard-reject: any negative keyword match -> score = 0.0

Usage:
    classifier = NicheClassifier()
    scores = classifier.classify("Sora just generated a full broadcast", "AI tool")
    routed = classifier.route(scores)  # -> ["ai_creators"]
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# GenLab workspace root: parent of genlab-core/
# Path: niche_classifier.py -> intelligence/ -> genlab_core/ -> src/ -> genlab-core/ -> GenLab/
GENLAB_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent

# Where each niche stores its sources.yaml
NICHE_SOURCE_PATHS: dict[str, Path] = {
    "ai_creators": Path("BlackboxBrief/config/sources.yaml"),
    "gaming": Path("CriticalRush/niches/gaming/config/sources.yaml"),
    "sports": Path("ClutchWire/config/sources.yaml"),
    "movies": Path("SpliceReel/config/sources.yaml"),
    "anime": Path("FrameDrift/config/sources.yaml"),
}

# YouTube category -> niche mapping
CATEGORY_TO_NICHE: dict[str, str] = {
    "20": "gaming",
    "17": "sports",
    "1": "movies",
    "24": "movies",
    "28": "ai_creators",
}


@dataclass
class NicheProfile:
    """Loaded keyword profile for a single niche."""

    niche_id: str
    positive_keywords: list[str] = field(default_factory=list)
    negative_keywords: list[str] = field(default_factory=list)
    relevance_threshold: float = 0.3

    # Pre-compiled patterns for efficient matching
    _positive_patterns: list[re.Pattern] = field(default_factory=list, repr=False)
    _negative_patterns: list[re.Pattern] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._compile_patterns()

    def _compile_patterns(self) -> None:
        """Compile keyword lists into regex patterns for word-boundary matching."""
        self._positive_patterns = [
            re.compile(r"\b" + re.escape(kw.lower()) + r"\b") for kw in self.positive_keywords
        ]
        self._negative_patterns = [
            re.compile(r"\b" + re.escape(kw.lower()) + r"\b") for kw in self.negative_keywords
        ]


class NicheClassifier:
    """Multi-label classifier that scores stories against all 5 niche profiles."""

    def __init__(self, genlab_root: Path | None = None) -> None:
        self._root = genlab_root or GENLAB_ROOT
        self._profiles: dict[str, NicheProfile] = {}
        self._load_all_profiles()

    def _load_all_profiles(self) -> None:
        """Load keyword profiles from each channel's sources.yaml."""
        for niche_id, rel_path in NICHE_SOURCE_PATHS.items():
            full_path = self._root / rel_path
            try:
                config = yaml.safe_load(full_path.read_text())
                filter_config = config.get("content_filter", {})
                positive = filter_config.get("positive_keywords", [])
                negative = filter_config.get("negative_keywords", [])
                threshold = filter_config.get("relevance_threshold", 0.3)

                profile = NicheProfile(
                    niche_id=niche_id,
                    positive_keywords=[str(k) for k in positive],
                    negative_keywords=[str(k) for k in negative],
                    relevance_threshold=threshold,
                )
                self._profiles[niche_id] = profile
                logger.debug(
                    "[NicheClassifier] Loaded %s: %d positive, %d negative keywords",
                    niche_id,
                    len(positive),
                    len(negative),
                )
            except FileNotFoundError:
                logger.warning(
                    "[NicheClassifier] sources.yaml not found for %s at %s",
                    niche_id,
                    full_path,
                )
            except Exception as exc:
                logger.warning(
                    "[NicheClassifier] Failed to load %s: %s",
                    niche_id,
                    exc,
                )

        logger.info(
            "[NicheClassifier] Loaded %d/%d niche profiles",
            len(self._profiles),
            len(NICHE_SOURCE_PATHS),
        )

    def _score_niche(
        self,
        profile: NicheProfile,
        text: str,
        source_affinity: list[str] | None = None,
        youtube_category: str | None = None,
    ) -> float:
        """Compute a score for a single niche against the given text.

        Returns:
            Score between 0.0 and 1.0.
            Returns 0.0 if any negative keyword matches (hard reject).
        """
        text_lower = text.lower()

        # Negative keyword hard-reject
        for pattern in profile._negative_patterns:
            if pattern.search(text_lower):
                return 0.0

        # Keyword relevance (0 - 0.6)
        hits = sum(1 for p in profile._positive_patterns if p.search(text_lower))

        # Min-2-hits gate: suppress keyword component for weak matches
        # but don't return 0.0 — category/affinity can still contribute
        if hits < 2:
            keyword_score = 0.0
        else:
            normalizer = min(max(len(profile.positive_keywords) * 0.15, 1), 5)
            keyword_score = min(hits / normalizer, 1.0) * 0.6

        score = keyword_score

        # Source affinity bonus (+0.15, requires at least 1 keyword hit)
        if source_affinity and profile.niche_id in source_affinity and hits > 0:
            score += 0.15

        # YouTube category match (+0.15)
        if youtube_category and CATEGORY_TO_NICHE.get(youtube_category) == profile.niche_id:
            score += 0.15

        return round(min(score, 1.0), 4)

    def classify(
        self,
        title: str,
        description: str = "",
        source_affinity: list[str] | None = None,
        youtube_category: str | None = None,
    ) -> dict[str, float]:
        """Score a story against all loaded niche profiles.

        Args:
            title: Story/video title.
            description: Story/video description or summary.
            source_affinity: List of niche_ids this source has affinity for.
            youtube_category: YouTube category ID (e.g. "20" for Gaming).

        Returns:
            Dict mapping niche_id -> score (0.0 to 1.0).
        """
        combined_text = f"{title} {description}".strip()
        if not combined_text:
            return {niche_id: 0.0 for niche_id in self._profiles}

        scores: dict[str, float] = {}
        for niche_id, profile in self._profiles.items():
            scores[niche_id] = self._score_niche(
                profile,
                combined_text,
                source_affinity=source_affinity,
                youtube_category=youtube_category,
            )

        return scores

    def route(
        self,
        scores: dict[str, float],
        max_niches: int = 2,
    ) -> list[str]:
        """Select which niches a story should be routed to.

        Returns niches where score >= that niche's threshold, max `max_niches`
        (sorted by descending score).
        """
        eligible: list[tuple[str, float]] = []
        for niche_id, score in scores.items():
            profile = self._profiles.get(niche_id)
            if not profile:
                continue
            if score >= profile.relevance_threshold:
                eligible.append((niche_id, score))

        # Sort by score descending, take top max_niches
        eligible.sort(key=lambda x: x[1], reverse=True)
        return [niche_id for niche_id, _ in eligible[:max_niches]]

    def classify_and_route(
        self,
        title: str,
        description: str = "",
        source_affinity: list[str] | None = None,
        youtube_category: str | None = None,
        max_niches: int = 2,
    ) -> tuple[dict[str, float], list[str]]:
        """Convenience: classify then route in one call.

        Returns:
            Tuple of (scores_dict, routed_niche_ids).
        """
        scores = self.classify(
            title,
            description,
            source_affinity=source_affinity,
            youtube_category=youtube_category,
        )
        routed = self.route(scores, max_niches=max_niches)
        return scores, routed
