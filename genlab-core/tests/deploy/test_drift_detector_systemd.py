"""Pins for the ``genlab-drift-detector`` systemd units (L7, 2026-07-08).

The drift detector fires daily and writes signals via
``genlab_core.learning.drift_persist.record_signals`` (guarded by
``GENLAB_DRIFT_PERSIST_ENABLED``). Failure to fire is invisible in
the DriftSignalCard because the card shows "posteriors stable" when
no signals exist — indistinguishable from "runner didn't fire".

This suite pins the invariants that keep the runner honest:

  * ``OnFailure=genlab-service-failure-alert@%n.service`` present
    — otherwise a runner crash is silent
  * ``SuccessExitStatus`` MUST NOT include 1 or the standard
    failure codes — the class-of-bug the audit-shipping arc
    surfaced multiple times (a well-meaning "clean up spurious
    alerts" edit that silences real failures)
  * ``Persistent=true`` on the timer so a missed fire catches up
    on next boot instead of dropping the drift signal for that day
  * OnCalendar time is AFTER the snapshot writer's 21:30 UTC —
    otherwise the runner reads a stale snapshot dir
"""

from __future__ import annotations

import configparser
from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_PHASE2 = _GENLAB_ROOT / "deploy" / "systemd-phase2"
_SERVICE = _PHASE2 / "genlab-drift-detector.service"
_TIMER = _PHASE2 / "genlab-drift-detector.timer"


def _parse(unit_path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read(unit_path)
    return parser


class TestUnitsExist:
    def test_service_present(self):
        assert _SERVICE.is_file(), (
            f"{_SERVICE} missing — the drift detector needs a systemd "
            "wrapper so regressions surface on Mission Control's "
            "DriftSignalCard, not from manual CLI invocations."
        )

    def test_timer_present(self):
        assert _TIMER.is_file(), (
            f"{_TIMER} missing — without a timer the service never fires."
        )


class TestServiceContract:
    def test_service_type_is_oneshot(self):
        parser = _parse(_SERVICE)
        assert parser["Service"]["Type"] == "oneshot"

    def test_service_invokes_drift_detector_module(self):
        """The ExecStart MUST call the module directly. If someone
        wraps it in a shell script, the class-of-bug is systemd
        losing the actual exit code — the wrapper's exit code
        propagates instead. Pin the direct invocation."""
        parser = _parse(_SERVICE)
        exec_start = parser["Service"]["ExecStart"]
        assert "genlab_core.learning.drift_detector" in exec_start, (
            f"ExecStart doesn't invoke drift_detector: {exec_start!r}"
        )
        assert (
            ".venv/bin/python" in exec_start or "uv run" in exec_start
        ), "ExecStart doesn't use the pinned venv/uv python"

    def test_on_failure_alert_wired(self):
        """OnFailure MUST point at the shared alert template.
        Without it, a runner crash is silent — the DriftSignalCard
        shows the same "posteriors stable" copy whether the runner
        succeeded with zero signals OR crashed and wrote nothing."""
        parser = _parse(_SERVICE)
        on_failure = parser["Unit"].get("OnFailure", "")
        assert (
            "genlab-service-failure-alert" in on_failure
        ), (
            "OnFailure= not wired to the shared alert template. "
            "Add: OnFailure=genlab-service-failure-alert@%n.service"
        )

    def test_no_success_exit_status_allowlists_failures(self):
        """The audit-shipping arc's recurring footgun: someone adds
        ``SuccessExitStatus=1 2 3`` to silence "spurious alerts" and
        accidentally allowlists REAL failures. Pin that we don't have
        this trap.

        For drift detector, exit 0 is the ONLY success (there's no
        "amber = OK" tier like the reward-shaper validator). Any
        SuccessExitStatus= entry is a red flag — locking the absence
        of the field so a future addition surfaces intentionally."""
        parser = _parse(_SERVICE)
        ses = parser["Service"].get("SuccessExitStatus", "").strip()
        assert ses == "", (
            f"SuccessExitStatus={ses!r} present on drift-detector "
            "service — exit 0 is the only success for this runner. "
            "A non-empty value silences real failures. If you have a "
            "legitimate reason, add a comment in the .service file "
            "AND update this test to match."
        )

    def test_snapshot_dir_env_var_set(self):
        """The runner reads BANDIT_SNAPSHOT_DIR to know where the
        daily snapshots live. Without it, drift_detector falls back
        to a hardcoded prod path — fine on prod but confusing when
        a future editor tries to point at a different dir. Locking
        that the env var is set explicitly."""
        parser = _parse(_SERVICE)
        # ConfigParser doesn't natively multi-value Environment=,
        # so read the raw file for the flag.
        raw = _SERVICE.read_text()
        assert "Environment=BANDIT_SNAPSHOT_DIR=" in raw, (
            "Environment=BANDIT_SNAPSHOT_DIR=... not set — the "
            "snapshot dir is a real config surface, not a hardcoded "
            "path."
        )


class TestTimerContract:
    def test_timer_persistent_true(self):
        """A missed fire should catch up on next boot. Otherwise
        a maintenance window silently drops a day of drift data."""
        parser = _parse(_TIMER)
        persistent = parser["Timer"].get("Persistent", "").strip().lower()
        assert persistent == "true", (
            f"Persistent={persistent!r} — must be 'true' so missed "
            "fires catch up. Drift signals for a dropped day are "
            "unrecoverable."
        )

    def test_oncalendar_is_daily(self):
        """Daily cadence is right for this signal. Locking so a
        future weekly-cadence edit surfaces intentionally."""
        parser = _parse(_TIMER)
        on_cal = parser["Timer"].get("OnCalendar", "").strip()
        # Daily-glob format is `*-*-* HH:MM:SS UTC`. If someone
        # switches to weekly (`Mon *-*-*`) we want to know.
        assert on_cal.startswith("*-*-*"), (
            f"OnCalendar={on_cal!r} — not daily. Drift detection "
            "on a weekly cadence would miss regressions until they "
            "ossify."
        )
        assert "UTC" in on_cal, (
            f"OnCalendar={on_cal!r} — must be UTC-explicit to avoid "
            "surprise when the box's TZ changes."
        )

    def test_oncalendar_fires_after_snapshot_writer(self):
        """The bandit_arms snapshot writer fires 21:30 UTC. The
        drift detector MUST fire AFTER that (next day's morning UTC)
        so the fresh snapshot is included in the recent window.
        Firing before the snapshot means using stale data.

        Locking that the hour is between 00:00 and 21:00 UTC
        (exclusive of the snapshot-writer's slot)."""
        parser = _parse(_TIMER)
        on_cal = parser["Timer"].get("OnCalendar", "").strip()
        # Parse `*-*-* HH:MM:SS UTC`
        parts = on_cal.split()
        assert len(parts) >= 2, (
            f"OnCalendar={on_cal!r} — unexpected format"
        )
        time_part = parts[1]  # HH:MM:SS
        hour = int(time_part.split(":")[0])
        # Snapshot writer fires 21:30 UTC. Drift detector should fire
        # BEFORE 21:00 the same day (early morning), not after. The
        # sweet spot is 04:00-06:00 UTC = 09:30-11:30 IST when
        # operators start their morning check.
        assert 0 <= hour < 21, (
            f"OnCalendar hour {hour} conflicts with the snapshot "
            "writer's 21:30 UTC slot. Fire between 00:00 and 21:00 "
            "so the fresh snapshot is included."
        )

    def test_timer_links_to_service(self):
        """Unit= MUST name the drift-detector service. A stale link
        (e.g. after a rename refactor) would fire the wrong service."""
        parser = _parse(_TIMER)
        unit = parser["Timer"].get("Unit", "").strip()
        assert unit == "genlab-drift-detector.service", (
            f"Timer's Unit={unit!r} doesn't point at "
            "genlab-drift-detector.service"
        )
