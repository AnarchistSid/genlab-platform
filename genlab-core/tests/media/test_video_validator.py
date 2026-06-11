"""R-07: ``check_vmaf`` fail-open contract — visibility for the
two distinct skip paths.

Before R-07 both ``subprocess.run`` failure (libvmaf missing) and JSON
parse failure (VMAF log unreadable) shared a single WARN with the same
message "(libvmaf unavailable or parse error)". Ops had no way to tell
a routine no-libvmaf skip from the silent-failure mode that bit us in
May 2026 (pipe-buffer deadlock left the log truncated, every check
fail-opened invisibly).

After R-07 the two paths log distinctly — INFO for the infra skip,
ERROR for the unreadable-log skip — both still returning the
(True, 0.0) fail-open contract because the caller depends on it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

from genlab_core.media.video_validator import check_vmaf


def test_check_vmaf_subprocess_failure_logs_infra_skip(tmp_path: Path, caplog) -> None:
    """When libvmaf is missing (subprocess raises) → INFO log with
    structured field ``vmaf_skipped_infra``, returns ``(True, 0.0)``."""
    master = tmp_path / "m.mp4"
    master.write_bytes(b"x")
    variant = tmp_path / "v.mp4"
    variant.write_bytes(b"x")

    with (
        caplog.at_level(logging.INFO, logger="genlab_core.media.video_validator"),
        # Patch the binary resolver so the function reaches the subprocess
        # call — CI environments don't always have ffmpeg installed.
        patch("genlab_core.media.ffmpeg.get_ffmpeg_binary", return_value="ffmpeg"),
        patch(
            "genlab_core.media.video_validator.subprocess.run",
            side_effect=FileNotFoundError("ffmpeg not found"),
        ),
    ):
        passed, score = check_vmaf(master, variant, "instagram")

    assert passed is True
    assert score == 0.0
    # Distinct identifier for the infra-skip path.
    assert any("vmaf_skipped_infra" in r.message for r in caplog.records)
    # Must NOT be misclassified as the silent-failure mode.
    assert not any("vmaf_log_unreadable" in r.message for r in caplog.records)


def test_check_vmaf_log_unreadable_logs_error(tmp_path: Path, caplog) -> None:
    """When the VMAF log is unparseable (the silent-failure mode that
    bit us in May 2026) → ERROR log with ``vmaf_log_unreadable``,
    still returns ``(True, 0.0)`` to preserve fail-open shape but at a
    log level that triggers alerting."""
    master = tmp_path / "m.mp4"
    master.write_bytes(b"x")
    variant = tmp_path / "v.mp4"
    variant.write_bytes(b"x")

    with (
        caplog.at_level(logging.ERROR, logger="genlab_core.media.video_validator"),
        patch("genlab_core.media.ffmpeg.get_ffmpeg_binary", return_value="ffmpeg"),
        patch("genlab_core.media.video_validator.subprocess.run"),  # succeeds
        patch(
            "builtins.open",
            side_effect=json.JSONDecodeError("malformed", "", 0),
        ),
    ):
        passed, score = check_vmaf(master, variant, "instagram")

    assert passed is True
    assert score == 0.0
    assert any("vmaf_log_unreadable" in r.message for r in caplog.records)
    # The two paths must NOT be conflated.
    assert not any("vmaf_skipped_infra" in r.message for r in caplog.records)


def test_check_vmaf_log_unreadable_uses_error_level(tmp_path: Path, caplog) -> None:
    """The silent-failure mode is a real bug worth alerting on — log
    level must be ERROR (not WARNING/INFO) so observability picks it up."""
    master = tmp_path / "m.mp4"
    master.write_bytes(b"x")
    variant = tmp_path / "v.mp4"
    variant.write_bytes(b"x")

    with (
        caplog.at_level(logging.DEBUG, logger="genlab_core.media.video_validator"),
        patch("genlab_core.media.ffmpeg.get_ffmpeg_binary", return_value="ffmpeg"),
        patch("genlab_core.media.video_validator.subprocess.run"),
        patch(
            "builtins.open",
            side_effect=ValueError("log empty"),
        ),
    ):
        check_vmaf(master, variant, "instagram")

    log_unreadable_records = [r for r in caplog.records if "vmaf_log_unreadable" in r.message]
    assert log_unreadable_records, "expected at least one vmaf_log_unreadable record"
    assert all(r.levelno == logging.ERROR for r in log_unreadable_records)
