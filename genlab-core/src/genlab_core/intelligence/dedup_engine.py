"""genlab_core.intelligence.dedup_engine — 3-pass content deduplication.

The three passes handle different types of duplicates in order of cost:

Pass 1 — URL exact match (O(1) hash lookup):
  Same URL = identical source content = hard duplicate. Fastest possible check.
  Uses full SHA-256 of the normalised URL (not truncated).

Pass 2 — Jaccard 3-gram similarity (O(n) per comparison):
  Catches near-duplicates with different URLs but identical or very similar text.
  Example: 'Elden Ring Sets Steam Record' vs 'Elden Ring Breaks Steam Record'
  Threshold is configurable per niche.

Pass 3 — TF-IDF cosine similarity (O(n^2) for full corpus):
  Catches semantically similar content that 3-gram Jaccard misses because the
  wording is different but the topic is the same. TF-IDF vectors weight rare
  words more heavily, so 'GPT-5 unveiled' and 'OpenAI reveals next model' would
  cluster together if they share significant rare terminology.

Niche configs control the thresholds for Passes 2 and 3.
A gaming niche might use lower thresholds (0.70, 0.65) to be more aggressive
about deduplicating 'same game, slightly different headline' stories.
An AI news niche might use higher thresholds (0.85, 0.80) because nuanced
differences in AI announcements matter more.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Sequence

logger = logging.getLogger(__name__)


def make_content_id(text: str) -> str:
    """Stable, non-truncated SHA-256 hex digest (64 chars).

    Use this instead of ad-hoc ``hashlib.sha256(...)[:16]`` to ensure
    content IDs are collision-resistant.  The full 64-character hex
    digest avoids the birthday-bound collision risk of truncated hashes.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    """Compute the set of character n-grams for a text string."""
    text = text.lower().strip()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def jaccard_similarity(a: str, b: str, n: int = 3) -> float:
    """Jaccard similarity between two strings using character n-grams."""
    ngrams_a = _char_ngrams(a, n)
    ngrams_b = _char_ngrams(b, n)
    if not ngrams_a or not ngrams_b:
        return 0.0
    intersection = len(ngrams_a & ngrams_b)
    union = len(ngrams_a | ngrams_b)
    return intersection / union if union > 0 else 0.0


@dataclass
class DedupResult:
    """Result of running the dedup engine on a batch of items."""

    unique: list[dict] = field(default_factory=list)
    duplicates: list[dict] = field(default_factory=list)
    pass1_removed: int = 0  # URL exact match
    pass2_removed: int = 0  # Jaccard near-duplicate
    pass3_removed: int = 0  # TF-IDF clustering


class DedupEngine:
    """3-pass content deduplication engine.

    Pass 1: URL SHA-256 exact match.
    Pass 2: Jaccard 3-gram similarity threshold.
    Pass 3: TF-IDF cosine similarity clustering.

    All thresholds are configurable so niche YAML configs can tune
    aggressiveness without touching code.

    Args:
        jaccard_threshold: Similarity above this = near-duplicate (Pass 2).
        tfidf_threshold:   Cosine similarity above this = cluster dup (Pass 3).
        url_field:         Key name for the URL in each content item dict.
        text_field:        Key name for the text content to compare.
        existing_ids:      Set of URL hashes already in the backlog
                           (used to deduplicate against published content,
                           not just the current batch).
    """

    def __init__(
        self,
        jaccard_threshold: float = 0.85,
        tfidf_threshold: float = 0.80,
        url_field: str = "url",
        text_field: str = "title",
        existing_ids: set[str] | None = None,
    ) -> None:
        self.jaccard_threshold = jaccard_threshold
        self.tfidf_threshold = tfidf_threshold
        self.url_field = url_field
        self.text_field = text_field
        self._seen_url_hashes: set[str] = set(existing_ids or set())

    def run(self, items: Sequence[dict]) -> DedupResult:
        """Run all three dedup passes on a batch of content items.

        The passes run in order of increasing cost. An item removed in
        Pass 1 is never evaluated in Passes 2 or 3, keeping the typical
        case fast.

        Args:
            items: Content items, each a dict with url_field and text_field keys.

        Returns:
            DedupResult with unique items and per-pass removal counts.
        """
        result = DedupResult()

        # Pass 1: URL exact match
        after_pass1 = []
        for item in items:
            url = item.get(self.url_field, "")
            url_hash = hashlib.sha256(url.strip().lower().encode()).hexdigest()
            if url_hash in self._seen_url_hashes:
                result.duplicates.append(item)
                result.pass1_removed += 1
                logger.debug("Pass 1 dup: %s", url[:80])
            else:
                self._seen_url_hashes.add(url_hash)
                after_pass1.append(item)

        # Pass 2: Jaccard 3-gram similarity
        after_pass2 = []
        seen_texts: list[str] = []
        for item in after_pass1:
            text = item.get(self.text_field, "")
            is_dup = any(
                jaccard_similarity(text, seen) >= self.jaccard_threshold
                for seen in seen_texts
            )
            if is_dup:
                result.duplicates.append(item)
                result.pass2_removed += 1
                logger.debug("Pass 2 dup (Jaccard): %s", text[:80])
            else:
                seen_texts.append(text)
                after_pass2.append(item)

        # Pass 3: TF-IDF cosine clustering
        after_pass3 = self._tfidf_pass(after_pass2, result)

        result.unique = after_pass3
        logger.info(
            "Dedup: %d -> %d unique (pass1=%d, pass2=%d, pass3=%d)",
            len(items),
            len(result.unique),
            result.pass1_removed,
            result.pass2_removed,
            result.pass3_removed,
        )
        return result

    def _tfidf_pass(self, items: list[dict], result: DedupResult) -> list[dict]:
        """TF-IDF cosine clustering pass. Requires sklearn."""
        if len(items) < 2:
            return items
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError:
            logger.warning(
                "scikit-learn not installed — skipping TF-IDF dedup pass. "
                "Install with: pip install scikit-learn"
            )
            return items

        texts = [item.get(self.text_field, "") for item in items]
        try:
            vectorizer = TfidfVectorizer(min_df=1, stop_words="english")
            matrix = vectorizer.fit_transform(texts)
        except ValueError:
            return items  # empty vocabulary edge case

        cos_sim = cosine_similarity(matrix)
        keep = [True] * len(items)
        for i in range(len(items)):
            if not keep[i]:
                continue
            for j in range(i + 1, len(items)):
                if keep[j] and cos_sim[i, j] >= self.tfidf_threshold:
                    keep[j] = False
                    result.duplicates.append(items[j])
                    result.pass3_removed += 1

        return [item for item, k in zip(items, keep) if k]
