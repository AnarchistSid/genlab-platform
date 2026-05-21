"""Tests for video_content_writer."""
from unittest.mock import MagicMock

from genlab_core.writing.video_content_writer import NICHE_VOICE, write_video_content


def _make_video(title="Bam Adebayo drops 83 points", channel="ESPN"):
    return {
        "video_id": "abc123",
        "title": title,
        "channel_name": channel,
        "view_count": 500000,
        "age_hours": 3,
        "view_velocity": 166666,
        "description_snippet": "Historic performance from Bam Adebayo",
        "tags": ["nba", "sports", "highlights"],
    }


def _make_llm(response: str):
    client = MagicMock()
    client.complete.return_value = response
    return client


class TestVideoContentWriter:
    def test_generates_all_platform_fields(self):
        llm = _make_llm(
            '{"hook":"Bam just dropped 83","instagram_caption":"Historic night '
            '#Sports","twitter_content":"83 points","youtube_content":"Did Bam just?",'
            '"facebook_content":"83 points tonight"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert "hook" in result
        assert "instagram_caption" in result
        assert "twitter_content" in result
        assert "youtube_content" in result
        assert "facebook_content" in result

    def test_hook_truncated_to_60_chars(self):
        long_hook = "A" * 100
        llm = _make_llm(
            f'{{"hook":"{long_hook}","instagram_caption":"x",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert len(result["hook"]) <= 60

    def test_fallback_on_llm_failure(self):
        llm = _make_llm("not valid json at all")
        result = write_video_content(_make_video(title="Short title"), "sports", llm)
        assert "hook" in result
        assert result["hook"] == "Short title"

    def test_fallback_truncates_long_title(self):
        long_title = "A" * 100
        llm = _make_llm("invalid")
        result = write_video_content(_make_video(title=long_title), "sports", llm)
        assert len(result["hook"]) <= 60
        assert result["hook"].endswith("...")

    def test_niche_voice_defined_for_all_channels(self):
        for niche in ["gaming", "sports", "movies", "anime", "ai_creators"]:
            assert niche in NICHE_VOICE
            voice = NICHE_VOICE[niche]
            assert "account" in voice
            assert "style" in voice
            assert "hashtags" in voice
            assert len(voice["hashtags"]) >= 3

    def test_existing_hooks_passed_to_llm(self):
        llm = _make_llm(
            '{"hook":"New hook","instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        write_video_content(
            _make_video(), "sports", llm,
            existing_hooks=["Old hook 1", "Old hook 2"],
        )
        # Verify the LLM was called with system prompt containing existing hooks
        call_args = llm.complete.call_args
        assert "Old hook 1" in call_args.kwargs.get("system", "")

    def test_instagram_hashtags_added_if_missing(self):
        llm = _make_llm(
            '{"hook":"Test","instagram_caption":"No hashtags here",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert "#Sports" in result["instagram_caption"]

    def test_handles_markdown_code_fences(self):
        llm = _make_llm(
            '```json\n{"hook":"Works","instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}\n```'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert result["hook"] == "Works"

    def test_extra_instructions_in_system_prompt(self):
        llm = _make_llm(
            '{"hook":"Test","instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        write_video_content(
            _make_video(), "sports", llm,
            extra_instructions="BANNED PHRASES:\n  - the sports world is watching",
        )
        call_args = llm.complete.call_args
        system_prompt = call_args.kwargs.get("system", "")
        assert "BANNED PHRASES" in system_prompt
        assert "the sports world is watching" in system_prompt

    def test_extra_instructions_empty_no_effect(self):
        llm = _make_llm(
            '{"hook":"Test","instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(_make_video(), "sports", llm, extra_instructions="")
        call_args = llm.complete.call_args
        system_prompt = call_args.kwargs.get("system", "")
        # When extra_instructions is empty, the sports-specific phrase must
        # NOT be in the prompt. (The built-in BANNED PHRASES section IS
        # always in the prompt now — that's intentional, it's the generic
        # LLM-tic block from the quality rewrite.)
        assert "the sports world is watching" not in system_prompt
        assert result["hook"] == "Test"

    def test_extra_instructions_before_json_instruction(self):
        llm = _make_llm(
            '{"hook":"Ok","instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        write_video_content(
            _make_video(), "sports", llm,
            extra_instructions="TONE: Be exciting",
        )
        call_args = llm.complete.call_args
        system_prompt = call_args.kwargs.get("system", "")
        # extra_instructions should appear before the JSON-only instruction
        tone_pos = system_prompt.index("TONE: Be exciting")
        json_pos = system_prompt.index("Respond ONLY with valid JSON")
        assert tone_pos < json_pos


class TestHookStylePropagation:
    """The bandit-picked hook style must reach the returned content dict
    so push_to_backlog can persist it and the metric_collector can credit
    the ``style:{niche}:{name}`` arm at 48h. Regression guard for the
    2026-05-20 silent failure where style:* arms were stuck at n_plays=0
    because the writing stage never recorded the chosen style.
    """

    def test_hook_style_added_when_picker_returns_one(self, monkeypatch):
        monkeypatch.setattr(
            "genlab_core.writing.llm_hook_generator.pick_hook_style",
            lambda _niche: "question",
        )
        # Avoid the "[entity] just [verb]" banned pattern by phrasing as
        # a clean question with no "just".
        llm = _make_llm(
            '{"hook":"Why is the league panicking about Bam?",'
            '"instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert result.get("hook_style") == "question"
        # Style hint should also appear in the LLM system prompt
        system = llm.complete.call_args.kwargs.get("system", "")
        assert "STYLE TARGET" in system

    def test_hook_style_omitted_when_picker_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "genlab_core.writing.llm_hook_generator.pick_hook_style",
            lambda _niche: None,
        )
        llm = _make_llm(
            '{"hook":"Cold start hook","instagram_caption":"x #Sports",'
            '"twitter_content":"x","youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        # No style picked = cold start → no hook_style key (would otherwise
        # send false reward to a phantom arm).
        assert "hook_style" not in result
        system = llm.complete.call_args.kwargs.get("system", "")
        assert "STYLE TARGET" not in system

    def test_hook_style_omitted_when_hook_was_rejected(self, monkeypatch):
        # Banned phrase trips the post-LLM filter and clears the hook;
        # crediting the style arm for a hook that didn't actually ship
        # would teach the bandit the wrong lesson.
        monkeypatch.setattr(
            "genlab_core.writing.llm_hook_generator.pick_hook_style",
            lambda _niche: "bold_claim",
        )
        llm = _make_llm(
            '{"hook":"This changes everything for sports",'
            '"instagram_caption":"x #Sports","twitter_content":"x",'
            '"youtube_content":"x","facebook_content":"x"}'
        )
        result = write_video_content(_make_video(), "sports", llm)
        assert result.get("hook") == ""
        assert "hook_style" not in result
