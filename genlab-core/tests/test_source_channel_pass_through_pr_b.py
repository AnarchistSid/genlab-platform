"""PR #B (2026-07-10, Markanimation incident) — source channel metadata
end-to-end pass-through pins.

Context: PR #A wired source-creator credit into all platform captions,
but relied on ``video.channel_name`` being available at writer time. The
underlying data plane still dropped ``channel_id`` at
``TrendingVideo.to_story()`` (asymmetry with ``to_dict()``) and never
declared ``channel_name`` / ``channel_id`` on ``StoryCandidate`` — the
schema-validation boundary that the pipeline treats as ground truth.

Result: 282/282 published blueprints over the last 90 days had NULL
``source_channel_id``. Cross-niche source-discovery, retroactive-credit
scripts, and any future attribution logic that reads from the DB were
all broken. PR #B closes the pipe:

  1. ``StoryCandidate.channel_id`` + ``channel_name`` are declared
     first-class fields (survive ``model_dump()`` intact).
  2. ``TrendingVideo.to_story()`` emits BOTH channel_name AND channel_id.
  3. RSS parser captures ``yt:channelId`` per entry (was silently
     empty on the RSS fallback path).
  4. ``push_to_backlog`` persists ``source_channel_title`` into the
     blueprint alongside ``source_channel_id``.

These are source pins — same defensive pattern as
``test_push_to_backlog_source_channel_id_wire.py``. A future refactor
that drops any link in the chain surfaces here at import time.
"""

from __future__ import annotations

import re
from pathlib import Path


def _normalise(src: str) -> str:
    return re.sub(r"\s+", " ", src)


# ── StoryCandidate declared fields ─────────────────────────────────


def test_story_candidate_declares_channel_id_and_channel_name():
    """channel_id and channel_name must be declared on ``StoryCandidate``
    (not just allowed as extras). Declared fields survive ``model_dump()``
    with their defaults intact; extras only survive if actually present
    in the raw dict — silently dropped otherwise on model construction
    from a partial dict.
    """
    from genlab_core.pipeline.models import StoryCandidate

    # Both fields present with the expected shape + default
    assert "channel_id" in StoryCandidate.model_fields
    assert "channel_name" in StoryCandidate.model_fields
    assert StoryCandidate.model_fields["channel_id"].default is None
    assert StoryCandidate.model_fields["channel_name"].default is None


def test_story_candidate_channel_fields_survive_round_trip():
    """model_validate → model_dump preserves both channel fields."""
    from genlab_core.pipeline.models import StoryCandidate

    # 2026-08-10: video_id added to satisfy Option C video-invariant
    # (Markanimation shape IS a YouTube video, so video_id is natural).
    raw = {
        "title": "Some title",
        "source": "youtube_trending",
        "source_url": "https://www.youtube.com/watch?v=abc",
        "video_id": "abc",
        "channel_id": "UC12qezbIDmro0C5qBEIdFWg",
        "channel_name": "Markanimation",
    }
    sc = StoryCandidate.from_raw(raw)
    dumped = sc.model_dump()
    assert dumped["channel_id"] == "UC12qezbIDmro0C5qBEIdFWg"
    assert dumped["channel_name"] == "Markanimation"


def test_story_candidate_channel_fields_default_to_none():
    """Non-YouTube fetchers can omit channel fields — validation must
    accept the omission and default both to None. video_id still
    required (Option C invariant) — a Reddit story linking to a video
    populates video_id from the linked post's ID."""
    from genlab_core.pipeline.models import StoryCandidate

    sc = StoryCandidate.from_raw(
        {
            "title": "Reddit story",
            "source": "reddit_top",
            "source_url": "https://reddit.com/r/x/comments/y/",
            "video_id": "reddit_post_id_xyz",  # 2026-08-10 invariant
        }
    )
    dumped = sc.model_dump()
    assert dumped["channel_id"] is None
    assert dumped["channel_name"] is None


# ── TrendingVideo.to_story emits channel_id ────────────────────────


def test_trending_video_to_story_emits_channel_id():
    """The Markanimation incident's root cause: ``to_dict()`` had
    channel_id but ``to_story()`` silently omitted it. This test pins
    the fix — a symmetry regression fires here."""
    from datetime import UTC, datetime

    from genlab_core.media.trending_video_fetcher import TrendingVideo

    tv = TrendingVideo(
        video_id="FW1it6lrPNQ",
        title="test",
        channel_name="Markanimation",
        channel_id="UC12qezbIDmro0C5qBEIdFWg",
        published_at=datetime.now(UTC),
        view_count=1000,
        like_count=100,
        duration_seconds=30,
        thumbnail_url="",
        niche_id="anime",
        search_query="jjk",
        view_velocity=500.0,
        download_url="https://www.youtube.com/watch?v=FW1it6lrPNQ",
        is_official_channel=False,
        license="youtube",
    )
    story = tv.to_story()
    assert story["channel_id"] == "UC12qezbIDmro0C5qBEIdFWg"
    assert story["channel_name"] == "Markanimation"


def test_trending_video_to_dict_to_story_symmetry():
    """Both serializers must expose the same channel fields — any
    future asymmetry recreates the Markanimation-class bug."""
    from datetime import UTC, datetime

    from genlab_core.media.trending_video_fetcher import TrendingVideo

    tv = TrendingVideo(
        video_id="x",
        title="t",
        channel_name="Ch",
        channel_id="UC1",
        published_at=datetime.now(UTC),
        view_count=1,
        like_count=1,
        duration_seconds=30,
        thumbnail_url="",
        niche_id="anime",
        search_query="",
        view_velocity=1.0,
        download_url="https://x",
        is_official_channel=False,
        license="",
    )
    d = tv.to_dict()
    s = tv.to_story()
    assert d["channel_id"] == s["channel_id"]
    assert d["channel_name"] == s["channel_name"]


# ── RSS parser captures yt:channelId ───────────────────────────────


def test_rss_parser_captures_channel_id():
    """The RSS fallback path (used when YouTube quota is exhausted)
    must extract ``yt:channelId`` from each entry so channel metadata
    survives even the degraded path. Prior to PR #B this was silently
    hardcoded to empty string."""
    import genlab_core.media.trending_video_fetcher as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The RSS parser must look for the yt:channelId element
    assert "YT_NS}}}channelId" in src or "YT_NS}channelId" in src
    # And write it into the item dict alongside channel_name
    assert '"channel_id": chan_el.text' in src


def test_rss_fallback_reads_channel_id_from_meta():
    """The RSS-fallback TrendingVideo construction (line ~843) previously
    hardcoded ``channel_id=""``; PR #B reads it from meta if the RSS
    parser captured it. Pin the meta-fallback pattern."""
    import genlab_core.media.trending_video_fetcher as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The hardcoded empty is gone; meta.get is used
    assert 'channel_id=meta.get("channel_id"' in src


def test_channel_metadata_merge_handles_channel_id_fallback():
    """When ``_fetch_video_details`` returns a TrendingVideo with an
    empty channel_id (edge case: API returned partial snippet), the
    merge loop must copy channel_id from ``video_metadata`` (populated
    by the RSS/playlist item that surfaced this video_id). Mirrors the
    pre-existing channel_name fallback pattern at the same site."""
    import genlab_core.media.trending_video_fetcher as mod

    src = _normalise(Path(mod.__file__).read_text())
    # The channel_id meta-fallback assignment
    assert "v.channel_id = meta[" in src or 'v.channel_id = meta["channel_id"]' in src


# ── push_to_backlog persists source_channel_title ─────────────────


def test_push_to_backlog_reads_source_channel_title_from_story():
    """push_to_backlog must persist ``source_channel_title`` alongside
    ``source_channel_id`` so downstream retroactive-credit + attribution
    logic can look up creator handles from the DB without hitting the
    YouTube API. Reads ``channel_name`` (StoryCandidate field name)
    first for the trending path, then falls back to
    ``source_channel_title`` for pre-migration story shapes."""
    import genlab_core.pipeline.stages.push_to_backlog as mod

    src = _normalise(Path(mod.__file__).read_text())

    # Local variable name
    assert "source_channel_title =" in src
    # Reads channel_name (StoryCandidate declared field) from story
    assert 'story.get("channel_name")' in src
    # Passes into fields dict for persistence
    assert '"source_channel_title": source_channel_title' in src
