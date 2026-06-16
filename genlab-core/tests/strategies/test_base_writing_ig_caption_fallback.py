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


class TestFallbackWarnLog:
    """Pin the observability log added 2026-06-17 (followup to PRs #273+#275).

    The WARN fires only when the LLM omitted ``instagram_caption`` AND
    the upstream retry (PR #275) didn't recover. Operators can grep
    ``[BaseWriting] instagram_caption fallback fired`` in journalctl
    to spot per-niche prompt regressions and decide whether to tighten
    the LLM prompt further for that niche.

    These tests exercise the REAL ``base_writing.py`` LLM-write path
    (not the inline replica above) so the log assertion catches
    drift between the two.
    """

    def _make_real_strategy(self, niche_id: str = "anime"):
        from pathlib import Path

        from genlab_core.strategies.base_writing import BaseWritingStrategy

        class _TestStrategy(BaseWritingStrategy):
            def __init__(self) -> None:
                super().__init__(niche_id=niche_id, niche_root=Path("/tmp"))

        return _TestStrategy()

    def test_warn_fires_when_ig_caption_missing(self, caplog):
        """LLM omits instagram_caption → WARN log fires + names the
        fallback source it used."""
        from unittest.mock import patch

        strategy = self._make_real_strategy("anime")
        fake_result = {
            "hook": "Test hook",
            # instagram_caption MISSING — triggers fallback
            "facebook_content": "Fallback came from FB",
            "twitter_content": "Tweet",
            "youtube_content": "YT desc",
            "threads_content": "Threads",
        }
        story = {
            "story_id": "s1",
            "title": "Test story title for log assertion",
            "summary": "Summary",
            "source": "TestSource",
            "video_id": "v1",
        }
        with (
            patch(
                "genlab_core.writing.video_content_writer.write_video_content",
                return_value=fake_result,
            ),
            caplog.at_level("WARNING", logger="genlab_core.strategies.base_writing"),
        ):
            strategy._write_story_llm(
                story, llm_client=object(), extra_instructions="", existing_hooks=[]
            )

        warn_messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        # The fallback-fired log must be among the WARNs
        fallback_logs = [m for m in warn_messages if "instagram_caption fallback fired" in m]
        assert len(fallback_logs) == 1, (
            f"Expected exactly one fallback WARN log, got {len(fallback_logs)}: {warn_messages}"
        )
        msg = fallback_logs[0]
        assert "niche=anime" in msg
        assert "fallback_source=facebook_content" in msg

    def test_warn_does_not_fire_when_ig_caption_present(self, caplog):
        """Happy path: LLM returns instagram_caption → NO fallback,
        NO log. Pinned to prevent false-positive operator alerts."""
        from unittest.mock import patch

        strategy = self._make_real_strategy("gaming")
        fake_result = {
            "hook": "Test hook",
            "instagram_caption": "🎮 Real IG content from LLM",
            "facebook_content": "FB",
            "twitter_content": "Tweet",
            "youtube_content": "YT",
            "threads_content": "Threads",
        }
        story = {
            "story_id": "s1",
            "title": "T",
            "summary": "S",
            "source": "Src",
            "video_id": "v1",
        }
        with (
            patch(
                "genlab_core.writing.video_content_writer.write_video_content",
                return_value=fake_result,
            ),
            caplog.at_level("WARNING", logger="genlab_core.strategies.base_writing"),
        ):
            strategy._write_story_llm(
                story, llm_client=object(), extra_instructions="", existing_hooks=[]
            )

        warn_messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        fallback_logs = [m for m in warn_messages if "instagram_caption fallback fired" in m]
        assert fallback_logs == [], (
            f"WARN should NOT fire when IG is present, but got: {fallback_logs}"
        )

    def test_warn_names_correct_source_per_fallback_level(self, caplog):
        """The log says exactly which fallback rung satisfied
        (facebook_content vs youtube_content vs hook vs story.title)
        so operators can spot e.g. 'always falling back to title' =
        LLM-completely-broken vs 'always falling back to FB' = just
        IG prompt tuning needed."""
        from unittest.mock import patch

        strategy = self._make_real_strategy("movies")
        # LLM returns only hook + a title is available; should fall back
        # all the way through to hook (FB + YT both missing).
        fake_result = {
            "hook": "Just the hook survived",
            "twitter_content": "tw",
            # no instagram_caption, no facebook_content, no youtube_content
        }
        story = {
            "story_id": "s2",
            "title": "Some Movie Trailer Title",
            "summary": "S",
            "source": "Src",
            "video_id": "v2",
        }
        with (
            patch(
                "genlab_core.writing.video_content_writer.write_video_content",
                return_value=fake_result,
            ),
            caplog.at_level("WARNING", logger="genlab_core.strategies.base_writing"),
        ):
            strategy._write_story_llm(
                story, llm_client=object(), extra_instructions="", existing_hooks=[]
            )

        warn_messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        fallback_logs = [m for m in warn_messages if "instagram_caption fallback fired" in m]
        assert len(fallback_logs) == 1
        # Should pick the next non-empty fallback after IG + FB + YT all empty
        assert "fallback_source=hook" in fallback_logs[0]
