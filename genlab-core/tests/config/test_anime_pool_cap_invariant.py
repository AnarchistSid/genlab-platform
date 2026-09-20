"""anime raises its candidate POOL, never lowers its relevance BAR.

anime is the only niche with no native YouTube category, so its candidates
come from raw keyword search rather than a category-filtered ``mostPopular``
chart. That makes its intake the least precise in the repo while its
relevance threshold is the strictest (0.35 vs 0.20-0.25). Same
``top_n_per_run`` as everyone else meant the cap truncated the list BEFORE
the relevance gate ran, spending its slots on rows the next stage rejected.

Measured on the live fetcher 2026-09-21, one run, same keywords, 2 quota
units (all cache hits -- ``limit`` does not drive search.list):

    top_n   15   20   25   30   40   60
    kept     4    6   10   14   21   23     (pool exhausts at 42)

anime booked 1 day of schedule at the time; every other niche booked 8.

The bar is the thing that must not move. Rule #9 ("never ingest non-anime
content into FrameDrift") is enforced by the 0.35 threshold and the
``positive_keywords`` list. Raising supply by lowering either would import
exactly the off-niche content that rule exists to keep out -- and it would
look like a fix, because the candidate count goes up either way. These pins
separate the two so that "anime supply is low" can never be answered by
relaxing the gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]

# (niche dir, niche.yaml, sources.yaml) — the four category-backed niches
_NATIVE_CATEGORY_NICHES = {
    "gaming": (
        "CriticalRush/niches/gaming/config/niche.yaml",
        "CriticalRush/niches/gaming/config/sources.yaml",
    ),
    "sports": ("ClutchWire/config/niche.yaml", "ClutchWire/config/sources.yaml"),
    "movies": ("SpliceReel/config/niche.yaml", "SpliceReel/config/sources.yaml"),
}
_ANIME_NICHE = "FrameDrift/config/niche.yaml"
_ANIME_SOURCES = "FrameDrift/config/sources.yaml"

# The strictest threshold in the repo, and deliberately so.
_ANIME_RELEVANCE_THRESHOLD = 0.35


def _load(rel: str) -> dict:
    return yaml.safe_load((_ROOT / rel).read_text())


def _threshold(rel: str) -> float:
    return float(_load(rel).get("content_filter", {}).get("relevance_threshold"))


def test_anime_pool_cap_exceeds_the_category_backed_niches():
    """The asymmetry is the justification — keep it visible."""
    anime_top_n = _load(_ANIME_NICHE)["video_sourcing"]["top_n_per_run"]
    assert anime_top_n >= 30, (
        f"anime top_n_per_run is {anime_top_n}; at 15 only 4 of 42 candidates "
        "survived relevance and anime booked 1 day against everyone else's 8."
    )
    for niche, (niche_yaml, _) in _NATIVE_CATEGORY_NICHES.items():
        other = _load(niche_yaml)["video_sourcing"]["top_n_per_run"]
        assert anime_top_n > other, (
            f"anime ({anime_top_n}) must keep a larger pool than {niche} ({other}): "
            "their candidates arrive pre-filtered by a native YouTube category, "
            "anime's are raw keyword hits facing the strictest bar in the repo."
        )


def test_anime_relevance_bar_did_not_move():
    """Supply was raised pool-side. If this fails, the bar was lowered instead."""
    assert _threshold(_ANIME_SOURCES) == pytest.approx(_ANIME_RELEVANCE_THRESHOLD), (
        "anime's relevance_threshold changed. Raising anime supply by relaxing "
        "this is exactly what rule #9 forbids — the candidate count rises either "
        "way, so the metric cannot tell you which one happened. Raise "
        "top_n_per_run instead, and re-measure the kept-vs-top_n curve."
    )


def test_anime_keeps_the_strictest_bar_of_all_niches():
    anime = _threshold(_ANIME_SOURCES)
    for niche, (_, sources_yaml) in _NATIVE_CATEGORY_NICHES.items():
        assert anime >= _threshold(sources_yaml), (
            f"anime ({anime}) must stay at least as strict as {niche} "
            f"({_threshold(sources_yaml)}) — it has no category filter upstream."
        )


def test_other_niches_were_not_widened_by_side_effect():
    """The change was anime-only; nothing else should have drifted to match."""
    for niche, (niche_yaml, _) in _NATIVE_CATEGORY_NICHES.items():
        top_n = _load(niche_yaml)["video_sourcing"]["top_n_per_run"]
        assert top_n == 15, (
            f"{niche} top_n_per_run is {top_n}, expected 15. The anime fix was "
            "justified by anime having NO native category; applying it to a "
            "category-backed niche needs its own measurement, not this one's."
        )
