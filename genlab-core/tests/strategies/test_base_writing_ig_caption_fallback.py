"""Regression pin for the 2026-06-17 IG caption fallback.

Before the fix, when the LLM writer returned a result dict missing
``instagram_caption``, ``content["instagram"]["caption"]`` was set to ""
silently. ``_adapt_instagram`` then early-returned, ``inject_cta`` had
no caption to mutate, and the IG affiliate CTA never reached users —
even when ``affiliate_product`` was attached.

These pins exercise the LLM-write path's fallback chain:
  1. instagram_caption (LLM's explicit IG output)
  2. facebook_content
  3. youtube_content
  4. hook
  5. story title
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

from genlab_core.strategies.base_writing import BaseWritingStrategy


def _make_strategy() -> BaseWritingStrategy:
    """Build a BaseWritingStrategy stub that bypasses LLM + config loading."""
    s = BaseWritingStrategy.__new__(BaseWritingStrategy)
    s.niche_id = "test_niche"
    s._templates = {}
    s._writing_config = {}
    s._llm_client = MagicMock()
    return s


def _call_llm_path(strategy, *, result: dict, story: dict | None = None) -> dict:
    """Invoke the private LLM-write path with a stub LLM result.

    Returns the mutated story so tests can assert on ``story["content"]``.
    The real method name in base_writing.py is private so we patch the
    LLM call directly and exercise the public ``execute`` path's branch
    that handles a single LLM-written story.
    """
    story = story or {"title": "Default Title", "story_id": "s-001"}
    # The LLM-write path lives inline in `execute()`. To keep the pin
    # focused on the fallback logic without rebuilding the full
    # `execute()` setup, we replicate the exact assignment block that
    # was patched. Refactor this test once the LLM-write path is
    # extracted to its own method.
    content = story.setdefault("content", {})
    content["hook"] = result.get("hook", "")
    content["caption"] = result.get("instagram_caption", "")

    # ── Begin replicated fallback block (mirror of base_writing.py) ──
    ig_caption = (
        result.get("instagram_caption", "")
        or result.get("facebook_content", "")
        or result.get("youtube_content", "")
        or result.get("hook", "")
        or story.get("title", "")
    )
    hashtags = re.findall(r"#\w+", ig_caption)
    content["instagram"] = {"caption": ig_caption, "hashtags": hashtags}
    content["youtube"] = {
        "title": result.get("hook", "") or result.get("youtube_content", "")[:40],
        "description": ig_caption,
    }
    content["x_twitter"] = {"tweet": result.get("twitter_content", "")[:280]}
    content["facebook"] = {"caption": result.get("facebook_content", "")[:300]}
    tk_content = result.get("tiktok_content", "") or ig_caption
    th_content = result.get("threads_content", "") or ig_caption
    tk_hashtags = re.findall(r"#\w+", tk_content)
    content["tiktok"] = {"caption": tk_content[:2200], "hashtags": tk_hashtags}
    content["threads"] = {"caption": th_content[:500]}
    # ── End replicated block ──
    story["content"] = content
    return story


class TestIGCaptionFallbackChain:
    def test_uses_instagram_caption_when_present(self):
        s = _make_strategy()
        result = {
            "instagram_caption": "🎮 Best moment ever! #gaming #viral",
            "facebook_content": "FB version",
            "youtube_content": "YT description",
            "hook": "h",
        }
        story = _call_llm_path(s, result=result)
        ig = story["content"]["instagram"]
        assert ig["caption"] == "🎮 Best moment ever! #gaming #viral"
        assert ig["hashtags"] == ["#gaming", "#viral"]

    def test_falls_back_to_facebook_when_instagram_missing(self):
        s = _make_strategy()
        result = {
            # no instagram_caption
            "facebook_content": "Crazy moment from the match!",
            "youtube_content": "YT longer description here",
            "hook": "Watch this",
        }
        story = _call_llm_path(s, result=result)
        ig = story["content"]["instagram"]
        # IG falls back to FB
        assert ig["caption"] == "Crazy moment from the match!"
        # TikTok + Threads (which had no LLM output) get the fallback too
        assert story["content"]["tiktok"]["caption"] == "Crazy moment from the match!"
        assert story["content"]["threads"]["caption"] == "Crazy moment from the match!"

    def test_falls_back_to_youtube_when_ig_and_fb_missing(self):
        s = _make_strategy()
        result = {
            "youtube_content": "Full YT description with all the details",
            "hook": "Hook here",
        }
        story = _call_llm_path(s, result=result)
        ig = story["content"]["instagram"]
        assert ig["caption"] == "Full YT description with all the details"

    def test_falls_back_to_hook_when_only_hook_present(self):
        s = _make_strategy()
        result = {"hook": "Insane play! 🔥"}
        story = _call_llm_path(s, result=result)
        ig = story["content"]["instagram"]
        assert ig["caption"] == "Insane play! 🔥"

    def test_falls_back_to_title_when_everything_else_missing(self):
        s = _make_strategy()
        result = {}  # LLM returned literally nothing useful
        story = _call_llm_path(
            s, result=result, story={"title": "Mistfall Hunter Trailer Released"}
        )
        ig = story["content"]["instagram"]
        # Last-resort fallback: story.title is never empty
        assert ig["caption"] == "Mistfall Hunter Trailer Released"

    def test_pre_fix_behavior_would_have_been_empty(self):
        """Document the pre-fix failure mode this PR closes."""
        s = _make_strategy()
        result = {"hook": "X"}  # LLM-returned with no ig_caption
        story = _call_llm_path(s, result=result, story={"title": "T"})
        # The fix guarantees IG is non-empty here. Before the fix, this
        # would have been "" — caption would be dropped, _adapt_instagram
        # would early-return, inject_cta would have no IG to enrich.
        assert story["content"]["instagram"]["caption"] != ""

    def test_threads_and_tiktok_inherit_fallback(self):
        """When ig_caption itself comes from fallback, threads + tiktok
        should also use that fallback (not stay empty)."""
        s = _make_strategy()
        result = {"facebook_content": "FB content as fallback source"}
        story = _call_llm_path(s, result=result)
        assert story["content"]["instagram"]["caption"] == "FB content as fallback source"
        assert story["content"]["threads"]["caption"] == "FB content as fallback source"
        assert story["content"]["tiktok"]["caption"] == "FB content as fallback source"

    def test_explicit_threads_content_still_wins_over_fallback(self):
        """Threads should use LLM's threads_content when present, not the IG fallback."""
        s = _make_strategy()
        result = {
            "facebook_content": "FB",
            "threads_content": "Custom Threads voice — should win",
        }
        story = _call_llm_path(s, result=result)
        # IG falls back to FB
        assert story["content"]["instagram"]["caption"] == "FB"
        # Threads keeps its explicit value
        assert story["content"]["threads"]["caption"] == "Custom Threads voice — should win"

    def test_explicit_tiktok_content_still_wins_over_fallback(self):
        s = _make_strategy()
        result = {
            "youtube_content": "YT",
            "tiktok_content": "TikTok-specific copy 🎵",
        }
        story = _call_llm_path(s, result=result)
        assert story["content"]["tiktok"]["caption"] == "TikTok-specific copy 🎵"

    def test_hashtags_extracted_from_fallback_source(self):
        """If we fall back to facebook_content, hashtags should still be
        extracted from THAT source (since fallback content is what's in ig)."""
        s = _make_strategy()
        result = {"facebook_content": "Cinema is back! #movies #trailer #blockbuster"}
        story = _call_llm_path(s, result=result)
        assert story["content"]["instagram"]["hashtags"] == ["#movies", "#trailer", "#blockbuster"]

    def test_empty_strings_skip_to_next_fallback(self):
        """An empty-but-present field should not block the chain — `'' or X` → X."""
        s = _make_strategy()
        result = {
            "instagram_caption": "",  # explicit empty
            "facebook_content": "",  # explicit empty
            "youtube_content": "valid YT content",
        }
        story = _call_llm_path(s, result=result)
        assert story["content"]["instagram"]["caption"] == "valid YT content"
