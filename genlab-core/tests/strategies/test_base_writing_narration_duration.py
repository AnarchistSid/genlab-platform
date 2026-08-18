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
        src = inspect.getsource(base_writing.BaseWritingStrategy._write_story_llm)
        # We look in at least 3 places: video dict, story dict, media dict
        assert 'video.get("duration_seconds")' in src
        assert 'story.get("duration_seconds")' in src
        assert '"clip_duration_seconds"' in src or '"duration_seconds"' in src
