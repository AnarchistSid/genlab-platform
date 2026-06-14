"""2026-06-14 captioned-MP4 guard: PushToBacklog must drop stale
``_captioned.mp4`` paths when the current niche config has
whisper_sync disabled.

Background: for ~10 days the dashboard served stale ``_captioned.mp4``
variants for blueprints rendered while whisper was enabled — those
variants have a regressed text_optimizer bug that produces giant
overlay text. PR #177's text_optimizer revival fixes new renders;
this guard ensures the same class of bug can't recur in future
config flips.

See [[session-2026-06-13-render-audit-findings]] for the underlying
text_optimizer regression and [[session-2026-06-14-deploy-pipeline-gap]]
for the catalyst that surfaced this specific bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from genlab_core.pipeline.stages.push_to_backlog import (
    _strip_captioned_when_whisper_disabled,
)

# ────────────────────────────────────────────────────────────────────────────
# whisper DISABLED in config — guard fires
# ────────────────────────────────────────────────────────────────────────────


def test_captioned_path_swapped_when_whisper_disabled(tmp_path: Path):
    """The canonical case: whisper off in config + path ends _captioned.mp4
    + non-captioned sibling exists -> guard swaps to sibling."""
    base = tmp_path / "story_reel.mp4"
    captioned = tmp_path / "story_reel_captioned.mp4"
    base.write_bytes(b"\x00")  # non-captioned sibling must exist
    captioned.write_bytes(b"\x00")  # source path

    config = {"animation": {"word_by_word": {"whisper_sync": {"enabled": False}}}}
    result = _strip_captioned_when_whisper_disabled(str(captioned), config)
    assert result == str(base), "should have stripped _captioned suffix"


def test_captioned_path_kept_when_whisper_disabled_but_sibling_missing(tmp_path: Path):
    """Don't break a publish just because the rename guess fails.
    If the non-captioned sibling doesn't exist, fall through to the
    original (captioned) path — no worse than pre-guard behavior."""
    captioned = tmp_path / "story_reel_captioned.mp4"
    captioned.write_bytes(b"\x00")  # sibling intentionally NOT created

    config = {"animation": {"word_by_word": {"whisper_sync": {"enabled": False}}}}
    result = _strip_captioned_when_whisper_disabled(str(captioned), config)
    assert result == str(captioned), "should fall through to original path"


# ────────────────────────────────────────────────────────────────────────────
# whisper ENABLED in config — guard is a no-op (operator wants captions)
# ────────────────────────────────────────────────────────────────────────────


def test_captioned_path_kept_when_whisper_enabled(tmp_path: Path):
    """Operator explicitly opted into captions via visuals.yaml. Don't
    second-guess them — keep the captioned path even when a non-captioned
    sibling exists."""
    base = tmp_path / "story_reel.mp4"
    captioned = tmp_path / "story_reel_captioned.mp4"
    base.write_bytes(b"\x00")
    captioned.write_bytes(b"\x00")

    config = {"animation": {"word_by_word": {"whisper_sync": {"enabled": True}}}}
    result = _strip_captioned_when_whisper_disabled(str(captioned), config)
    assert result == str(captioned), "should keep captioned path when whisper enabled"


# ────────────────────────────────────────────────────────────────────────────
# Non-captioned input — guard is a no-op (common case, performance pin)
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/opt/genlab/x/story_reel.mp4",
        "/opt/genlab/x/story_reel_branded.mp4",
        "/opt/genlab/x/some_other_overlaid.mp4",  # ends in _overlaid not _captioned
        "",  # empty path
    ],
)
def test_non_captioned_path_is_returned_unchanged(path: str):
    """The common case: most rendered paths don't end in _captioned.mp4.
    The guard must be a fast no-op for them — single suffix check, no
    config lookup or filesystem stat."""
    config = {"animation": {"word_by_word": {"whisper_sync": {"enabled": False}}}}
    assert _strip_captioned_when_whisper_disabled(path, config) == path


# ────────────────────────────────────────────────────────────────────────────
# Config-shape robustness — guard tolerates partial / missing config
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "config",
    [
        {},  # empty config
        {"animation": {}},  # animation exists but no word_by_word
        {"animation": {"word_by_word": {}}},  # no whisper_sync
        {"animation": {"word_by_word": {"whisper_sync": {}}}},  # no enabled
        {"visuals": {"animation": {"word_by_word": {"whisper_sync": {"enabled": False}}}}},
    ],
)
def test_guard_treats_missing_config_as_whisper_disabled(tmp_path: Path, config: dict):
    """Default is "whisper disabled" when the config doesn't say
    otherwise — matches the conservative behavior of
    RenderWhisperCaptions._get_whisper_config which also defaults to
    {"enabled": False}. The visuals.animation fallback is exercised to
    cover the nested-config-shape niches use."""
    base = tmp_path / "story_reel.mp4"
    captioned = tmp_path / "story_reel_captioned.mp4"
    base.write_bytes(b"\x00")
    captioned.write_bytes(b"\x00")

    result = _strip_captioned_when_whisper_disabled(str(captioned), config)
    assert result == str(base), (
        f"config {config!r} should be treated as whisper-disabled and trigger swap"
    )
