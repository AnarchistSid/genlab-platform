"""Pin: detect_metric_drift script + systemd units present + wired correctly.

Background
----------
The engagement_rate=0 bug (analytics_store.py:214, commit f32b6189 on
2026-06-09) went undetected for 17 days because no automated check was
watching the distribution shape of the headline bandit-reward metric.
``docs/AGENT-AUTONOMY-RESEARCH.md`` Tier 1 Move #2 calls this out as
"the single highest-leverage observability addition possible given
today's state."

``scripts/detect_metric_drift.sh`` is that prevention layer. It runs
nightly via ``genlab-metric-drift-detect.timer`` and fires WARN to
journalctl when the engagement_rate distribution drifts in one of
four ways — including the exact pattern (all-zero rates) of the
original bug.

These pins guard:
  1. Script exists + is executable (so systemd ExecStart works)
  2. All 4 documented alert triggers are referenced in the script
     source (so a future refactor can't silently drop one)
  3. Service unit ExecStart points to the script (no copy-paste
     typo from a sibling unit)
  4. Timer fires daily, Persistent=true, installs to timers.target
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_DEPLOY_DIR = _REPO / "deploy" / "systemd-phase2"
_SCRIPT = _REPO / "scripts" / "detect_metric_drift.sh"
_SERVICE = _DEPLOY_DIR / "genlab-metric-drift-detect.service"
_TIMER = _DEPLOY_DIR / "genlab-metric-drift-detect.timer"


class TestScript:
    def test_script_exists(self):
        """Pin: script must exist at the path the systemd unit
        ExecStarts. Missing-file would make the timer fail silently
        on every fire."""
        assert _SCRIPT.exists(), f"Script missing at {_SCRIPT}"

    def test_script_is_executable(self):
        """Pin: script must be chmod +x so systemd can ExecStart it
        directly (without invoking bash explicitly). Without exec
        bit, systemd logs ``Permission denied`` and exits 203."""
        assert os.access(_SCRIPT, os.X_OK), (
            f"Script {_SCRIPT} is not executable. Run: chmod +x scripts/detect_metric_drift.sh"
        )

    def test_script_references_all_four_alert_triggers(self):
        """Pin: all 4 documented anomaly triggers must be referenced
        by name in the script so a future refactor can't silently
        drop one. Each ``reason=`` tag is the operator-facing label
        in the WARN log line — these become grep-anchors for the
        Slack notifier."""
        content = _SCRIPT.read_text()
        required_triggers = [
            "publishing_collapsed",
            "all_zero_engagement",
            "mean_collapsed",
            "variance_vanished",
        ]
        for trigger in required_triggers:
            assert trigger in content, (
                f"Script missing alert trigger label '{trigger}'. "
                f"All 4 triggers documented in the script header "
                f"must be present in the WARN log lines."
            )

    def test_no_invalid_psql_csv_arg(self):
        """Pin the 2026-07-14 regression fix.

        The prior script used ``--csv=off`` which was accepted by older
        psql versions but errors under PostgreSQL 18 (prod's version):
        ``option '--csv' doesn't allow an argument``. psql errored
        before running the query; the ``2>/dev/null`` in the
        ``run_query_for_niche`` invocation swallowed the error, and
        the empty stdout triggered ``"no data (skipping)"`` for all 5
        niches. Analytics had 2184 composite rows but the drift
        detector was blind to them for weeks.

        Never re-add ``--csv=off`` (or ``--csv=<anything>``). The
        default is off — no flag needed.
        """
        # Match only non-comment lines (a leading # is a shell comment).
        # This lets the docstring/history comment reference the flag
        # without tripping the pin.
        offending_lines = [
            line for line in _SCRIPT.read_text().splitlines()
            if "--csv=" in line and not line.lstrip().startswith("#")
        ]
        assert not offending_lines, (
            f"Script has active `--csv=<value>` invocation (breaks under "
            f"PG18): {offending_lines}. The default is off — drop the flag."
        )


class TestServiceUnit:
    def test_service_file_exists(self):
        """Pin: service unit template must exist in the deploy dir
        so operators can install it via the documented one-liner."""
        assert _SERVICE.exists(), (
            f"Missing systemd service unit at {_SERVICE}. "
            "Restore from git history if accidentally deleted."
        )

    def test_service_execstart_points_to_script(self):
        """Pin: ExecStart must invoke the script at the exact path
        ``/opt/genlab/scripts/detect_metric_drift.sh``. Catches
        copy-paste errors that leave a sibling unit's path."""
        content = _SERVICE.read_text()
        assert "ExecStart=/opt/genlab/scripts/detect_metric_drift.sh" in content, (
            "Service unit ExecStart does not point to "
            "/opt/genlab/scripts/detect_metric_drift.sh — check for "
            "copy-paste from a sibling unit."
        )

    def test_service_runs_as_genlab_not_root(self):
        """Pin: must run as User=genlab to avoid permission drift —
        root-owned writes to .logs/ would break subsequent runs by
        the regular pipeline user (chronic /opt/genlab issue, see
        check_permissions_drift.sh)."""
        content = _SERVICE.read_text()
        assert re.search(r"^User=genlab$", content, re.MULTILINE), (
            "Service must run as User=genlab (not root). Root-owned "
            "writes to .logs/detect_metric_drift.log would create "
            "permission drift."
        )

    def test_service_loads_dotenv(self):
        """Pin: must load /opt/genlab/.env for DATABASE_URL. Without
        env, the script exits 0 with SKIP — silent miss of the
        nightly check."""
        content = _SERVICE.read_text()
        assert "EnvironmentFile=/opt/genlab/.env" in content, (
            "Service must load /opt/genlab/.env — script needs "
            "DATABASE_URL to query the analytics table."
        )


class TestTimerUnit:
    def test_timer_file_exists(self):
        """Pin: timer unit template must exist so the service fires
        on a schedule (vs only when manually triggered)."""
        assert _TIMER.exists(), (
            f"Missing systemd timer unit at {_TIMER}. Without it, the service won't fire nightly."
        )

    def test_timer_fires_daily(self):
        """Pin: OnCalendar must be a daily wildcard (``*-*-* HH:MM:SS``).
        A weekly or monthly schedule would let multi-day drift slip
        through before the next fire."""
        content = _TIMER.read_text()
        assert re.search(r"OnCalendar=\*-\*-\* \d{2}:\d{2}:\d{2}", content), (
            "Timer must fire daily via OnCalendar=*-*-* HH:MM:SS. "
            "Anything weekly/monthly lets drift accumulate."
        )

    def test_timer_has_persistent_catchup(self):
        """Pin: Persistent=true so a missed nightly fire after VPS
        reboot gets caught up. Without it, a Sunday-night reboot
        silently drops Monday's drift check."""
        content = _TIMER.read_text()
        assert "Persistent=true" in content, (
            "Timer must set Persistent=true — without it, a reboot "
            "at the wrong time silently drops one night's drift check."
        )

    def test_timer_installs_to_timers_target(self):
        """Pin: [Install] WantedBy=timers.target so
        ``systemctl enable`` actually wires the timer into the boot
        sequence. Wrong target = timer enabled but never fires."""
        content = _TIMER.read_text()
        assert "WantedBy=timers.target" in content, (
            "Timer must install to timers.target — other targets "
            "leave the timer dangling after enable."
        )
