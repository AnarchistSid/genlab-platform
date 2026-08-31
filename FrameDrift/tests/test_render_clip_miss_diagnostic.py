"""Every way the renderer can fail to find a clip must say which one it was.

2026-08-31. Anime published nothing for ten days. The clips downloaded (2/2),
VideoGate passed both, and the stories reached the render stage — GenerateAudio
produced 2 and RenderTextOverlays saw 2. Yet `clips.get(story["story_id"])`
matched nothing and every story fell through to the no-video path, silently,
because the fall-through logged nothing at all.

Four causes are possible and each needs a different fix:

    no_story_id    the story never carried one
    id_mismatch    clips are keyed by an id the story does not have
    not_success    the entry exists but the download failed
    file_missing   a path was recorded but is gone from disk

Distinguishing them is the whole point. A single "no clip found" line would
have left the same investigation to do.
"""
from __future__ import annotations

import logging

import pytest

from fd_strategies.visual_render import AnimeVisualRenderStrategy


class _Strategy(AnimeVisualRenderStrategy):
    def __init__(self):  # bypass config/IO
        pass

    def _ensure_config(self):
        pass

    def _render_story(self, story):
        return story

    def _compose_frame(self, *a, **k):
        return None


GOOD_ID = "a" * 64
FAILED_ID = "c" * 64
CLIPS = {
    GOOD_ID: {"success": True, "clip_path": "/nonexistent/gone.mp4"},
    FAILED_ID: {"success": False, "clip_path": ""},
}


def _run(story, caplog):
    ctx = {"stories": [story], "clip_index": {"clips": dict(CLIPS)}}
    with caplog.at_level(logging.WARNING):
        out = _Strategy().execute(ctx)
    return out, caplog.text


class TestMissIsClassified:
    @pytest.mark.parametrize("story,expected", [
        ({"title": "t", "story_id": ""}, "no_story_id"),
        ({"title": "t", "story_id": "b" * 64}, "id_mismatch"),
        ({"title": "t", "story_id": FAILED_ID}, "not_success"),
        ({"title": "t", "story_id": GOOD_ID}, "file_missing"),
    ])
    def test_each_cause_is_named(self, caplog, story, expected):
        _, text = _run(story, caplog)
        assert expected in text, (
            f"expected the miss to be classified as {expected}; got: {text[:300]}"
        )

    def test_story_id_and_available_keys_are_logged(self, caplog):
        """The whole diagnostic value is seeing BOTH sides of the failed
        lookup — the id we searched for, and what was actually there."""
        _, text = _run({"title": "t", "story_id": "b" * 64}, caplog)
        assert "b" * 20 in text, "the story_id we looked up is not in the log"
        assert "clip_index has 2 key(s)" in text, "available keys not reported"
        assert "aaaaaaaaaaaaaaaa" in text, "a sample of the real keys is missing"

    def test_key_sample_is_bounded(self, caplog):
        """A run with many clips must not dump them all into the log."""
        many = {f"{i:064d}": {"success": False, "clip_path": ""} for i in range(50)}
        ctx = {"stories": [{"title": "t", "story_id": "zz"}],
               "clip_index": {"clips": many}}
        with caplog.at_level(logging.WARNING):
            _Strategy().execute(ctx)
        assert "clip_index has 50 key(s)" in caplog.text
        assert caplog.text.count("…") <= 6, "key sample is not bounded"


class TestSuccessPathStaysQuiet:
    def test_no_warning_when_a_clip_is_found(self, tmp_path, caplog):
        """The diagnostic must not fire on healthy runs, or it becomes noise."""
        clip = tmp_path / "real.mp4"
        clip.write_bytes(b"\x00" * 16)
        ctx = {"stories": [{"title": "t", "story_id": GOOD_ID}],
               "clip_index": {"clips": {GOOD_ID: {"success": True,
                                                  "clip_path": str(clip)}}}}
        with caplog.at_level(logging.WARNING):
            _Strategy().execute(ctx)
        for token in ("no_story_id", "id_mismatch", "not_success", "file_missing"):
            assert token not in caplog.text, f"{token} fired on a healthy clip"


class TestCounterIsHonest:
    def test_rendered_counts_the_fallback_too(self, caplog):
        """`rendered` increments on the no-video fallback, so it equals the
        story count even when nothing was made. `videos_found` is the honest
        number — pinned so a reader of run_stats is not misled."""
        out, _ = _run({"title": "t", "story_id": "b" * 64}, caplog)
        stats = out["run_stats"]["render"]
        assert stats["rendered"] == 1
        assert stats["videos_found"] == 0
