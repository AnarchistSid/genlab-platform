"""SOURCE-04: sports top-N selection reserves slots for declared highlights.

Measured cause (2026-09-14): `_trending_video` means "has a video" and is set by
six fetchers — reddit, tmdb, twitch, steam, anime_promos, generated_backfill — so
the "video-first" top-N cut put every candidate in the same bucket and order fell
back to `final_score`. Reddit discussion posts outscore YouTube highlights on the
sports dimensions (which assume ESPN metadata highlights lack), so Reddit took all
five slots from July onward: `youtube_trending` went 189 blueprints in May to 5 in
September while the fetcher still returned ~25 action clips per run.

These assert on the FIELD and the ORDER, not on a count, because the count was
what hid the defect: `trending_bypassed` emitted 0 whether nothing needed the
bypass or nothing was recognised.
"""

from __future__ import annotations

from typing import Any

from cw_strategies.scoring import SportScoringStrategy


def _story(title: str, *, highlight: bool, score: float, src: str) -> dict[str, Any]:
    """A story as it reaches scoring, already carrying a final_score."""
    return {
        "title": title,
        "source": src,
        "is_highlight": highlight,
        # every video fetcher sets this — that is the whole point
        "_trending_video": True,
        "final_score": score,
        "composite_score": score,
        "visual_potential": 1.0,
        "scores": {},
    }


class _Strategy(SportScoringStrategy):
    """Bypass score_item so the test controls final_score exactly."""

    def score_item(self, item: dict) -> dict:  # type: ignore[override]
        return item


def _run(stories: list[dict], top_n: int = 5, highlight_min: int = 4) -> list[dict]:
    s = _Strategy()
    s._config = {}
    s._weights = {}
    s._multipliers = {}
    s._thresholds = {
        "min_clip_score": 0.30,
        "top_clips_per_run": top_n,
        "highlight_slot_min": highlight_min,
        "min_visual_potential": 0.0,
    }
    ctx: dict[str, Any] = {"stories": stories}
    return s.execute(ctx)["stories"]


def test_highlight_wins_at_equal_score() -> None:
    """The brief's pin: equal final_score, the highlight is selected first."""
    out = _run(
        [
            _story("reddit discussion", highlight=False, score=0.90, src="reddit:MMA"),
            _story("league highlight", highlight=True, score=0.90, src="youtube_trending"),
        ],
        top_n=1,
    )
    assert len(out) == 1
    assert out[0]["is_highlight"] is True, (
        f"at equal score the highlight must be selected first; got {out[0]['title']!r}"
    )


def test_highlights_hold_the_reserved_slots_even_when_outscored() -> None:
    """The real shape: Reddit outscores every highlight and must not take all five."""
    stories = [_story(f"reddit {i}", highlight=False, score=0.99 - i * 0.01,
                      src="reddit:boxing") for i in range(5)]
    stories += [_story(f"highlight {i}", highlight=True, score=0.40 - i * 0.01,
                       src="youtube_trending") for i in range(5)]
    out = _run(stories, top_n=5, highlight_min=4)
    picked = sum(1 for s in out if s["is_highlight"])
    assert len(out) == 5
    assert picked >= 4, (
        f"highlight_slot_min=4 must reserve 4 of 5 slots; got {picked}. "
        f"Selected: {[s['title'] for s in out]}"
    )
    assert picked < 5, "one slot must remain available to a genuinely hotter story"


def test_knob_at_zero_restores_previous_behaviour() -> None:
    """highlight_slot_min: 0 is the documented rollback, no deploy required."""
    stories = [_story(f"reddit {i}", highlight=False, score=0.99 - i * 0.01,
                      src="reddit:boxing") for i in range(5)]
    stories += [_story("highlight", highlight=True, score=0.10, src="youtube_trending")]
    out = _run(stories, top_n=5, highlight_min=0)
    assert sum(1 for s in out if s["is_highlight"]) == 0, (
        "with the knob off, pure score order must return — the pre-2026-09-14 behaviour"
    )


def test_no_highlights_present_is_not_a_regression() -> None:
    """The other four niches, and any sports fire with no highlight source up."""
    stories = [_story(f"reddit {i}", highlight=False, score=0.9 - i * 0.01,
                      src="reddit:boxing") for i in range(8)]
    out = _run(stories, top_n=5, highlight_min=4)
    assert len(out) == 5, "with no highlights present the cut must still fill top_n"


def test_counters_distinguish_recognised_from_bypassed() -> None:
    """`trending_bypassed` emitted 0 for both health and total failure.

    `trending_recognised` and `highlights_recognised` are the denominators that
    make the two distinguishable.
    """
    s = _Strategy()
    s._config = {}
    s._weights = {}
    s._multipliers = {}
    s._thresholds = {"min_clip_score": 0.30, "top_clips_per_run": 5,
                     "highlight_slot_min": 4, "min_visual_potential": 0.0}
    ctx: dict[str, Any] = {
        "stories": [
            _story("h", highlight=True, score=0.9, src="youtube_trending"),
            _story("r", highlight=False, score=0.8, src="reddit:MMA"),
        ]
    }
    stats = s.execute(ctx)["run_stats"]["scoring"]
    assert stats["trending_recognised"] == 2, stats
    assert stats["highlights_recognised"] == 1, stats
    assert stats["highlights_selected"] == 1, stats
