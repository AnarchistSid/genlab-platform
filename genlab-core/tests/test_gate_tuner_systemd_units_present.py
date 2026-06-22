"""Pin: gate_tuner systemd unit files exist in deploy/systemd-phase2/.

Background: 2026-06-22 audit found that `gate_tuner` (Lever A, PR #417)
had never run in production because no systemd timer was created.
Calibration data was accumulating since 2026-06-13 but gate thresholds
never adapted.

This pin guards against the systemd unit files being accidentally
deleted from the deploy directory. The actual installation to
`/etc/systemd/system/` on the production VPS is a separate operator
step (`cp + systemctl daemon-reload + systemctl enable --now`), and
this test cannot verify that. But it CAN verify that the templates
exist + are syntactically valid systemd unit files.

If this test fails, the gate_tuner timer was probably removed
accidentally during a deploy cleanup. Restore from git history.
"""

from __future__ import annotations

import re
from pathlib import Path


_DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd-phase2"


class TestGateTunerSystemdUnits:
    def test_service_file_exists(self):
        """Pin: service unit template must exist."""
        path = _DEPLOY_DIR / "genlab-gate-tuner.service"
        assert path.exists(), (
            f"Missing systemd service unit at {path}. The gate_tuner "
            f"timer requires both .service and .timer templates."
        )

    def test_timer_file_exists(self):
        """Pin: timer unit template must exist."""
        path = _DEPLOY_DIR / "genlab-gate-tuner.timer"
        assert path.exists(), (
            f"Missing systemd timer unit at {path}. Without the timer, "
            f"the service won't fire on a schedule."
        )

    def test_service_runs_correct_module(self):
        """Pin: ExecStart must call the gate_tuner module — never a
        different one. Catches accidental copy-paste from auto-approver
        template that leaves wrong module name."""
        content = (_DEPLOY_DIR / "genlab-gate-tuner.service").read_text()
        assert "python -m genlab_core.learning.gate_tuner" in content, (
            "Service unit ExecStart does not invoke "
            "`python -m genlab_core.learning.gate_tuner`. Check for "
            "copy-paste errors from sibling units."
        )

    def test_service_runs_as_genlab_not_root(self):
        """Pin: must run as genlab user — never root. Permission drift
        is a chronic issue (root-owned files in /opt/genlab break
        subsequent runs); see permissions_repair runbook."""
        content = (_DEPLOY_DIR / "genlab-gate-tuner.service").read_text()
        assert re.search(r"^User=genlab$", content, re.MULTILINE), (
            "Service must run as User=genlab (not root). Root-owned "
            "writes to .tmp/gate_overrides.json would break subsequent "
            "atomic writes from the regular pipeline user."
        )

    def test_service_loads_dotenv(self):
        """Pin: must load /opt/genlab/.env for DATABASE_URL +
        GENLAB_PROJECT_ROOT. Without env, tuner cannot find the
        calibration table."""
        content = (_DEPLOY_DIR / "genlab-gate-tuner.service").read_text()
        assert "EnvironmentFile=/opt/genlab/.env" in content, (
            "Service must load /opt/genlab/.env — gate_tuner needs "
            "DATABASE_URL to query the calibration table."
        )

    def test_timer_fires_before_first_auto_approve(self):
        """Pin: timer must fire BEFORE the 06:00 UTC auto-approver
        first-fire so freshly-tuned thresholds apply to that day's
        decisions. Anything ≥ 06:00 UTC would leave the morning's
        first auto-approver pass running on yesterday's stale
        thresholds."""
        content = (_DEPLOY_DIR / "genlab-gate-tuner.timer").read_text()
        m = re.search(r"OnCalendar=\*-\*-\* (\d{2}):", content)
        assert m, "Timer must have OnCalendar=*-*-* HH:MM:SS UTC"
        hour = int(m.group(1))
        assert hour < 6, (
            f"Timer fires at {hour:02d}:00 UTC — must be < 06:00 UTC "
            f"to land BEFORE the first auto-approver fire of the day."
        )

    def test_timer_has_persistent_catchup(self):
        """Pin: Persistent=true so a missed nightly fire after reboot
        gets caught up. Without this, a Sunday-night VPS reboot would
        skip Monday's tuning entirely."""
        content = (_DEPLOY_DIR / "genlab-gate-tuner.timer").read_text()
        assert "Persistent=true" in content, (
            "Timer must set Persistent=true — without it, a reboot at "
            "the wrong time silently drops one night's tuning."
        )
