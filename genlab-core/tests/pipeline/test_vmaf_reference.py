"""R-25: the VMAF gate must not diff against an invalid reference.

Previously video_gate set master_path to the raw downloaded clip, so VMAF
compared the branded 1080x1920 reel against the unbranded landscape source —
a meaningless score that triggered a wasted CRF-12 re-encode every run. The
gate now skips unless a same-framing master is available.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from genlab_core.pipeline.stages.validate_videos import ValidateVideos

_MOD = "genlab_core.pipeline.stages.validate_videos"


def _probe_dims(w: int, h: int) -> dict:
    return {"streams": [{"codec_type": "video", "width": w, "height": h}]}


def test_vmaf_skips_when_no_master(tmp_path: Path) -> None:
    # R-07: tuple return — when the master is missing the gate fail-opens
    # to "pass" BUT must surface the skip_reason so callers can record
    # ``vmaf_skipped=True`` for observability.
    stage = ValidateVideos()
    with patch(f"{_MOD}.check_vmaf") as mock_vmaf:
        result = stage._run_vmaf_gate({}, str(tmp_path / "reel.mp4"))
    assert result == ("pass", "no_master")
    mock_vmaf.assert_not_called()


def test_vmaf_skips_on_dimension_mismatch(tmp_path: Path) -> None:
    master = tmp_path / "master.mp4"
    master.write_bytes(b"x")
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x")
    stage = ValidateVideos()

    def fake_probe(p):
        return _probe_dims(1920, 1080) if Path(p) == master else _probe_dims(1080, 1920)

    with (
        patch.object(ValidateVideos, "_probe", staticmethod(fake_probe)),
        patch(f"{_MOD}.check_vmaf") as mock_vmaf,
    ):
        result = stage._run_vmaf_gate({"master_path": str(master)}, str(reel))

    # R-07: dim_mismatch is the skip_reason — gate fail-opened, not real pass.
    assert result == ("pass", "dim_mismatch")
    mock_vmaf.assert_not_called()  # mismatched framing -> no garbage VMAF, no re-encode


def test_vmaf_runs_when_dimensions_match(tmp_path: Path) -> None:
    master = tmp_path / "master.mp4"
    master.write_bytes(b"x")
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x")
    stage = ValidateVideos()

    with (
        patch.object(ValidateVideos, "_probe", staticmethod(lambda p: _probe_dims(1080, 1920))),
        patch(f"{_MOD}.check_vmaf", return_value=(True, 92.0)) as mock_vmaf,
    ):
        result = stage._run_vmaf_gate({"master_path": str(master)}, str(reel))

    # R-07: skip_reason is None on a REAL pass (gate produced a verdict).
    assert result == ("pass", None)
    mock_vmaf.assert_called_once()


def test_r07_vmaf_failopen_score_zero_marks_skipped(tmp_path: Path) -> None:
    """R-07: ``check_vmaf`` returns (True, 0.0) when libvmaf is missing
    or the VMAF log was unparseable — the score=0.0 sentinel. The gate
    must propagate that as ``skip_reason='infra_or_log_unreadable'`` so
    callers can stamp ``vmaf_skipped=True`` on the post."""
    master = tmp_path / "master.mp4"
    master.write_bytes(b"x")
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x")
    stage = ValidateVideos()

    with (
        patch.object(ValidateVideos, "_probe", staticmethod(lambda p: _probe_dims(1080, 1920))),
        # Mirrors the real check_vmaf fail-open contract.
        patch(f"{_MOD}.check_vmaf", return_value=(True, 0.0)),
    ):
        result = stage._run_vmaf_gate({"master_path": str(master)}, str(reel))

    assert result == ("pass", "infra_or_log_unreadable")


def test_video_dims_extraction() -> None:
    assert ValidateVideos._video_dims(_probe_dims(1080, 1920)) == (1080, 1920)
    assert ValidateVideos._video_dims(None) is None
    assert ValidateVideos._video_dims({"streams": []}) is None
    assert ValidateVideos._video_dims({"streams": [{"codec_type": "audio"}]}) is None
