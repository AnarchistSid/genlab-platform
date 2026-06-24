"""Pins for ``deploy/systemd/genlab-archive-stale-drafted.{service,timer}``.

These two unit files wire ``scripts/archive_stale_drafted.py`` (shipped
in PR #522) into a nightly systemd timer so the operator no longer
needs to invoke the script manually for the recurring stuck-DRAFTED
cleanup.

The pins below mirror the conventions established in
``test_dpo_export.py::TestSystemdUnits`` — same shape, same intent.
Each pin defends a specific operational invariant:

* the ``--apply`` flag must be present (without it, the timer is a
  no-op cron — burns CPU + writes log noise but mutates nothing);
* ``--age-days`` must be conservative (≥7 days) to prevent
  premature archive of in-flight blueprints;
* ``EnvironmentFile`` must load ``.env`` so ``DATABASE_URL`` is set;
* ``OnCalendar`` must fire BEFORE the 06:30 UTC publisher window so
  a stale row archived this morning doesn't surface in today's
  publisher pass;
* ``Persistent=true`` so a missed window (system off / network down)
  fires once on next boot instead of skipping forever.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SYSTEMD_DIR = Path(__file__).resolve().parents[1] / "deploy" / "systemd"
SERVICE_PATH = SYSTEMD_DIR / "genlab-archive-stale-drafted.service"
TIMER_PATH = SYSTEMD_DIR / "genlab-archive-stale-drafted.timer"


# ── unit files exist ───────────────────────────────────────────────


def test_service_unit_exists():
    assert SERVICE_PATH.exists(), (
        f"systemd service unit missing at {SERVICE_PATH}. "
        f"PR #524 ships both .service and .timer; deleting one leaves "
        f"the other half-wired."
    )


def test_timer_unit_exists():
    assert TIMER_PATH.exists(), (
        f"systemd timer unit missing at {TIMER_PATH}. "
        f"Without it the service never fires on its own."
    )


# ── service: invokes the right script with the right flags ─────────


@pytest.fixture(scope="module")
def service_source() -> str:
    return SERVICE_PATH.read_text()


def test_service_invokes_archive_stale_drafted_script(service_source):
    """``ExecStart`` must call the canonical script path. A typo or
    rename would leave the timer firing into a void with no error."""
    assert "scripts/archive_stale_drafted.py" in service_source, (
        "ExecStart must reference scripts/archive_stale_drafted.py — shipped in PR #522."
    )


def test_service_uses_apply_flag(service_source):
    """``--apply`` is required for the script to mutate. Without it
    the cron fires every night, prints the dry-run output, and never
    actually archives anything — the operational equivalent of a
    no-op timer that looks healthy in `systemctl status`."""
    assert "--apply" in service_source, (
        "ExecStart must pass --apply — without it the timer never "
        "actually archives. This is the entire point of wiring the cron."
    )


def test_service_uses_conservative_age_days(service_source):
    """``--age-days`` must be present + ≥7. Aggressive defaults (1-2
    days) risk archiving in-flight blueprints whose source-video
    fetch hasn't yet run for the day. Match the script's own
    default of 7."""
    m = re.search(r"--age-days\s+(\d+)", service_source)
    assert m is not None, "ExecStart must specify --age-days N explicitly"
    age_days = int(m.group(1))
    assert age_days >= 7, (
        f"--age-days must be ≥7 (conservative grace); got {age_days}. "
        f"Tighter values risk archiving in-flight blueprints."
    )


def test_service_loads_env_file(service_source):
    """``DATABASE_URL`` lives in /opt/genlab/.env — must be loaded
    via ``EnvironmentFile``. Without it the script exits 1 with
    'DATABASE_URL not set' (verified empirically during PR #522
    dry-run on prod when we forgot the env source)."""
    assert "EnvironmentFile=/opt/genlab/.env" in service_source, (
        "Service must load /opt/genlab/.env to get DATABASE_URL — without it the script exits 1."
    )


def test_service_runs_as_genlab_user(service_source):
    """Never run cron as root. The script writes a CSV backup to
    ``/opt/genlab/.tmp/backups/`` — running as root makes those files
    root-owned and the dashboard service can't read them later."""
    assert "User=genlab" in service_source
    assert "Group=genlab" in service_source


def test_service_is_oneshot(service_source):
    """``Type=oneshot`` matches the other GenLab cron services
    (metric_collector, db_maintenance). A long-running ``simple``
    type would make systemd treat it as a daemon — the timer would
    refuse to re-fire while the previous run is "still active"."""
    assert "Type=oneshot" in service_source


# ── timer: fires daily at a sensible time ──────────────────────────


@pytest.fixture(scope="module")
def timer_source() -> str:
    return TIMER_PATH.read_text()


def test_timer_fires_before_publisher_window(timer_source):
    """Publisher runs at 06:30 UTC. The cleanup must fire BEFORE that
    so today's archived row doesn't accidentally appear in today's
    publish queue (it wouldn't — DRAFTED never gets published — but
    a fire-after pattern means the row sits for an extra 24h)."""
    m = re.search(r"OnCalendar=\*-\*-\* (\d{2}):(\d{2}):", timer_source)
    assert m is not None, "Timer must use OnCalendar=*-*-* HH:MM:SS format (daily fire)."
    hour = int(m.group(1))
    minute = int(m.group(2))
    # Publisher at 06:30 UTC; archive cleanup must finish before then.
    fire_minutes = hour * 60 + minute
    publisher_minutes = 6 * 60 + 30
    assert fire_minutes < publisher_minutes, (
        f"Timer fires at {hour:02d}:{minute:02d} UTC; must be BEFORE 06:30 UTC publisher window."
    )


def test_timer_uses_persistent_flag(timer_source):
    """``Persistent=true`` re-fires on next boot if a window was missed
    (system off, network down, etc.). Without it, a missed daily run
    is silently skipped and the stuck rows accumulate for another 24h."""
    assert "Persistent=true" in timer_source, (
        "Timer must set Persistent=true so missed windows fire on next boot."
    )


def test_timer_installed_to_timers_target(timer_source):
    """Required for ``systemctl enable`` to actually wire the timer
    into the boot graph. Missing this and the timer file is dormant
    even after `systemctl daemon-reload`."""
    assert "WantedBy=timers.target" in timer_source


# ── cross-unit pins ────────────────────────────────────────────────


def test_service_and_timer_share_base_name(service_source, timer_source):
    """systemd convention: ``foo.timer`` triggers ``foo.service`` by
    matching the base name. Renaming one without the other silently
    breaks the wiring."""
    # Both files exist at the matching base name — Python's path layer
    # already enforces this since the fixtures load by literal path.
    # Pin the convention explicitly so a future split (e.g.
    # foo.timer → bar.service) trips the test.
    assert SERVICE_PATH.stem == TIMER_PATH.stem
    assert SERVICE_PATH.stem == "genlab-archive-stale-drafted"


def test_documentation_link_present(service_source, timer_source):
    """Both units should link back to the script as the source-of-truth
    documentation. Operators reading `systemctl cat` get a pointer
    to the actual behavior."""
    assert "scripts/archive_stale_drafted.py" in service_source
    assert "Documentation=" in service_source
    assert "Documentation=" in timer_source
