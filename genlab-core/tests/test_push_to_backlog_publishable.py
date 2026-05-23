"""R-47: VISUAL_READY must be gated on video validation, not just rendered_path.

Previously push_to_backlog set status="VISUAL_READY" whenever a rendered_path
existed and never inspected media["video_validation"]["valid"], so a video that
FAILED the VMAF / spec / no-audio gate still went VISUAL_READY + auto-scheduled.
"""

from __future__ import annotations

from genlab_core.pipeline.stages.push_to_backlog import _is_publishable


def test_rendered_and_valid_is_publishable():
    assert _is_publishable({"rendered_path": "/x.mp4", "video_validation": {"valid": True}}) is True


def test_rendered_but_validation_failed_is_not_publishable():
    # The whole point of R-47: a VMAF/spec/no-audio failure must NOT ship.
    assert (
        _is_publishable({"rendered_path": "/x.mp4", "video_validation": {"valid": False}}) is False
    )


def test_rendered_with_no_validation_falls_back_to_publishable():
    # Absent validation = "not checked" -> don't stall the pipeline.
    assert _is_publishable({"rendered_path": "/x.mp4"}) is True


def test_no_render_is_not_publishable():
    assert _is_publishable({"video_validation": {"valid": True}}) is False
    assert _is_publishable({}) is False
    assert _is_publishable(None) is False
