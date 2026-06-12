"""Tests for the silent-failure visibility fix: ``_derive_render_failure_reason``
+ the ``fields["error_message"]`` write in push_to_backlog.

Production motivation: at the time of writing, ALL 141 stuck DRAFTED
blueprints in prod carried ``error_message=NULL`` despite obvious
upstream render failures (4 of 5 niches stuck since 2026-05-21 with
yt-dlp SABR blocks). The render-stage exceptions were getting logged
to the journal but never persisted to a queryable column, so root-
causing the outage required SSH access and journalctl spelunking.

These pins anchor the contract that closes that gap:

1. The helper produces a structured ``render:<bucket>:<detail>``
   string for each failure shape (validation / compositor /
   download / no-video / generic).
2. The helper returns an empty string only when the blueprint
   IS publishable (caller's job to gate, not ours, but we pin
   the empty branch defensively).
3. Output is capped at 500 chars so a pathological underlying
   error doesn't blow past the column budget.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.push_to_backlog import (
    _derive_render_failure_reason,
)

# ── 1. Validation failure (most precise — explicit ``valid: False``) ─


def test_validation_failure_takes_precedence() -> None:
    """``video_validation.valid=False`` produces the most specific
    bucket — pin it as priority 1 because the validation failure
    reason carries the actionable signal (VMAF score / spec violation
    / missing audio stream)."""
    story = {"media": {"video_validation": {"valid": False, "reason": "vmaf=72.3 below 85"}}}
    reason = _derive_render_failure_reason(story, {}, "s1")
    assert reason == "render:validation_failed:vmaf=72.3 below 85"


def test_validation_failure_with_missing_reason_falls_back_to_unknown() -> None:
    """Even a malformed validation dict (``valid=False`` with no
    ``reason``) must produce a complete reason string — pin so a
    refactor doesn't leak ``render:validation_failed:None`` into
    the dashboard."""
    story = {"media": {"video_validation": {"valid": False}}}
    reason = _derive_render_failure_reason(story, {}, "s1")
    assert reason == "render:validation_failed:unknown"


# ── 2. Compositor failure (the silent-failure case from prod) ────────


def test_compositor_failure_uses_render_error_string() -> None:
    """The exception string ``base_visual_render._compose_frame``
    persists on ``media.render_error`` must surface in the reason
    so operators see what the compositor actually choked on."""
    story = {
        "media": {
            "render_error": "ffmpeg returned non-zero exit code 1",
        }
    }
    reason = _derive_render_failure_reason(story, {}, "s1")
    assert reason == "render:compositor_failed:ffmpeg returned non-zero exit code 1"


def test_compositor_failure_outranks_no_video_status() -> None:
    """If both ``render_error`` AND a non-success ``render_status``
    are present, the explicit exception wins — the exception string
    carries strictly more information than the bucket label.

    A subclass that sets ``render_status="render_failed"`` AND
    ``render_error="..."`` should not produce a "no_video_found"
    reason just because render_status happens to also match a
    different branch.
    """
    story = {
        "media": {
            "render_error": "ffprobe: stream not found",
            "render_status": "render_failed",
        }
    }
    reason = _derive_render_failure_reason(story, {}, "s1")
    assert reason.startswith("render:compositor_failed:")
    assert "stream not found" in reason


# ── 3. Download failure (clip_index carries the yt-dlp error) ────────


def test_download_failure_pulls_yt_dlp_error_from_clip_index() -> None:
    """The yt-dlp error string for a failed download lives in
    ``clip_index.clips[story_id].error``. Pin that the helper
    walks this exact path so the actual error (SABR / WARP / bot
    detection / etc) reaches ``blueprint.error_message`` instead
    of a generic 'no video'."""
    clip_index = {
        "clips": {
            "s1": {
                "success": False,
                "error": "yt-dlp: HTTP 403 (likely SABR-only experiment)",
            }
        }
    }
    story = {"media": {"render_status": "no_video"}}
    reason = _derive_render_failure_reason(story, clip_index, "s1")
    assert reason.startswith("render:download_failed:")
    assert "SABR-only experiment" in reason


def test_download_failure_with_no_clip_entry_falls_back_to_no_video_found() -> None:
    """If clip_index has no entry at all for this story_id (the
    VideoSourcer's upstream branch where NO candidate URL was
    found), the helper returns ``render:no_video_found`` — distinct
    from "download attempted but failed"."""
    story = {"media": {"render_status": "no_video"}}
    reason = _derive_render_failure_reason(story, {"clips": {}}, "s1")
    assert reason == "render:no_video_found"


def test_download_failure_with_none_clip_index_falls_back_to_no_video_found() -> None:
    """Robustness: a missing clip_index (legacy pipelines or
    early-stage stories) must not crash; treat as
    ``render:no_video_found``."""
    story = {"media": {"render_status": "no_video"}}
    reason = _derive_render_failure_reason(story, None, "s1")
    assert reason == "render:no_video_found"


# ── 4. Render-failed with no captured exception (defensive branch) ───


def test_render_failed_without_exception_returns_explicit_diagnostic() -> None:
    """A subclass override that sets ``render_status="render_failed"``
    but doesn't persist ``render_error`` lands in this defensive
    branch. The reason makes the missing-exception-capture visible
    so the next operator knows to look upstream rather than at
    the compositor."""
    story = {"media": {"render_status": "render_failed"}}
    reason = _derive_render_failure_reason(story, {}, "s1")
    assert reason == "render:compositor_failed:no_exception_captured"


# ── 5. Last-resort fallback ──────────────────────────────────────────


def test_unrecognised_state_falls_back_to_not_publishable() -> None:
    """A story with unexpected media shape (no validation, no
    render_error, no recognised render_status) lands in the
    catch-all. The reason is non-empty so the dashboard never
    shows a stuck DRAFTED with empty error_message."""
    story = {"media": {"render_status": "unexpected_value"}}
    reason = _derive_render_failure_reason(story, {}, "s1")
    assert reason == "render:not_publishable"


def test_missing_media_key_does_not_crash() -> None:
    """Stories without a ``media`` key at all must produce a
    reason without raising — robustness pin."""
    story: dict = {}
    reason = _derive_render_failure_reason(story, {}, "s1")
    # No media → no validation, no render_error, render_status=""
    # → falls through the empty-string branch which looks at
    # clip_index → empty → no_video_found.
    assert reason == "render:no_video_found"


# ── 6. Truncation cap ────────────────────────────────────────────────


def test_pathological_error_truncated_to_500_chars() -> None:
    """A 5000-char ffmpeg error must not exceed 500 chars after
    the ``render:compositor_failed:`` prefix — keeps the dashboard
    readable + avoids any column-width risk."""
    story = {"media": {"render_error": "x" * 5000}}
    reason = _derive_render_failure_reason(story, {}, "s1")
    assert len(reason) == 500


# ── 7. Vocabulary stability (forward-looking dashboard groupings) ────


def test_all_reasons_share_render_namespace_prefix() -> None:
    """The dashboard groups error_message rows by prefix. Every
    reason this helper returns must start with ``render:`` so a
    future add of a different stage's failures (e.g. ``publish:``,
    ``write:``) stays cleanly separable."""
    cases = [
        ({"media": {"video_validation": {"valid": False, "reason": "x"}}}, {}, "s1"),
        ({"media": {"render_error": "x"}}, {}, "s1"),
        (
            {"media": {"render_status": "no_video"}},
            {"clips": {"s1": {"error": "x"}}},
            "s1",
        ),
        ({"media": {"render_status": "no_video"}}, {}, "s1"),
        ({"media": {"render_status": "render_failed"}}, {}, "s1"),
        ({"media": {"render_status": "unexpected"}}, {}, "s1"),
        ({}, {}, "s1"),
    ]
    for story, clip_index, sid in cases:
        reason = _derive_render_failure_reason(story, clip_index, sid)
        assert reason.startswith("render:"), f"Missing render: prefix: {reason!r}"
