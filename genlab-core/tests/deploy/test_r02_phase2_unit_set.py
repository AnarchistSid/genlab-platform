"""R-02: pin the set of services the phase-2 deploy bundle MUST carry.

Before R-02 the following units lived only in ``deploy/systemd/``
(phase 1). A clean phase-2 re-bootstrap would have:

  * lost ``genlab-dashboard.service`` → no review UI, blueprints
    pile up unapproved.
  * lost ``genlab-token-refresh.{service,timer}`` → publishing
    silently dies at the next token expiry (60-day Threads, 60-day
    YT, ~24h X bearer).
  * lost ``genlab-metric-collector.{service,timer}`` → feedback
    loop dark, bandit arms never update.
  * lost ``genlab-db-maintenance.{service,timer}`` → indexes don't
    get re-VACUUMed, query latency drifts up.

This test is a regression pin — future drift between the phase-2
bundle and the "essential daemons" set fails CI, not the next
re-bootstrap at 3am.

R-05 (PR #131) carries the engagement-poller separately; pinned in
its own test file (test_systemd_phase2_inventory.py).
"""

from __future__ import annotations

from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_PHASE2_DIR = _GENLAB_ROOT / "deploy" / "systemd-phase2"

_R02_REQUIRED_UNITS = (
    "genlab-dashboard.service",
    "genlab-db-maintenance.service",
    "genlab-db-maintenance.timer",
    "genlab-metric-collector.service",
    "genlab-metric-collector.timer",
    "genlab-token-refresh.service",
    "genlab-token-refresh.timer",
)


def test_r02_phase2_carries_dashboard_and_maintenance_units() -> None:
    """R-02: phase-2 deploy bundle must include the 7 "essential
    daemon" files that used to live only in phase 1."""
    missing = [name for name in _R02_REQUIRED_UNITS if not (_PHASE2_DIR / name).is_file()]
    assert not missing, (
        "R-02 regression: phase-2 deploy bundle is missing required "
        f"units: {missing}. Add the matching .service/.timer files from "
        "``deploy/systemd/`` to ``deploy/systemd-phase2/``."
    )


def test_r02_dashboard_unit_runs_gunicorn() -> None:
    """The dashboard's ``Restart=always`` daemon model is non-negotiable
    — a oneshot timer'd dashboard means the operator can't approve
    blueprints between fires. Pin that the unit launches gunicorn
    (not a oneshot wrapper)."""
    unit = (_PHASE2_DIR / "genlab-dashboard.service").read_text()
    assert "gunicorn" in unit, "Dashboard unit must launch gunicorn (long-running daemon)."
    assert "Restart=always" in unit, "Dashboard unit must auto-restart on crash."


def test_r02_token_refresh_unit_pins_timer_schedule() -> None:
    """Token refresh is the most failure-sensitive timer in the stack —
    a missed window silently breaks the next publish. Pin that the
    timer file exists alongside its service so both ship together."""
    assert (_PHASE2_DIR / "genlab-token-refresh.service").is_file()
    assert (_PHASE2_DIR / "genlab-token-refresh.timer").is_file()
