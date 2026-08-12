"""Pin the writer's near-dupe hook retry behavior.

Contract:

  * Flag off (default) -> retry never fires; first result returned as-is.
  * Flag on + first result has hook NOT near-dupe -> retry not called.
  * Flag on + first result near-dupe + retry produces non-dupe hook ->
    retry result adopted.
  * Flag on + first result near-dupe + retry ALSO near-dupe -> first
    result kept (downstream push_to_backlog drops it, unchanged
    behavior).
  * Flag on + retry raises -> first result kept.
  * Flag on + retry returns empty hook -> first result kept.

The retry augments extra_instructions with an explicit avoid-hint
naming both the rejected hook and its match. The rejected hook is
appended to existing_hooks so the retry sees BOTH the historical
dupe AND its own prior attempt.

The helper is `_maybe_retry_on_near_dupe` on BaseWritingStrategy.
Tests exercise it directly to avoid the full _write_story_llm setup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from genlab_core.strategies.base_writing import BaseWritingStrategy


class _MinimalWriter(BaseWritingStrategy):
    """Concrete subclass just so we can instantiate + call the helper."""


@pytest.fixture
def writer(tmp_path):
    return _MinimalWriter(niche_id="sports", niche_root=tmp_path)


class TestFlagGating:
    def test_flag_off_returns_first_unchanged(self, writer, monkeypatch):
        monkeypatch.delenv("GENLAB_HOOK_NEAR_DUPE_RETRY_ENABLED", raising=False)
        first = {"hook": "the greatest goal of the season"}
        result = writer._maybe_retry_on_near_dupe(
            first_result=first,
            video={"title": "t"},
            existing_hooks=["the greatest play of the season"],  # would be a near-dupe
            extra_instructions="",
            llm_client=MagicMock(),
        )
        assert result is first  # exact same dict, no retry

    def test_no_existing_hooks_no_retry(self, writer, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_NEAR_DUPE_RETRY_ENABLED", "1")
        first = {"hook": "some fresh hook here"}
        result = writer._maybe_retry_on_near_dupe(
            first_result=first,
            video={"title": "t"},
            existing_hooks=[],
            extra_instructions="",
            llm_client=MagicMock(),
        )
        assert result is first


class TestRetryFires:
    @pytest.fixture(autouse=True)
    def _flag_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_NEAR_DUPE_RETRY_ENABLED", "1")

    def test_retry_success_swaps_to_retry_result(self, writer, caplog):
        first = {"hook": "the greatest goal of the season"}
        existing = ["the greatest play of the season"]
        retry_result = {"hook": "completely fresh angle nobody expected"}

        with patch(
            "genlab_core.writing.video_content_writer.write_video_content",
            return_value=retry_result,
        ) as mock_writer, caplog.at_level(logging.INFO):
            out = writer._maybe_retry_on_near_dupe(
                first_result=first,
                video={"title": "t"},
                existing_hooks=existing,
                extra_instructions="baseline_instructions",
                llm_client=MagicMock(),
            )
        assert out is retry_result
        # The retry sees BOTH the historical dupe AND the first attempt
        call_kwargs = mock_writer.call_args.kwargs
        assert first["hook"] in call_kwargs["existing_hooks"]
        assert "CRITICAL RETRY" in call_kwargs["extra_instructions"]
        assert "the greatest play of the season" in call_kwargs["extra_instructions"]
        assert any("RETRY_SUCCESS" in r.message for r in caplog.records)

    def test_retry_also_near_dupe_keeps_first(self, writer, caplog):
        first = {"hook": "the greatest goal of the season"}
        existing = ["the greatest play of the season"]
        # Retry produces yet another dupe against 'the greatest play'
        retry_result = {"hook": "the greatest play of last season"}

        with patch(
            "genlab_core.writing.video_content_writer.write_video_content",
            return_value=retry_result,
        ), caplog.at_level(logging.WARNING):
            out = writer._maybe_retry_on_near_dupe(
                first_result=first,
                video={"title": "t"},
                existing_hooks=existing,
                extra_instructions="",
                llm_client=MagicMock(),
            )
        assert out is first
        assert any("RETRY_FAILED" in r.message for r in caplog.records)

    def test_retry_raises_keeps_first(self, writer, caplog):
        first = {"hook": "the greatest goal of the season"}
        existing = ["the greatest play of the season"]

        with patch(
            "genlab_core.writing.video_content_writer.write_video_content",
            side_effect=RuntimeError("simulated LLM failure"),
        ), caplog.at_level(logging.WARNING):
            out = writer._maybe_retry_on_near_dupe(
                first_result=first,
                video={"title": "t"},
                existing_hooks=existing,
                extra_instructions="",
                llm_client=MagicMock(),
            )
        assert out is first
        # No exception propagates
        assert any(
            "retry write_video_content raised" in r.message for r in caplog.records
        )

    def test_retry_empty_hook_keeps_first(self, writer, caplog):
        first = {"hook": "the greatest goal of the season"}
        existing = ["the greatest play of the season"]
        retry_result = {"hook": ""}  # writer's own validation zeroed it

        with patch(
            "genlab_core.writing.video_content_writer.write_video_content",
            return_value=retry_result,
        ), caplog.at_level(logging.INFO):
            out = writer._maybe_retry_on_near_dupe(
                first_result=first,
                video={"title": "t"},
                existing_hooks=existing,
                extra_instructions="",
                llm_client=MagicMock(),
            )
        assert out is first
        assert any("RETRY_EMPTY" in r.message for r in caplog.records)

    def test_first_not_near_dupe_no_retry(self, writer):
        """When first attempt is already good, retry never fires."""
        first = {"hook": "totally unrelated fresh angle"}
        existing = ["boring existing hook here"]
        with patch(
            "genlab_core.writing.video_content_writer.write_video_content",
        ) as mock_writer:
            out = writer._maybe_retry_on_near_dupe(
                first_result=first,
                video={"title": "t"},
                existing_hooks=existing,
                extra_instructions="",
                llm_client=MagicMock(),
            )
        assert out is first
        mock_writer.assert_not_called()

    def test_retry_result_includes_all_writer_fields(self, writer):
        """The retry adoption is whole-result — includes new captions,
        not just the new hook. Guards against 'kept old captions with
        new hook' bug."""
        first = {
            "hook": "the greatest goal of the season",
            "instagram_caption": "old ig",
            "twitter_content": "old tw",
        }
        retry_result = {
            "hook": "completely fresh angle nobody saw",
            "instagram_caption": "new ig",
            "twitter_content": "new tw",
        }
        with patch(
            "genlab_core.writing.video_content_writer.write_video_content",
            return_value=retry_result,
        ):
            out = writer._maybe_retry_on_near_dupe(
                first_result=first,
                video={"title": "t"},
                existing_hooks=["the greatest play of the season"],
                extra_instructions="",
                llm_client=MagicMock(),
            )
        assert out["instagram_caption"] == "new ig"
        assert out["twitter_content"] == "new tw"
