"""Pin the source_attribution propagation through ``_write_story_llm``
(W1 trace — 2026-07-13 audit follow-up).

Production bug found after the 2026-07-13 Layer 5 tightening: today's
DB showed 0/6 attribution across ALL 5 niches. Trace revealed two bugs:

**Bug A** (``_story_to_video_dict``): read ``story.get("source")`` for
``raw_channel`` — but ``source`` is the source TYPE
(``"youtube_trending"``), NOT the creator's channel name. The writer's
``format_source_attribution`` received garbage as
``source_channel_title``, produced either ``"🎬 Original:
@youtube_trending — url"`` or (via URL-template mismatch) an empty
string.

**Bug C** (``_write_story_llm``): the writer sets
``result["source_attribution"]`` with the audience-facing credit line
but this propagator only cherry-picks specific fields into
``story["content"]``. ``source_attribution`` fell on the floor.
``push_to_backlog`` then reads
``story["content"]["source_attribution"]`` (empty), ``_credit``
helper no-ops, and every published caption ships without a visible
credit line.

Bug C alone broke Layer 4 acceptance + Layer 5 metric on the caption
side. Bug A alone would ship low-quality credit ("@youtube_trending"
instead of the real creator handle). Both fixes are required for
correct end-to-end wire.

The old Layer 5 metric masked this for weeks by counting
``source_channel_id IS NOT NULL`` as attribution — a signal that IS
populated regardless of this bug class. PR #776's audit tightening
removed that signal → metric became honest → today's 0/6 was the
first observable evidence of a long-standing broken wire.
"""

from __future__ import annotations

from unittest.mock import patch


def _make_strategy():
    """Build a stubbed BaseWritingStrategy that lets us call
    ``_write_story_llm`` with the real prod method body. Mirrors the
    sibling pattern in ``test_base_writing_caption_segments_wire.py``."""
    from genlab_core.strategies.base_writing import BaseWritingStrategy

    s = BaseWritingStrategy.__new__(BaseWritingStrategy)
    s.niche_id = "ai_creators"
    s._niche_id = "ai_creators"
    s._templates = {}
    s._writing_config = {}
    s._writing_cfg = {}
    return s


def _make_youtube_story() -> dict:
    """Story dict as emitted by TrendingVideoFetcher.to_story() — rich
    dict with channel_name populated from the YouTube API response."""
    return {
        "title": "OpenAI just shipped autonomous agents",
        "story_id": "s-yt-001",
        "source": "youtube_trending",  # ← source TYPE, not creator name
        "channel_name": "OpenAI",  # ← the actual creator handle
        "channel_id": "UC1234567890",
        "video_id": "rKV5JcALQoQ",
        "source_url": "https://youtube.com/watch?v=rKV5JcALQoQ",
        "summary": "OpenAI released a new autonomous agent framework...",
    }


class TestBugA_ChannelNameLookup:
    """Pin the ``_story_to_video_dict`` channel_name lookup fix.

    Regression pattern: reads ``story["source"]`` instead of
    ``story["channel_name"]``. If a future refactor re-inlines the
    old string, this test fires because the video dict will carry
    ``channel_name="youtube_trending"``."""

    def test_video_dict_carries_actual_channel_name_not_source_type(self):
        strategy = _make_strategy()
        video = strategy._story_to_video_dict(_make_youtube_story())
        assert video["channel_name"] == "OpenAI", (
            f"Expected creator name 'OpenAI' but got {video['channel_name']!r} — "
            "this means base_writing is reading the wrong field again (Bug A)."
        )
        # Belt-and-suspenders: the source TYPE must NOT appear here.
        assert video["channel_name"] != "youtube_trending"

    def test_fallback_to_source_channel_title_when_channel_name_missing(self):
        """Some legacy paths populate ``source_channel_title`` instead
        of ``channel_name``. Fallback chain: channel_name →
        source_channel_title → empty."""
        strategy = _make_strategy()
        story = {
            "title": "T",
            "story_id": "s",
            "source": "youtube_trending",
            "source_channel_title": "LegacyChannel",
            # no channel_name
        }
        video = strategy._story_to_video_dict(story)
        assert video["channel_name"] == "LegacyChannel"

    def test_empty_when_no_creator_field_populated(self):
        """No fetcher populated a creator field — video dict gets
        empty string (writer will then emit no credit marker)."""
        strategy = _make_strategy()
        story = {
            "title": "T",
            "story_id": "s",
            "source": "youtube_trending",
        }
        video = strategy._story_to_video_dict(story)
        assert video["channel_name"] == ""


class TestBugC_SourceAttributionPropagation:
    """Pin the ``_write_story_llm`` fix that copies
    ``result["source_attribution"]`` onto ``story["content"]``.

    Regression pattern: someone adds a new writer output field but
    forgets to propagate it through this method. Without the copy,
    push_to_backlog reads empty and the credit line never reaches
    the caption."""

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_source_attribution_propagates_to_story_content(self, mock_write_content):
        """The exact fix: writer emits source_attribution, base_writing
        must copy it to story["content"]["source_attribution"] so
        push_to_backlog's _credit helper can append it to captions."""
        mock_write_content.return_value = {
            "hook": "OpenAI shipped autonomous agents",
            "instagram_caption": "The autonomous era just started",
            "facebook_content": "The autonomous era just started",
            "youtube_content": "OpenAI's new agents",
            "twitter_content": "OpenAI's new agents",
            "tiktok_content": "OpenAI",
            "threads_content": "OpenAI shipped",
            # This is what video_content_writer emits at line ~825
            "source_attribution": "\U0001f3ac Original: @OpenAI — https://youtube.com/watch?v=rKV5JcALQoQ",
        }
        strategy = _make_strategy()
        story = _make_youtube_story()
        strategy._write_story_llm(
            story,
            llm_client=None,
            extra_instructions="",
            existing_hooks=[],
        )
        # THE pin: source_attribution reached story["content"]
        assert story["content"]["source_attribution"] == (
            "\U0001f3ac Original: @OpenAI — https://youtube.com/watch?v=rKV5JcALQoQ"
        )

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_empty_source_attribution_does_not_pollute_content(self, mock_write_content):
        """When the writer returns no source_attribution (unknown
        source, missing video_id, etc.), content must NOT gain an
        empty ``source_attribution`` key — push_to_backlog checks
        truthiness, and an empty string vs missing key has the same
        effect but a missing key is cleaner for downstream JSON
        serialization."""
        mock_write_content.return_value = {
            "hook": "h",
            "instagram_caption": "c",
            "facebook_content": "c",
            "youtube_content": "c",
            "twitter_content": "c",
            "tiktok_content": "c",
            "threads_content": "c",
            "source_attribution": "",  # writer returned empty
        }
        strategy = _make_strategy()
        story = _make_youtube_story()
        strategy._write_story_llm(
            story,
            llm_client=None,
            extra_instructions="",
            existing_hooks=[],
        )
        # Empty string source_attribution should NOT be persisted —
        # the ``if result.get("source_attribution"):`` guard means
        # story["content"] just doesn't gain the key at all
        assert "source_attribution" not in story["content"]


class TestEndToEndWire:
    """The full wire from writer result → story.content → what
    push_to_backlog sees. A test that pins THIS end-to-end because
    each individual pin misses cross-file drift."""

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_wire_covers_writer_output_to_content_dict(self, mock_write_content):
        """This is the pin that would have caught the 2026-07-13
        production failure: writer emits full content including
        source_attribution + hook_style + caption_segments; base_writing
        must propagate ALL of them so push_to_backlog's field reader
        sees them at persist time. Any future refactor that adds a
        new writer output field but forgets to update
        ``_write_story_llm``'s cherry-pick list should either add its
        field to this test's assertion set OR the missing propagation
        is a real bug."""
        writer_result = {
            "hook": "OpenAI shipped autonomous agents",
            "instagram_caption": "Content",
            "facebook_content": "Content",
            "youtube_content": "Content",
            "twitter_content": "Content",
            "tiktok_content": "Content",
            "threads_content": "Content",
            "source_attribution": "\U0001f3ac Original: @OpenAI — https://youtube.com/watch?v=abc",
            "hook_style": "counterintuitive",
            "caption_segments": [{"text": "Segment", "emphasis_words": ["Segment"]}],
        }
        mock_write_content.return_value = writer_result
        strategy = _make_strategy()
        story = _make_youtube_story()
        strategy._write_story_llm(
            story,
            llm_client=None,
            extra_instructions="",
            existing_hooks=[],
        )
        content = story["content"]
        # Every propagated writer output must be present. If a
        # future refactor removes a propagation line, this test
        # signals which one.
        assert content["hook"] == writer_result["hook"]
        assert content["caption"] == writer_result["instagram_caption"]
        assert content["source_attribution"] == writer_result["source_attribution"]
        assert content["hook_style"] == writer_result["hook_style"]
        assert content["caption_segments"] == writer_result["caption_segments"]
