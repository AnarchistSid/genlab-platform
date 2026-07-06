"""Pin the caption_segments propagation through ``_write_story_llm``
(task #531 — 2026-07-06 live-fire).

Production bug caught via journal grep on the transformation
orchestrator: every render since PR #705 wire logged
``skipped=['caption_style']``. 0 of 15 blueprints in the last 12h had
``caption_segments`` persisted anywhere. Direct test of
``generate_caption_segments()`` showed it works fine.

Root cause: ``_write_story_llm`` cherry-picks specific keys from
``video_content_writer.write_video_content``'s ``result`` dict onto
``story["content"]``, but forgot ``caption_segments``. The writer
generates them (PR #695) but they drop on the floor here. So the
transformation orchestrator's caption_style stage reads
``blueprint_context["caption_segments"]`` (populated from
``story.get("content", {}).get("caption_segments")`` per PR #705's
post_render_transform wire) and gets ``None``.

Three transformation bandit dimensions were affected: caption_style,
caption_pacing, caption_emphasis_color. All three consistently skipped
until this fix.

If this pin regresses, we're back to caption dimensions dormant.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_strategy():
    """Same pattern as
    ``test_base_writing_yt_title_llm_leak_guard.py``: build a stubbed
    BaseWritingStrategy that lets us call ``_write_story_llm`` with the
    real prod method body."""
    from genlab_core.strategies.base_writing import BaseWritingStrategy

    s = BaseWritingStrategy.__new__(BaseWritingStrategy)
    s.niche_id = "ai_creators"
    s._niche_id = "ai_creators"
    s._templates = {}
    s._writing_config = {}
    s._writing_cfg = {}
    return s


def _make_story() -> dict:
    return {
        "title": "AI creators are winning",
        "story_id": "s-caption-001",
        "source_url": "https://example.com/ai-creators",
    }


class TestCaptionSegmentsPropagation:
    """Pin the 2026-07-06 fix that copies ``caption_segments`` from the
    writer's ``result`` onto ``story["content"]``."""

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_caption_segments_propagate_to_story_content(
        self, mock_write_content
    ):
        """The exact fix: writer emits caption_segments, base_writing
        must copy them to story["content"]["caption_segments"] so the
        post_render_transform wire picks them up."""
        # video_content_writer generates this shape today (PR #695)
        mock_write_content.return_value = {
            "hook": "AI creators are winning big",
            "instagram_caption": "3 tools to try",
            "facebook_content": "3 tools",
            "youtube_content": "AI tools",
            "twitter_content": "AI",
            "tiktok_content": "AI",
            "threads_content": "AI",
            "caption_segments": [
                {
                    "text": "AI creators are winning big",
                    "emphasis_words": ["winning"],
                },
                {
                    "text": "3 tools transform your workflow",
                    "emphasis_words": ["transform"],
                },
            ],
        }

        s = _make_strategy()
        story = _make_story()
        s._write_story_llm(story, MagicMock(), "", [], None)

        segs = story["content"].get("caption_segments")
        assert segs is not None, (
            "caption_segments dropped between writer's result and "
            "story['content']. Transformation orchestrator's "
            "caption_style stage will silently skip on every render. "
            "See task #531 + PR #705 post_render_transform wire."
        )
        assert isinstance(segs, list)
        assert len(segs) == 2
        assert segs[0]["text"] == "AI creators are winning big"
        assert segs[0]["emphasis_words"] == ["winning"]

    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_missing_caption_segments_does_not_error(
        self, mock_write_content
    ):
        """Fail-open: if the writer's result omits caption_segments
        (older code path, or generator failed), _write_story_llm must
        not raise. Story just gets no segments, orchestrator skips
        the caption_style stage. Same as pre-fix behavior for the
        happy-path but no crash."""
        mock_write_content.return_value = {
            "hook": "AI stuff",
            "instagram_caption": "caption",
            "facebook_content": "fb",
            "youtube_content": "yt",
            "twitter_content": "tw",
            "tiktok_content": "tk",
            "threads_content": "th",
            # NO caption_segments key
        }

        s = _make_strategy()
        story = _make_story()
        # Must not raise
        s._write_story_llm(story, MagicMock(), "", [], None)

        # And the key must simply not be present (not None, not empty
        # list) so the orchestrator's ``if segments`` check trips
        # cleanly.
        assert story["content"].get("caption_segments") is None


    @patch("genlab_core.writing.video_content_writer.write_video_content")
    def test_empty_caption_segments_list_not_propagated(
        self, mock_write_content
    ):
        """Edge case: writer returns an EMPTY list. Empty list is
        falsy so ``if result.get('caption_segments'):`` is False and
        the empty list is NOT copied to story. Downstream caption_style
        will skip because segments is falsy. This matches the
        writer's own ``if seg_result is not None and seg_result.
        is_usable()`` guard."""
        mock_write_content.return_value = {
            "hook": "test",
            "instagram_caption": "c",
            "facebook_content": "fb",
            "youtube_content": "yt",
            "twitter_content": "tw",
            "tiktok_content": "tk",
            "threads_content": "th",
            "caption_segments": [],
        }

        s = _make_strategy()
        story = _make_story()
        s._write_story_llm(story, MagicMock(), "", [], None)

        assert story["content"].get("caption_segments") is None, (
            "Empty caption_segments list should NOT be copied (falsy) — "
            "otherwise downstream sees [] and can't distinguish "
            "'writer skipped' from 'writer produced nothing'."
        )
