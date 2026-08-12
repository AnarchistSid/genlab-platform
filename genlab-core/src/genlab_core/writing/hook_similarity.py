"""Fuzzy hook similarity — Jaccard word-overlap ratio.

## Why this exists

`push_to_backlog.py:2406-2423` drops blueprints whose hook is >60%
Jaccard-similar to a recent hook in the same niche. When this fires,
we lose a story entirely — the video was fetched, scored, LLM-written,
and then discarded at persist time.

The rejection is CORRECT (near-duplicate hooks tank per-video CTR
because feed algorithms see repetitive titles as low-quality) but
INVISIBLE — logged at INFO level, no metric, no dashboard signal.

This module extracts the similarity math into a reusable primitive
so we can call it EARLIER in the pipeline (writer post-LLM path)
for observability. Same algorithm as push_to_backlog's inline logic
so the two sites agree on which hooks get flagged.

## Thresholds

  * `SIMILARITY_THRESHOLD = 0.6` — matches push_to_backlog inline
  * `MIN_WORDS = 3` — hooks shorter than 3 tokens are too fragile
    for Jaccard (2 shared words out of 2 = 100%, meaningless)

## Consumer wires

  * `base_writing._write_story_llm` — logs WARN when LLM emits near-
    dupe. Observability-only.
  * `base_writing._maybe_retry_on_near_dupe` — retries the writer
    with an explicit avoid-hint. Flag-gated recovery action.
  * `push_to_backlog._merge_story` — the authoritative drop-at-
    persist gate. Migrated 2026-08-12 to use this module (was
    inline duplicate math). Shares threshold + algorithm so all
    three sites drop the same set of hooks.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD: Final[float] = 0.6
"""Above this ratio, hooks are considered near-duplicates. Since
2026-08-12 all three sites (writer WARN, writer retry, push drop)
import this constant — changing it here changes them all in lockstep."""

MIN_WORDS: Final[int] = 3
"""Minimum word count on BOTH hooks for a meaningful Jaccard.
Two-word hooks like 'Wild play' vs 'Wild moment' would score
50% purely on '{Wild}' overlap — meaningless signal."""


@dataclass(frozen=True)
class SimilarityMatch:
    """A near-dupe detection result."""

    matched_hook: str
    similarity: float
    """Jaccard ratio in [0.0, 1.0]."""


def jaccard_similarity(hook_a: str, hook_b: str) -> float:
    """Word-set Jaccard similarity. Case-insensitive.

    Returns 0.0 when either hook is shorter than MIN_WORDS — the
    ratio is unstable for very short strings.
    """
    if not hook_a or not hook_b:
        return 0.0
    words_a = set(hook_a.strip().lower().split())
    words_b = set(hook_b.strip().lower().split())
    if len(words_a) < MIN_WORDS or len(words_b) < MIN_WORDS:
        return 0.0
    union = words_a | words_b
    if not union:
        return 0.0
    return len(words_a & words_b) / len(union)


def find_most_similar(
    hook: str,
    recent_hooks: list[str] | set[str],
) -> SimilarityMatch | None:
    """Return the best match if any hook exceeds SIMILARITY_THRESHOLD,
    else None.

    "Best match" = highest Jaccard among the exceeded set. Ties broken
    by first-seen order in `recent_hooks`.
    """
    if not hook or not recent_hooks:
        return None
    best: SimilarityMatch | None = None
    for existing in recent_hooks:
        if not existing:
            continue
        score = jaccard_similarity(hook, existing)
        if score > SIMILARITY_THRESHOLD:
            if best is None or score > best.similarity:
                best = SimilarityMatch(matched_hook=existing, similarity=score)
    return best


def log_similarity_signal(
    hook: str,
    recent_hooks: list[str] | set[str],
    *,
    niche_id: str,
) -> SimilarityMatch | None:
    """Convenience wrapper that logs a WARN when a near-dupe is
    detected. Returns the match (or None) so callers can also act on
    the signal — but the caller doesn't need to do anything for the
    log to fire.

    Emits: `[hook_similarity] NEAR_DUPE niche=X score=0.75 emitted=... matched=...`

    Operator grep after deploy:
        journalctl -u genlab-* --since '2h ago' | grep 'NEAR_DUPE'
    """
    try:
        match = find_most_similar(hook, recent_hooks)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "[hook_similarity] find_most_similar raised (should not): %s", exc
        )
        return None
    if match is None:
        return None
    logger.warning(
        "[hook_similarity] NEAR_DUPE niche=%s score=%.2f emitted=%r matched=%r",
        niche_id,
        match.similarity,
        hook[:80],
        match.matched_hook[:80],
    )
    return match


__all__ = [
    "MIN_WORDS",
    "SIMILARITY_THRESHOLD",
    "SimilarityMatch",
    "find_most_similar",
    "jaccard_similarity",
    "log_similarity_signal",
]
