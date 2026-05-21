"""Regression test for hook_style propagation through BaseWritingStrategy.

The 2026-05-20 RCA found that the writing stage produced LLM hooks but
never recorded the bandit-picked ``hook_style`` on the story dict, so
``push_to_backlog`` had nothing to persist and the ``style:*`` bandit
arms accumulated zero plays across 514 reward-bearing PF rows.

These tests pin the contract that ``write_video_content``'s returned
``hook_style`` lands on both ``story["hook_style"]`` (for push_to_backlog
to copy into the blueprint's extra JSONB) and ``content["hook_style"]``
(for downstream consumers).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from genlab_core.strategies.base_writing import BaseWritingStrategy


class _TestStrategy(BaseWritingStrategy):
    def __init__(self) -> None:
        super().__init__(niche_id="sports", niche_root=Path("/tmp"))


def _story():
    return {
        "story_id": "s1",
        "title": "LeBron drops 50 in Game 7",
        "summary": "Late-game heroics.",
        "source": "ESPN",
        "video_id": "v1",
    }


def test_hook_style_propagates_to_story_and_content():
    strategy = _TestStrategy()
    fake_result = {
        "hook": "Why did LeBron just leave?",
        "instagram_caption": "x",
        "twitter_content": "x",
        "youtube_content": "x",
        "facebook_content": "x",
        "threads_content": "x",
        "hook_style": "question",
    }
    # write_video_content is imported inside _write_story_llm, so patching
    # the strategies.base_writing namespace fails. Patch at the source.
    with patch(
        "genlab_core.writing.video_content_writer.write_video_content",
        return_value=fake_result,
    ):
        story = _story()
        strategy._write_story_llm(
            story,
            llm_client=object(),
            extra_instructions="",
            existing_hooks=[],
        )

    # push_to_backlog reads story["hook_style"]; both surfaces must be set.
    assert story["hook_style"] == "question"
    assert story["content"]["hook_style"] == "question"
    assert story["content"]["written_by"] == "llm"


def test_no_hook_style_when_writer_omits_it():
    """Cold-start (no style arms seeded) returns no hook_style. We must
    not synthesise one — that would credit a phantom arm."""
    strategy = _TestStrategy()
    fake_result = {
        "hook": "Solid hook",
        "instagram_caption": "x",
        "twitter_content": "x",
        "youtube_content": "x",
        "facebook_content": "x",
        "threads_content": "x",
        # no hook_style key
    }
    # write_video_content is imported inside _write_story_llm, so patching
    # the strategies.base_writing namespace fails. Patch at the source.
    with patch(
        "genlab_core.writing.video_content_writer.write_video_content",
        return_value=fake_result,
    ):
        story = _story()
        strategy._write_story_llm(
            story,
            llm_client=object(),
            extra_instructions="",
            existing_hooks=[],
        )

    assert "hook_style" not in story
    assert "hook_style" not in story["content"]
