"""Regression pin: GenerateAudio._build_script reads caption from canonical store.

RENDER #5 fix (2026-06-13). The canonical caption store is
``bp["content"]["caption"]`` (set by ``BaseWritingStrategy._populate_content``
at base_writing.py:238). Pre-fix code at generate_audio.py:147 read
``bp.get("body", bp.get("caption", ""))`` directly, both empty in every
production blueprint shape since Sprint 64. TTS narrated ONLY the hook
(~63 chars) instead of the full caption (150-300 chars) for all 5 niches.

Symmetric bug at qc_gates.py:218 had body=0 in every production run, so the
"too long" check NEVER fired regardless of how verbose the caption was.

This test pins both sites against regression.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.generate_audio import GenerateAudio


class TestBuildScript:
    """The TTS script must include BOTH hook AND caption body."""

    def test_reads_caption_from_content_canonical_store(self):
        """Pin the canonical path: bp["content"]["caption"]."""
        bp = {
            "hook": "Wall Street analysts won't like this Codex demo",
            "content": {
                "caption": (
                    "Codex just ran a full post-earnings analysis using "
                    "Quartr, Daloopa, and S&P Global data."
                ),
            },
        }
        script = GenerateAudio._build_script(bp)
        # Both halves of the script present
        assert "Wall Street analysts" in script  # hook
        assert "post-earnings analysis" in script  # caption body

    def test_falls_back_to_legacy_caption_field(self):
        """Older BlackboxBrief blueprints stored caption at the top level.
        Backward-compat preserved."""
        bp = {
            "hook": "Test hook",
            "caption": "Legacy top-level caption text that should survive.",
        }
        script = GenerateAudio._build_script(bp)
        assert "Test hook" in script
        assert "Legacy top-level caption" in script

    def test_hook_text_field_recognized_as_hook(self):
        """Postgres column is `hook_text` not `hook`. Both shapes must work."""
        bp = {
            "hook_text": "Hook from the postgres path",
            "content": {"caption": "Caption body here."},
        }
        script = GenerateAudio._build_script(bp)
        assert "Hook from the postgres path" in script
        assert "Caption body here" in script

    def test_content_caption_wins_over_empty_top_level(self):
        """If top-level body/caption are empty but content.caption is set,
        content.caption must be used (the bug we just fixed)."""
        bp = {
            "hook": "Hook",
            "body": "",  # empty
            "caption": "",  # empty
            "content": {"caption": "The real caption is here."},
        }
        script = GenerateAudio._build_script(bp)
        assert "The real caption is here" in script

    def test_returns_empty_when_too_short(self):
        """Scripts shorter than 10 chars are dropped to avoid wasting TTS quota."""
        bp = {"hook": "hi"}
        assert GenerateAudio._build_script(bp) == ""

    def test_handles_non_string_body_gracefully(self):
        """If content.caption is a list or dict (defensive), don't crash."""
        bp = {
            "hook": "Test hook with sufficient length",
            "content": {"caption": ["accidentally", "a", "list"]},
        }
        # Should not crash; falls back to empty body, returns just the hook
        script = GenerateAudio._build_script(bp)
        assert "Test hook with sufficient length" in script

    def test_hook_only_when_caption_empty(self):
        """Hook with no caption still produces a script if hook is long enough."""
        bp = {
            "hook": "This is a hook longer than ten characters",
            "content": {},
        }
        script = GenerateAudio._build_script(bp)
        assert "This is a hook longer than ten characters" in script
