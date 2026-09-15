"""Pin: the videos.list re-enrichment must not destroy source-set provenance.

2026-09-15. `fetch_trending` collects candidates from the subscribed-channel,
chart and keyword paths, then re-enriches ALL of them in one videos.list batch
for accurate stats. That step built new TrendingVideo objects and replaced the
candidates wholesale, using the dict only for its keys -- so every field the
source path had set was silently dropped one function after it was set.

Measured on the 05:00Z sports fire: 16 subscribed channels -> 102 videos, and
all 14 reaching the scorer carried search_query='detail' (the re-enrichment's
own placeholder) and is_highlight=False. highlights_recognised=0, so the
highlight-first cut with slot_min=4 had nothing to reserve and Reddit took
every slot -- the exact symptom the highlight cut was built to fix.

This is the third time is_highlight has been lost in this file: once set only
on the rss_fallback branch (de72cb1b -> a38d0fc8), once emitted by to_dict but
not to_story (caught by test_serialiser_parity), and now destroyed by
re-enrichment. The field is fine; every boundary that rebuilds the object is
a new place to drop it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from genlab_core.media.trending_video_fetcher import TrendingVideo


def _video(vid: str, **kw) -> TrendingVideo:
    base = dict(
        video_id=vid,
        title="Race Highlights | 2026 Spanish Grand Prix",
        channel_name="FORMULA 1",
        channel_id="UCq",
        published_at=datetime.now(UTC),
        view_count=1_000_000,
        like_count=42_996,
        duration_seconds=45,
        thumbnail_url="",
        niche_id="sports",
        search_query="channel_subscription",
        view_velocity=5000.0,
        download_url=f"https://www.youtube.com/watch?v={vid}",
        is_official_channel=True,
        license="youtube",
    )
    base.update(kw)
    return TrendingVideo(**base)


def _merge(candidates: dict[str, TrendingVideo], detailed: list[TrendingVideo]) -> None:
    """The exact merge fetch_trending performs after videos.list."""
    for v in detailed:
        prior = candidates.get(v.video_id)
        if prior is not None:
            v.is_highlight = v.is_highlight or prior.is_highlight
            if prior.search_query and prior.search_query != "detail":
                v.search_query = prior.search_query
        candidates[v.video_id] = v


def test_is_highlight_survives_re_enrichment() -> None:
    """The measured failure: True on the candidate, False on the rebuild."""
    candidates = {"abc": _video("abc", is_highlight=True)}
    rebuilt = _video("abc", search_query="detail", is_highlight=False)
    _merge(candidates, [rebuilt])
    assert candidates["abc"].is_highlight is True, (
        "is_highlight set by the source path must survive videos.list re-enrichment"
    )


def test_search_query_placeholder_does_not_overwrite_real_provenance() -> None:
    """'detail' is _parse_video's placeholder, not a source."""
    candidates = {"abc": _video("abc", search_query="channel_subscription")}
    _merge(candidates, [_video("abc", search_query="detail")])
    assert candidates["abc"].search_query == "channel_subscription"


def test_stats_still_come_from_videos_list() -> None:
    """Provenance is restored; STATS must NOT be — videos.list is authoritative."""
    candidates = {"abc": _video("abc", view_count=1, like_count=1, duration_seconds=9)}
    _merge(candidates, [_video("abc", view_count=999_999, like_count=4242,
                               duration_seconds=45, search_query="detail")])
    got = candidates["abc"]
    assert (got.view_count, got.like_count, got.duration_seconds) == (999_999, 4242, 45)


def test_non_highlight_candidate_is_not_promoted() -> None:
    """The chart path must not acquire a highlight claim it never made."""
    candidates = {"abc": _video("abc", search_query="mostPopular", is_highlight=False)}
    _merge(candidates, [_video("abc", search_query="detail", is_highlight=False)])
    assert candidates["abc"].is_highlight is False


def test_unknown_video_id_is_inserted_not_crashed() -> None:
    """videos.list can return an id absent from candidates; prior is None."""
    candidates: dict[str, TrendingVideo] = {}
    _merge(candidates, [_video("new", search_query="detail")])
    assert candidates["new"].search_query == "detail"


def test_the_merge_under_test_matches_the_source() -> None:
    """Guard the guard: this file reimplements the merge, so it can drift
    from the code it pins. Fail loudly if the real site stops looking like it."""
    import inspect

    from genlab_core.media import trending_video_fetcher as tvf

    src = inspect.getsource(tvf.TrendingVideoFetcher.fetch_trending)
    for fragment in (
        "v.is_highlight = v.is_highlight or prior.is_highlight",
        'prior.search_query != "detail"',
    ):
        assert fragment in src, (
            f"fetch_trending no longer contains {fragment!r} — this pin is "
            f"testing a merge the production path no longer performs."
        )
