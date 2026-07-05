"""Pin the structured caption-segments writer (PR 10).

Covers:
  1. CaptionSegment model validation (text stripped, emphasis stripped)
  2. Clean JSON parse (happy path)
  3. Code-fence-wrapped JSON parse
  4. JSON with preamble text
  5. Malformed JSON returns None
  6. Missing keys returns None (unusable)
  7. Word-count clamp: 12-word segment cut to 8
  8. Word-count clamp: 1-word segment dropped
  9. Emphasis word not in segment text is dropped
 10. Emphasis words capped at 2
 11. Hashtag normalization: adds #, drops empty, caps at 5
 12. generate_caption_segments: no API key returns None
 13. Prompt shape includes niche_id, title, hook, JSON schema

No live API calls — parse_llm_response tested against canned strings.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from genlab_core.writing.caption_segments import (
    CaptionSegment,
    CaptionSegmentsResult,
    _clamp_hashtags,
    _clamp_segments,
    _extract_json_blob,
    _prompt_for_segments,
    generate_caption_segments,
    parse_llm_response,
)


class TestCaptionSegmentModel:
    def test_text_stripped(self) -> None:
        seg = CaptionSegment(text="  hello world  ", emphasis_words=[])
        assert seg.text == "hello world"

    def test_emphasis_stripped_and_empties_dropped(self) -> None:
        seg = CaptionSegment(
            text="hello world",
            emphasis_words=["  hello  ", "", "  ", "world"],
        )
        assert seg.emphasis_words == ["hello", "world"]

    def test_word_count(self) -> None:
        assert CaptionSegment(
            text="one two three four", emphasis_words=[]
        ).word_count() == 4


class TestExtractJsonBlob:
    def test_plain_json(self) -> None:
        raw = '{"segments": [], "hashtags": []}'
        assert _extract_json_blob(raw) == raw

    def test_code_fence_json(self) -> None:
        raw = '```json\n{"segments": [], "hashtags": []}\n```'
        assert _extract_json_blob(raw) == '{"segments": [], "hashtags": []}'

    def test_code_fence_no_lang(self) -> None:
        raw = '```\n{"segments": [], "hashtags": []}\n```'
        assert _extract_json_blob(raw) == '{"segments": [], "hashtags": []}'

    def test_preamble_text(self) -> None:
        raw = 'Here is your JSON:\n\n{"segments": [], "hashtags": []}\n\nEnjoy!'
        result = _extract_json_blob(raw)
        assert result == '{"segments": [], "hashtags": []}'

    def test_no_json_returns_none(self) -> None:
        assert _extract_json_blob("no json here") is None
        assert _extract_json_blob("") is None

    def test_nested_braces(self) -> None:
        raw = '{"segments": [{"text": "abc"}], "hashtags": ["#a"]}'
        assert _extract_json_blob(raw) == raw


class TestParseLLMResponse:
    def test_happy_path(self) -> None:
        raw = """
        {
          "segments": [
            {"text": "OpenAI just dropped", "emphasis_words": ["OpenAI"]},
            {"text": "the fastest AI video model", "emphasis_words": ["fastest"]},
            {"text": "and it's completely FREE", "emphasis_words": ["FREE"]}
          ],
          "hashtags": ["#ai", "#openai", "#creators"]
        }
        """
        result = parse_llm_response(raw)
        assert result is not None
        assert result.is_usable()
        assert len(result.segments) == 3
        assert result.hashtags == ["#ai", "#openai", "#creators"]

    def test_malformed_json_returns_none(self) -> None:
        assert parse_llm_response("{ not valid json") is None

    def test_missing_segments_returns_none(self) -> None:
        raw = '{"hashtags": ["#a"]}'
        # Zero segments < _MIN_SEGMENTS(1) → unusable → None
        assert parse_llm_response(raw) is None

    def test_only_one_valid_segment_still_usable(self) -> None:
        """_MIN_SEGMENTS=1 — even 1 segment beats hook-only."""
        raw = """
        {"segments": [
          {"text": "This changes everything", "emphasis_words": ["everything"]}
        ], "hashtags": []}
        """
        result = parse_llm_response(raw)
        assert result is not None
        assert len(result.segments) == 1

    def test_wrapped_in_code_fence(self) -> None:
        raw = """
        Sure, here's your JSON:
        ```json
        {
          "segments": [
            {"text": "AI just broke", "emphasis_words": ["broke"]},
            {"text": "the entire internet", "emphasis_words": ["internet"]}
          ],
          "hashtags": ["#ai"]
        }
        ```
        """
        result = parse_llm_response(raw)
        assert result is not None
        assert len(result.segments) == 2

    def test_non_dict_json_returns_none(self) -> None:
        """Array at top level, not dict."""
        assert parse_llm_response("[1, 2, 3]") is None


class TestClampSegments:
    def test_11_word_segment_clamped_to_8(self) -> None:
        raw = [
            CaptionSegment(
                text="one two three four five six seven eight nine ten eleven",
                emphasis_words=[],
            )
        ]
        clamped = _clamp_segments(raw)
        assert len(clamped) == 1
        assert clamped[0].word_count() == 8

    def test_1_word_segment_dropped(self) -> None:
        raw = [
            CaptionSegment(text="hello", emphasis_words=[]),
            CaptionSegment(text="second segment here", emphasis_words=[]),
        ]
        clamped = _clamp_segments(raw)
        # 1-word segment dropped, second (3 words) kept
        assert len(clamped) == 1
        assert clamped[0].text == "second segment here"

    def test_max_5_segments(self) -> None:
        """Never more than 5 segments — anything above is truncated."""
        raw = [
            CaptionSegment(text=f"seg {i} text", emphasis_words=[])
            for i in range(10)
        ]
        clamped = _clamp_segments(raw)
        assert len(clamped) == 5

    def test_emphasis_not_in_text_dropped(self) -> None:
        raw = [
            CaptionSegment(
                text="hello world how are you",
                emphasis_words=["hello", "notinsegment", "world"],
            )
        ]
        clamped = _clamp_segments(raw)
        # 'notinsegment' filtered — kept only 'hello' + 'world' (first 2)
        assert clamped[0].emphasis_words == ["hello", "world"]

    def test_emphasis_capped_at_2(self) -> None:
        raw = [
            CaptionSegment(
                text="one two three four five",
                emphasis_words=["one", "two", "three", "four"],
            )
        ]
        clamped = _clamp_segments(raw)
        assert len(clamped[0].emphasis_words) == 2

    def test_emphasis_case_insensitive_match(self) -> None:
        """LLM sometimes returns emphasis in different case than segment."""
        raw = [
            CaptionSegment(
                text="This changes EVERYTHING now",
                emphasis_words=["everything"],  # lowercase in emphasis
            )
        ]
        clamped = _clamp_segments(raw)
        assert clamped[0].emphasis_words == ["everything"]

    def test_emphasis_ignores_punctuation(self) -> None:
        """'FREE!' in text should match 'FREE' in emphasis."""
        raw = [
            CaptionSegment(
                text="and it's completely FREE!",
                emphasis_words=["FREE"],
            )
        ]
        clamped = _clamp_segments(raw)
        assert clamped[0].emphasis_words == ["FREE"]


class TestClampHashtags:
    def test_adds_hash_prefix(self) -> None:
        result = _clamp_hashtags(["ai", "creators"])
        assert result == ["#ai", "#creators"]

    def test_normalizes_double_hash(self) -> None:
        result = _clamp_hashtags(["##ai"])
        assert result == ["#ai"]

    def test_empty_dropped(self) -> None:
        result = _clamp_hashtags(["", "  ", "#ai"])
        assert result == ["#ai"]

    def test_caps_at_5(self) -> None:
        result = _clamp_hashtags(["#a", "#b", "#c", "#d", "#e", "#f", "#g"])
        assert result == ["#a", "#b", "#c", "#d", "#e"]


class TestPromptShape:
    def test_prompt_includes_niche_id(self) -> None:
        p = _prompt_for_segments("gaming", "T", "S", "H")
        assert "gaming" in p

    def test_prompt_includes_title_and_hook(self) -> None:
        p = _prompt_for_segments("gaming", "TITLE-X", "S", "HOOK-Y")
        assert "TITLE-X" in p
        assert "HOOK-Y" in p

    def test_prompt_includes_json_schema(self) -> None:
        p = _prompt_for_segments("gaming", "T", "S", "H")
        assert "segments" in p
        assert "emphasis_words" in p
        assert "hashtags" in p

    def test_prompt_truncates_long_summary(self) -> None:
        long_summary = "x" * 500
        p = _prompt_for_segments("gaming", "T", long_summary, "H")
        # Should include 300 chars max of summary
        assert "x" * 300 in p
        assert "x" * 400 not in p


class TestGenerateCaptionSegments:
    def test_no_api_key_returns_none(self, monkeypatch) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = generate_caption_segments(
            {"title": "T", "summary": "S"}, "gaming", "hook"
        )
        assert result is None

    def test_no_title_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        with patch("anthropic.Anthropic"):
            result = generate_caption_segments(
                {"title": "", "summary": "S"}, "gaming", "hook"
            )
        assert result is None

    def test_no_hook_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        with patch("anthropic.Anthropic"):
            result = generate_caption_segments(
                {"title": "T", "summary": "S"}, "gaming", ""
            )
        assert result is None

    def test_api_error_returns_none(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

        class _FakeAnthropic:
            def __init__(self, api_key):
                pass

            @property
            def messages(self):
                class _M:
                    @staticmethod
                    def create(**kwargs):
                        raise RuntimeError("simulated API failure")

                return _M

        with patch("anthropic.Anthropic", _FakeAnthropic):
            result = generate_caption_segments(
                {"title": "T", "summary": "S"}, "gaming", "hook"
            )
        assert result is None

    def test_happy_path_end_to_end(self, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")

        class _FakeText:
            def __init__(self, text: str):
                self.text = text

        class _FakeResponse:
            def __init__(self, text: str):
                self.content = [_FakeText(text)]

        class _FakeAnthropic:
            def __init__(self, api_key):
                pass

            @property
            def messages(self):
                class _M:
                    @staticmethod
                    def create(**kwargs):
                        return _FakeResponse(
                            """
                            {"segments": [
                                {"text": "OpenAI just launched", "emphasis_words": ["OpenAI"]},
                                {"text": "the fastest AI video", "emphasis_words": ["fastest"]},
                                {"text": "and it's completely FREE", "emphasis_words": ["FREE"]}
                            ], "hashtags": ["#ai", "#openai"]}
                            """
                        )

                return _M

        with patch("anthropic.Anthropic", _FakeAnthropic):
            result = generate_caption_segments(
                {"title": "T", "summary": "S"}, "gaming", "OpenAI drops it"
            )
        assert result is not None
        assert len(result.segments) == 3
        assert result.segments[0].emphasis_words == ["OpenAI"]
        assert "#ai" in result.hashtags


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
