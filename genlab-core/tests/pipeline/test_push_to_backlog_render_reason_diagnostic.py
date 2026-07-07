"""Pin the 2026-07-07 diagnostic fix on `_derive_render_failure_reason`.

Live-fire on 2026-07-07 caught: validate_videos.py writes ``issues``
(list) and/or ``error`` (string) on the story's video_validation,
but push_to_backlog.py only looked for ``reason`` (never set). Every
render failure logged as ``render:validation_failed:unknown`` for
weeks despite the actual issue being present in the story dict.

If this pin regresses we're back to blind diagnostics on render
failures — the operator has to attach a debugger to figure out why
gaming/anime/etc. blueprints are stuck at DRAFTED.
"""

from __future__ import annotations


def _derive(story):
    from genlab_core.pipeline.stages.push_to_backlog import _derive_render_failure_reason

    return _derive_render_failure_reason(story, clip_index=None, story_id="s-1")


class TestRenderFailureReason:
    """`_derive_render_failure_reason` reads issues list correctly."""

    def test_issues_list_becomes_comma_joined_reason(self):
        story = {
            "media": {
                "video_validation": {
                    "valid": False,
                    "issues": ["too_short:13.1s", "colorspace_mismatch"],
                }
            }
        }
        assert _derive(story) == "render:validation_failed:too_short:13.1s,colorspace_mismatch"

    def test_single_issue_still_readable(self):
        """The observed 2026-07-07 failure — a single ``too_short:13.1s``
        issue produced ``unknown`` under the old code."""
        story = {
            "media": {
                "video_validation": {
                    "valid": False,
                    "issues": ["too_short:13.1s"],
                }
            }
        }
        assert _derive(story) == "render:validation_failed:too_short:13.1s"

    def test_error_key_used_when_no_issues(self):
        """``probe_failed`` or ``exception`` under ``error`` key path."""
        story = {
            "media": {
                "video_validation": {
                    "valid": False,
                    "error": "probe_failed",
                }
            }
        }
        assert _derive(story) == "render:validation_failed:probe_failed"

    def test_legacy_reason_key_still_honored(self):
        """Not shipped anywhere today but preserving backwards compat if
        any future code path sets ``reason`` explicitly."""
        story = {
            "media": {
                "video_validation": {
                    "valid": False,
                    "reason": "legacy_key",
                }
            }
        }
        assert _derive(story) == "render:validation_failed:legacy_key"

    def test_fallback_unknown_when_no_signal_available(self):
        """Empty video_validation → the string "unknown" — matches the
        pre-fix behavior for genuinely undiagnosable failures."""
        story = {
            "media": {
                "video_validation": {
                    "valid": False,
                }
            }
        }
        assert _derive(story) == "render:validation_failed:unknown"

    def test_priority_order_issues_wins_over_error(self):
        """When both are present, issues takes precedence — it's the more
        precise signal (list of specific spec violations vs generic error
        class)."""
        story = {
            "media": {
                "video_validation": {
                    "valid": False,
                    "issues": ["too_short:12.5s"],
                    "error": "some_generic_error",
                }
            }
        }
        assert _derive(story) == "render:validation_failed:too_short:12.5s"

    def test_valid_true_returns_empty_string(self):
        """When validation passes, downstream branches select a different
        reason (compositor failure, etc.). This helper returns empty when
        video_validation.valid is not False — the caller only invokes it
        on the not-publishable branch."""
        story = {
            "media": {
                "video_validation": {
                    "valid": True,
                    "issues": [],
                },
                "render_error": "",
            }
        }
        result = _derive(story)
        # Should NOT be render:validation_failed:* — validate branch skipped
        assert not result.startswith("render:validation_failed:")
