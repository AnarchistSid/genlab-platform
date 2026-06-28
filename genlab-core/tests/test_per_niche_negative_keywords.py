"""Pin PR R: each of the 5 niches' ``sources.yaml`` declares a
non-trivial ``content_filter.negative_keywords`` list.

Background: AUTONOMY-GAP-ANALYSIS.md Q1 cited "All 5 niches have ZERO
negative_keywords in content_filter" as the empirical root of the
~3.8% egregious cross-niche contamination measured in §3 of that
doc. In reality, four of five niches already shipped substantial
negative-keyword lists; ``gaming`` was the only true gap (7 entries)
and was brought up to ~58 entries with the same cross-niche-guard
pattern the other four niches already used.

The ``RelevanceFilter`` in ``genlab_core.media.relevance_filter``
hard-rejects (score=0.0) any candidate whose lowercased text
contains ANY negative keyword as a substring, BEFORE the positive-
keyword scoring runs. The wiring already exists; this test pins
the *data* — preventing future edits from regressing any niche back
to a sparse list, and preventing FrameDrift from losing its
documented MMA / UFC / boxing / wrestling seed (called out as the
calibration anchor in CLAUDE.md's RelevanceFilter section).

If you intentionally want to shrink one of these lists below the
threshold below, raise the topic in a sprint review first — silent
shrinkage of the cross-niche guard is the regression this pin
exists to catch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

# (niche_id, sources.yaml path) — same set as test_single_publish_window_invariant.
SOURCES_YAMLS: dict[str, Path] = {
    "ai_creators": REPO_ROOT / "BlackboxBrief" / "config" / "sources.yaml",
    "anime": REPO_ROOT / "FrameDrift" / "config" / "sources.yaml",
    "sports": REPO_ROOT / "ClutchWire" / "config" / "sources.yaml",
    "movies": REPO_ROOT / "SpliceReel" / "config" / "sources.yaml",
    "gaming": REPO_ROOT / "CriticalRush" / "niches" / "gaming" / "config" / "sources.yaml",
}

# Floor agreed in PR R: every niche keeps ≥10 negative keywords. This
# is the minimum that meaningfully covers cross-niche contamination
# without being so onerous that operators can't make small principled
# cuts.
MIN_NEGATIVE_KEYWORDS = 10


def _load_content_filter(yaml_path: Path) -> dict:
    data = yaml.safe_load(yaml_path.read_text()) or {}
    cf = data.get("content_filter")
    assert cf is not None, (
        f"{yaml_path}: missing top-level ``content_filter`` block. "
        f"All 5 niches must declare ``content_filter`` so RelevanceFilter "
        f"has a config to consume."
    )
    return cf


def _negative_keywords(yaml_path: Path) -> list[str]:
    cf = _load_content_filter(yaml_path)
    neg = cf.get("negative_keywords") or []
    assert isinstance(neg, list), (
        f"{yaml_path}: ``content_filter.negative_keywords`` must be a list, "
        f"got {type(neg).__name__}."
    )
    return [str(k).lower() for k in neg]


def _positive_keywords(yaml_path: Path) -> list[str]:
    cf = _load_content_filter(yaml_path)
    pos = cf.get("positive_keywords") or []
    assert isinstance(pos, list), (
        f"{yaml_path}: ``content_filter.positive_keywords`` must be a list, "
        f"got {type(pos).__name__}."
    )
    return [str(k).lower() for k in pos]


# ---------------------------------------------------------------------------
# Per-niche pins (5 tests)
# ---------------------------------------------------------------------------


def test_ai_creators_negative_keywords_nonempty() -> None:
    neg = _negative_keywords(SOURCES_YAMLS["ai_creators"])
    assert len(neg) >= MIN_NEGATIVE_KEYWORDS, (
        f"ai_creators: expected ≥{MIN_NEGATIVE_KEYWORDS} negative_keywords, "
        f"got {len(neg)}. Shrinkage below this floor is the regression "
        f"AUTONOMY-GAP-ANALYSIS.md Q1 was written to prevent."
    )


def test_anime_negative_keywords_includes_mma_seed() -> None:
    """FrameDrift's MMA / UFC / boxing / wrestling seed is the
    calibration anchor cited in CLAUDE.md's RelevanceFilter section.
    Losing any one of those four would re-open the most-documented
    cross-niche contamination case (combat sports videos getting
    routed to anime because of overlapping creator pools)."""
    neg = _negative_keywords(SOURCES_YAMLS["anime"])
    for seed in ("mma", "ufc", "boxing", "wrestling"):
        assert seed in neg, (
            f"anime: negative_keywords lost the {seed!r} seed — see "
            f"CLAUDE.md's 'Anime rejects: MMA, UFC, boxing, wrestling' "
            f"line and AUTONOMY-GAP-ANALYSIS.md Q1."
        )
    assert len(neg) >= MIN_NEGATIVE_KEYWORDS, (
        f"anime: expected ≥{MIN_NEGATIVE_KEYWORDS} negative_keywords, got {len(neg)}."
    )


def test_gaming_negative_keywords_nonempty() -> None:
    """Gaming was the empirical gap PR R was written to close — it
    shipped with only 7 negative_keywords (cooking / beauty / mukbang
    / asmr / meditation / yoga / recipe) and no cross-niche guards
    at all. After PR R it carries ~58 entries with full sports /
    anime / movies / AI cross-niche guards."""
    neg = _negative_keywords(SOURCES_YAMLS["gaming"])
    assert len(neg) >= MIN_NEGATIVE_KEYWORDS, (
        f"gaming: expected ≥{MIN_NEGATIVE_KEYWORDS} negative_keywords, "
        f"got {len(neg)}. Gaming was the empirical gap in PR R — do not "
        f"regress it back to the 7-entry baseline."
    )


def test_movies_negative_keywords_nonempty() -> None:
    neg = _negative_keywords(SOURCES_YAMLS["movies"])
    assert len(neg) >= MIN_NEGATIVE_KEYWORDS, (
        f"movies: expected ≥{MIN_NEGATIVE_KEYWORDS} negative_keywords, got {len(neg)}."
    )


def test_sports_negative_keywords_nonempty() -> None:
    neg = _negative_keywords(SOURCES_YAMLS["sports"])
    assert len(neg) >= MIN_NEGATIVE_KEYWORDS, (
        f"sports: expected ≥{MIN_NEGATIVE_KEYWORDS} negative_keywords, got {len(neg)}."
    )


# ---------------------------------------------------------------------------
# Cross-cutting pin (6th test)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("niche_id", "yaml_path"),
    list(SOURCES_YAMLS.items()),
    ids=list(SOURCES_YAMLS.keys()),
)
def test_all_niches_have_negative_keywords(niche_id: str, yaml_path: Path) -> None:
    """Every niche must have content_filter populated with BOTH a
    non-empty positive_keywords list AND a non-empty negative_keywords
    list AND an explicit relevance_threshold. Anything missing means
    RelevanceFilter falls back to defaults (threshold=0.3, no negative
    short-circuit), which is exactly the regression mode PR R closed."""
    cf = _load_content_filter(yaml_path)

    pos = _positive_keywords(yaml_path)
    neg = _negative_keywords(yaml_path)

    assert len(pos) > 0, (
        f"{niche_id}: positive_keywords is empty — RelevanceFilter "
        f"would fall back to score=1.0 for every candidate."
    )
    assert len(neg) >= MIN_NEGATIVE_KEYWORDS, (
        f"{niche_id}: negative_keywords has {len(neg)} entries, "
        f"need ≥{MIN_NEGATIVE_KEYWORDS}. See AUTONOMY-GAP-ANALYSIS.md Q1."
    )

    threshold = cf.get("relevance_threshold")
    assert threshold is not None, (
        f"{niche_id}: relevance_threshold missing — RelevanceFilter "
        f"falls back to 0.3, which is wrong for anime (0.35 documented) "
        f"and most other niches."
    )
    assert 0.0 < float(threshold) <= 1.0, (
        f"{niche_id}: relevance_threshold={threshold!r} must be in (0, 1]."
    )
