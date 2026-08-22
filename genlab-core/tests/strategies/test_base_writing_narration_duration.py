"""Pin the narration duration-fallback fix (NARR-03 2026-08-18).

Prod trigger at 2026-08-18 15:11 UTC (BB pipeline manual run) exposed
this bug: every BB story had ``duration_seconds`` unpopulated on the
story→video dict, so ``base_writing._write_story_llm`` deferred to the
legacy 6-field output and skipped narration entirely. GenerateAudio then
logged the DEGRADED marker for both stories.

Fix: fall back to 30s baseline reel-midpoint when no duration is found;
the post-synth A4 vo_overrun probe catches any actual mismatch.

Pin here so any future refactor to the story→video dict path doesn't
silently re-break narration.
"""
from __future__ import annotations

import inspect

from genlab_core.strategies import base_writing


class TestDurationFallback:
    """Structural pin — the fallback path must exist in _write_story_llm
    and default to a sensible reel-midpoint value."""

    def test_source_contains_30s_fallback(self):
        src = inspect.getsource(base_writing._write_story_llm) \
            if hasattr(base_writing, "_write_story_llm") \
            else inspect.getsource(base_writing.BaseWritingStrategy._write_story_llm)
        # Sanity: the fallback path is present + hardcoded to a numeric
        # default. The value can move (30/45/60) but the "else" branch
        # must NOT return None here.
        assert "narration_target_seconds = 30.0" in src, (
            "duration fallback missing — prod bug 2026-08-18 will recur"
        )

    def test_source_tries_multiple_duration_locations(self):
        """NARR-08 (2026-08-19): the chain moved out of ``_write_story_llm``
        into ``_resolve_render_duration_seconds`` and gained a first source.

        Two changes to what this pin asserts:

        * ``video.get("duration_seconds")`` is GONE and its removal is not a
          loss. ``_story_to_video_dict`` never emitted that key, so the
          branch was dead from the day it was written — which is precisely
          why every BB story fell through to the 30s default this file was
          created to pin.
        * The renderer's trim window is now consulted FIRST. It has to
          outrank story metadata: when ``highlight_moment`` is enabled the
          renderer trims to that window, so it IS the reel length no matter
          what the source says. On 2026-08-19 story_0 the source clip was
          356.6s and the reel was 18.60s.
        """
        src = inspect.getsource(
            base_writing.BaseWritingStrategy._resolve_render_duration_seconds
        )
        assert "window_seconds" in src, (
            "must consult the renderer's trim target — sizing to the source "
            "clip produced a ~354s budget for story_0"
        )
        assert 'story.get("duration_seconds")' in src
        assert '"clip_duration_seconds"' in src

    def test_render_window_outranks_source_metadata(self):
        """Behavioural companion to the structural pin above.

        A 356s source clip against BB's configured ``window_seconds`` must
        resolve THAT — not 30 (the old default) and not 356 (the file on disk).

        Reads the window from config rather than pinning a literal. The literal
        made #226 (a deliberate 16 -> 28 change on 2026-08-22) look like a
        regression in two unrelated files; the property under test is the
        ORDERING, not the number.
        """
        from pathlib import Path

        repo = Path(__file__).resolve().parents[3]

        class _S(base_writing.BaseWritingStrategy):
            def _model_route_key(self) -> str:
                return "test"

        resolved = _S("ai_creators", repo / "BlackboxBrief")._resolve_render_duration_seconds(
            {"story_id": "s0"},
            {"clips": {"s0": {"duration_seconds": 356.588844}}},
        )
        import yaml

        cfg = yaml.safe_load((repo / "BlackboxBrief" / "config" / "visuals.yaml").read_text())
        windows: list = []

        def _walk(node):
            if isinstance(node, dict):
                if "window_seconds" in node:
                    windows.append(float(node["window_seconds"]))
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)

        _walk(cfg)
        assert windows, "BB visuals.yaml has no highlight_moment.window_seconds"
        assert resolved == windows[0], (
            f"expected BB's configured render window {windows[0]}s, got {resolved}"
        )
        assert resolved != 30.0, "must not fall back to the 30s default"
        assert resolved < 356.0, "must not size to the untrimmed source clip"
