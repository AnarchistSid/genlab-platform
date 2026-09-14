"""SOURCE-05: `to_story()` carries the two fields the sports funnel depends on.

Both were dropped at the same boundary, for the same reason — `to_dict()` emitted
them and `to_story()` did not, and nothing asserted the two agreed.

* `like_count` — `CompositeScorer` computes
  `engagement_score = like_count/view_count / target_like_ratio`. Absent, it is 0,
  `engagement_factor` pins at its 0.5 floor, and the composite degenerates to a
  constant. That is the sports 0.48 pin measured across July-September, and the
  value was present on the API items the whole time (180791, 42996, 115503).
* `is_highlight` — the sports top-N cut reserves slots for it. de72cb1b set it
  only on the `rss_fallback` construction, so the ENRICHED subscribed-channel
  videos (the ones that actually flow) defaulted to False and the cut had nothing
  to reserve for. Caught before the first fire that would have used it.

These assert on the field at the boundary, not on any count downstream.
"""

from __future__ import annotations

from datetime import UTC, datetime

from genlab_core.media.trending_video_fetcher import TrendingVideo


def _video(**kw) -> TrendingVideo:
    base = dict(
        video_id="abc12345678",
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
        download_url="https://www.youtube.com/watch?v=abc12345678",
        is_official_channel=True,
        license="youtube",
    )
    base.update(kw)
    return TrendingVideo(**base)


def test_to_story_emits_like_count() -> None:
    """The seam: engagement never reached CompositeScorer without this."""
    story = _video().to_story()
    assert "like_count" in story, (
        "to_story() must emit like_count — CompositeScorer divides by it, and its "
        "absence floors engagement_factor at 0.5, which is the sports composite pin."
    )
    assert story["like_count"] == 42_996


def test_to_story_and_to_dict_agree_on_engagement_fields() -> None:
    """The invariant that would have caught the original drop.

    `to_dict()` carried like_count and `to_story()` did not. Nothing compared them.
    """
    v = _video()
    d, s = v.to_dict(), v.to_story()
    for field_name in ("view_count", "like_count"):
        assert field_name in d and field_name in s, (
            f"{field_name} must be present in BOTH to_dict() and to_story(); "
            f"to_dict={field_name in d} to_story={field_name in s}"
        )
        assert d[field_name] == s[field_name]


def test_to_story_emits_is_highlight() -> None:
    assert _video(is_highlight=True).to_story()["is_highlight"] is True
    assert _video().to_story()["is_highlight"] is False, (
        "default must be False — only sources that KNOW they carry highlights set it"
    )


def test_is_highlight_defaults_false_so_other_niches_are_unaffected() -> None:
    """The mostPopular chart and every non-highlight source must not claim it."""
    chart_video = _video(search_query="mostPopular", is_official_channel=False)
    assert chart_video.to_story()["is_highlight"] is False


def test_is_highlight_is_a_settable_instance_attribute() -> None:
    """The enriched path sets it post-construction in the merge loop.

    de72cb1b only set it at construction on the rss_fallback branch; the fix
    assigns `v.is_highlight = True` on the enriched videos. If the dataclass ever
    becomes frozen, that assignment breaks silently at runtime — this fails first.
    """
    v = _video()
    assert v.is_highlight is False
    v.is_highlight = True
    assert v.to_story()["is_highlight"] is True
