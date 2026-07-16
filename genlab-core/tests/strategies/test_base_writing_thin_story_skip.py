"""Pin for 2026-07-14 fix: template writer skips thin stories.

Before fix: `_write_story_template` on a story with empty hook/title/
summary silently produced `caption = "{cta}\n\n{hashtags}"` — a defective
caption with no story content that still shipped downstream as a valid
blueprint. Observed live on anime blueprint 32719aa2 producing:

    Caught up yet?

    #Anime #AnimeReels

    🎬 Original: @Онака — https://youtube.com/watch?v=IPFdc8pbKZs

After fix: `_build_caption` returns "" on empty context; `_write_story_
template` detects it, sets `story["_skip_llm"] = True`, and does NOT
populate `content["instagram"]` etc. Downstream stages read the skip
signal and drop the story before render.

Class-of-bug: silent quality degradation — output is syntactically
"valid" (non-empty string, well-formed) but semantically bankrupt
(no story-specific content). Same shape as writer LLM refusal preambles
and empty attribution before this session's fixes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.strategies.base_writing import BaseWritingStrategy


def _make_strategy() -> BaseWritingStrategy:
    s = BaseWritingStrategy.__new__(BaseWritingStrategy)
    s.niche_id = "anime"
    s._niche_id = "anime"
    s._templates = {
        "captions": {
            "cta_library": ["Caught up yet?", "Peak or mid?"],
            "hashtag_pool": ["#Anime", "#AnimeReels", "#Manga"],
            "hashtags_per_post": 2,
            "target_length": 300,
        },
    }
    s._writing_config = {}
    s._llm_client = MagicMock()
    return s


class TestBuildCaptionEmptyContextReturnsEmpty:
    def test_no_hook_no_title_no_summary_returns_empty_string(self):
        s = _make_strategy()
        # Story with genuinely no writable content — all three of hook,
        # title, summary are empty.
        story = {"content": {}}
        caption = s._build_caption(story)
        assert caption == "", f"Empty-context story should produce empty caption, got: {caption!r}"

    def test_hook_alone_still_produces_content(self):
        """Guard: don't be over-aggressive — a hook alone is enough."""
        s = _make_strategy()
        story = {"content": {"hook": "Chainsaw Man just dropped."}}
        caption = s._build_caption(story)
        assert caption != ""
        assert "Chainsaw Man" in caption

    def test_title_alone_still_produces_content(self):
        s = _make_strategy()
        story = {"title": "Attack on Titan Final Cour", "content": {}}
        caption = s._build_caption(story)
        assert caption != ""
        assert "Attack on Titan" in caption

    def test_summary_alone_still_produces_content(self):
        s = _make_strategy()
        story = {"summary": "Berserk animation studio just leaked storyboards for the eclipse arc."}
        caption = s._build_caption(story)
        assert caption != ""
        assert "Berserk" in caption


class TestWriteStoryTemplateEmptyCaptionSetsSkip:
    def test_empty_context_story_marks_skip_llm_true(self):
        s = _make_strategy()
        story = {"content": {}}
        result = s._write_story_template(story)
        # skip signal must be propagated
        assert result.get("_skip_llm") is True

    def test_empty_context_story_does_not_populate_platform_content(self):
        """The defective platform-content dicts must NOT be created;
        downstream pipeline stages should treat this story as unwritable,
        not merely 'has empty caption'."""
        s = _make_strategy()
        story = {"content": {}}
        s._write_story_template(story)
        content = story.get("content", {})
        # If we HAD populated these fields with empty strings, downstream
        # would happily render + push a defective blueprint. Absent is
        # the correct signal.
        assert "instagram" not in content
        assert "facebook" not in content
        assert "youtube" not in content
        assert "x_twitter" not in content
        assert "threads" not in content

    def test_healthy_story_still_populates_content(self):
        """Guard: don't regress the healthy path."""
        s = _make_strategy()
        story = {
            "title": "Berserk news",
            "summary": "Studio Mappa leaks episode 12 sneak preview.",
            "content": {},
        }
        s._write_story_template(story)
        content = story.get("content", {})
        assert content.get("caption")
        assert "instagram" in content
        assert story.get("_skip_llm") is not True
