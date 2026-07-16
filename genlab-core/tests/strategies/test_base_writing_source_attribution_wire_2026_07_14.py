"""Pin tests for the 2026-07-14 writer wire attribution fix.

Session 2026-07-14 audit found Layer 5 attribution health at 0.0%
across all niches. All 6 recent PUBLISHED blueprints had EMPTY
``source_attribution`` in caption despite:
  * Story records having valid video_id + video_url populated
  * Layer 1 fetcher-side gate ENFORCE mode (channel_id required)
  * PR #779 (yesterday's writer wire fix) supposedly closing the gap

Root cause: ``base_writing._story_to_video_dict`` returned a video
dict missing ``source`` and ``video_url`` keys. Downstream
``write_video_content`` (video_content_writer.py:825) called
``format_source_attribution({video_id, source, source_channel_title})``:
  * ``source`` defaulted to "youtube_trending" (hardcoded fallback)
  * ``video_url`` was never passed → URL fallback branch dormant
  * Non-YT stories (twitch, scorebat, tmdb_trailer, RSS) produced
    EMPTY source_attribution because derive_source_url returns None
    for unknown source templates

Fix: ``_story_to_video_dict`` now passes ``source`` (from story) +
``video_url`` (with fallback chain to source_url / canonical_url).
``write_video_content`` uses these in format_source_attribution call.

These tests pin the wire end-to-end.
"""

from __future__ import annotations

from genlab_core.strategies.base_writing import BaseWritingStrategy


class _TestBaseWriting(BaseWritingStrategy):
    """Concrete subclass exposing the protected helper for testing."""

    def _load_config(self):  # noqa: D401
        """Skip config loading."""
        pass

    def execute(self, context):  # pragma: no cover — not exercised
        return context


class TestStoryToVideoDictSourceAttribution:
    """The video dict must carry source + video_url so downstream
    ``format_source_attribution`` can produce a credit line."""

    def _writer(self) -> _TestBaseWriting:
        # BaseWritingStrategy requires niche_id + niche_root but the
        # tests only exercise the pure ``_story_to_video_dict`` helper
        # (no config or logger access), so pass minimal args.
        from pathlib import Path

        w = _TestBaseWriting(niche_id="test", niche_root=Path("/tmp"))
        return w

    def test_youtube_story_video_dict_carries_source_and_url(self):
        story = {
            "story_id": "abc123",
            "title": "Cool YT video",
            "summary": "A cool video description",
            "channel_name": "Muse Asia",
            "video_id": "Cw-qEQfxGwo",
            "source": "youtube_trending",
            "video_url": "https://www.youtube.com/watch?v=Cw-qEQfxGwo",
        }
        video = self._writer()._story_to_video_dict(story)
        assert video["source"] == "youtube_trending"
        assert video["video_url"] == "https://www.youtube.com/watch?v=Cw-qEQfxGwo"
        assert video["channel_name"] == "Muse Asia"
        assert video["video_id"] == "Cw-qEQfxGwo"

    def test_twitch_story_video_dict_carries_source_url(self):
        """Non-YouTube path: derive_source_url returns None but
        format_source_attribution's URL fallback should still catch
        video_url."""
        story = {
            "story_id": "twitch-abc",
            "title": "Cloudrooms Twitch trending",
            "summary": "Cloudrooms is dominating Twitch",
            "source": "twitch_trending",
            "source_url": "https://www.twitch.tv/directory/game/Cloudrooms",
        }
        video = self._writer()._story_to_video_dict(story)
        assert video["source"] == "twitch_trending"
        # source_url falls through into video_url per the fallback chain
        assert video["video_url"] == "https://www.twitch.tv/directory/game/Cloudrooms"

    def test_empty_source_produces_empty_source_key_not_default(self):
        """When story has no source, the video dict should carry empty
        string — NOT default to 'youtube_trending'. The old default
        masked the missing-source case by producing garbage YT URLs."""
        story = {
            "story_id": "unknown-src",
            "title": "Untagged story",
            "summary": "Some content",
        }
        video = self._writer()._story_to_video_dict(story)
        assert video["source"] == ""
        assert video["video_url"] == ""


class TestFormatSourceAttributionEndToEnd:
    """End-to-end: given a real story shape, format_source_attribution
    must produce a non-empty credit line for all supported sources."""

    def test_youtube_produces_credit(self):
        from genlab_core.compliance.copyright_safety import format_source_attribution

        result = format_source_attribution(
            {
                "video_id": "Cw-qEQfxGwo",
                "source": "youtube_trending",
                "source_channel_title": "Muse Asia",
                "video_url": "https://www.youtube.com/watch?v=Cw-qEQfxGwo",
            }
        )
        assert "🎬 Original:" in result
        assert "Muse Asia" in result

    def test_twitch_produces_credit(self):
        """Twitch had no source URL template — the URL fallback branch
        activates here."""
        from genlab_core.compliance.copyright_safety import format_source_attribution

        result = format_source_attribution(
            {
                "video_id": "Cloudrooms",
                "source": "twitch_trending",
                "source_channel_title": "",
                "video_url": "https://www.twitch.tv/directory/game/Cloudrooms",
            }
        )
        assert "🎬 Original:" in result
        assert "twitch.tv" in result

    def test_scorebat_sports_produces_credit(self):
        """Sports had zero credited posts because scorebat wasn't in
        the URL template map. The URL fallback catches it."""
        from genlab_core.compliance.copyright_safety import format_source_attribution

        result = format_source_attribution(
            {
                "video_id": "",
                "source": "scorebat",
                "source_channel_title": "ESPN Sports",
                "video_url": "https://www.scorebat.com/barcelona-vs-getafe/",
            }
        )
        assert "🎬 Original:" in result
        assert "ESPN Sports" in result

    def test_no_url_no_id_returns_empty(self):
        """When we genuinely can't attribute, return empty — the
        Layer 4 gate then blocks publish. Don't fabricate a URL."""
        from genlab_core.compliance.copyright_safety import format_source_attribution

        result = format_source_attribution(
            {
                "video_id": "",
                "source": "",
                "source_channel_title": "",
                "video_url": "",
            }
        )
        assert result == ""
