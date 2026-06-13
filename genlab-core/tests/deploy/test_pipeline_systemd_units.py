"""Regression pin: every pipeline systemd unit runs the yt-dlp setup
script as ExecStartPre.

Task #35 (2026-06-13). The yt-dlp wrapper at /usr/local/bin/yt-dlp +
the cookies file chown were hotfixed onto the prod box on 2026-06-12
during the WARP-outage remediation. Without these baked into the
service-unit lifecycle, the next ``deploy.sh`` would silently revert
them — recreating the original "$2 burned / 0 posts" outage with no
journal trail.

This test verifies the wiring at the service-file level so a future
edit that drops the ExecStartPre line trips here, not in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SYSTEMD_DIR = _REPO_ROOT / "deploy/systemd-phase2"

_PIPELINE_UNITS = [
    "genlab-pipeline-ai.service",
    "genlab-pipeline-anime.service",
    "genlab-pipeline-gaming.service",
    "genlab-pipeline-movies.service",
    "genlab-pipeline-sports.service",
]


@pytest.mark.parametrize("unit_name", _PIPELINE_UNITS)
def test_pipeline_unit_runs_yt_dlp_setup_as_root(unit_name):
    """Every pipeline unit must run ensure_yt_dlp_environment.sh as root
    before the main pipeline starts. The `+` prefix on ExecStartPre is
    what runs it as root rather than the User=genlab unit-level user;
    /usr/local/bin write + chown need root, so the prefix is mandatory."""
    unit_path = _SYSTEMD_DIR / unit_name
    assert unit_path.exists(), f"Pipeline unit missing: {unit_path}"
    content = unit_path.read_text()

    # The ExecStartPre line MUST be present with the `+` privilege prefix.
    assert "ExecStartPre=+/opt/genlab/deploy/scripts/ensure_yt_dlp_environment.sh" in content, (
        f"{unit_name} is missing the yt-dlp setup hook. Re-add the line "
        f"`ExecStartPre=+/opt/genlab/deploy/scripts/ensure_yt_dlp_environment.sh` "
        f"or the prod hotfixes will vanish on next deploy. See "
        f"[[session-2026-06-12-outage-remediation]] for the original outage."
    )

    # And the main ExecStart MUST come after the ExecStartPre — systemd
    # runs them in declared order, but pinning both order + presence
    # catches accidental re-orderings.
    pre_idx = content.find("ExecStartPre=+/opt/genlab/deploy/scripts/ensure_yt_dlp_environment.sh")
    start_idx = content.find("ExecStart=/bin/bash")
    assert pre_idx > 0, f"{unit_name}: ExecStartPre line not found"
    assert start_idx > pre_idx, (
        f"{unit_name}: ExecStartPre must come BEFORE ExecStart "
        "so the wrapper is in place when the pipeline runs"
    )


def test_setup_script_is_executable():
    """The script itself must be marked executable in the repo so it
    survives rsync (modes are preserved by default). A non-executable
    script would silently fail when systemd tries to invoke it."""
    script = _REPO_ROOT / "deploy/scripts/ensure_yt_dlp_environment.sh"
    assert script.exists(), f"Setup script missing: {script}"
    # check executable bit
    import os

    assert os.access(script, os.X_OK), (
        f"{script} is not executable. Run `chmod +x {script}` and re-commit. "
        "Without the bit, systemd's ExecStartPre will silently fail and the "
        "hotfixes will not apply."
    )


def test_setup_script_is_idempotent_safe():
    """The script must declare `set -uo pipefail` (NOT `-e`) and `exit 0`
    at the end so a failed chown or write never blocks the pipeline.
    The CRITICAL alerts pipeline catches real download failures
    downstream; this script just lays the groundwork."""
    script = _REPO_ROOT / "deploy/scripts/ensure_yt_dlp_environment.sh"
    content = script.read_text()

    assert "set -uo pipefail" in content, (
        "Setup script must declare `set -uo pipefail` — adding `-e` would "
        "let a partial failure abort the pipeline instead of logging + "
        "continuing as designed."
    )
    assert content.rstrip().endswith("exit 0"), (
        "Setup script must end with `exit 0` so systemd never sees a "
        "non-zero exit from the pre-hook. The real-failure detection "
        "lives in pipeline_alerts.download_failure."
    )
