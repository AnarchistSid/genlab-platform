"""Canonical niche_id validation + legacy-name normalisation.

A small but load-bearing module: every cross-channel publish path runs
input strings through here so the rest of the orchestrator can assume
the niche_id is one of the five canonical IDs. The legacy aliases
(``ai_tech``, ``ai_news``) are absorbed here, NOT downstream — keeping
that translation in one place is what makes Sprint 62's cross-channel
publishing guard possible.

Lives in its own module so the multi-platform publisher orchestrator
(:mod:`genlab_core.publishing.publish_all_platforms`) stays focused on
publishing, and so other call sites (already-existing cross-channel
guards, future single-platform CLIs) can validate without importing
the 1500-line orchestrator. Extracted in the refactor-#9 decomposition
(PR 1/N). Re-exported from the orchestrator for backwards-compatible
imports.
"""

from __future__ import annotations

# Source of truth for the five canonical Gen Lab niche IDs. New niches go
# here first; the publisher then accepts them automatically.
VALID_NICHE_IDS: frozenset[str] = frozenset(
    {
        "ai_creators",
        "gaming",
        "sports",
        "movies",
        "anime",
    }
)


def normalize_niche(niche_id: str) -> str:
    """Map legacy/alternate niche names to their canonical ID.

    Currently absorbs ``ai_tech`` and ``ai_news`` → ``ai_creators``.
    Returns the input unchanged (only whitespace-stripped) if no alias
    matches — invalid IDs surface in :func:`validate_niche`, not here.
    """
    niche_id = niche_id.strip()
    if niche_id in ("ai_tech", "ai_news"):
        return "ai_creators"
    return niche_id


def validate_niche(niche_id: str) -> str:
    """Normalise + validate a niche_id, returning the canonical form.

    Raises :class:`ValueError` on an empty or unknown niche_id — this is
    a hard rejection at the entry point of every publish path, not a
    silent fallback. A typo'd niche_id MUST stop the run; the cost of
    a silent default would be publishing to the wrong channel.
    """
    niche_id = normalize_niche(niche_id)
    if not niche_id:
        raise ValueError("Empty niche_id — refusing to publish")
    if niche_id not in VALID_NICHE_IDS:
        raise ValueError(f"unknown niche_id '{niche_id}' — valid: {sorted(VALID_NICHE_IDS)}")
    return niche_id
