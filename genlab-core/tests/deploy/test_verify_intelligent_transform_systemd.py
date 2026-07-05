"""Pins for the ``genlab-verify-intelligent-transform`` systemd units
(sprint activation follow-up, 2026-07-05).

The verify script exits **3** on activation regression — reels
published with empty ``arm_ids_by_dimension``. The alert path is
``OnFailure=genlab-service-failure-alert@%n.service``, which fires
when systemd sees a non-zero exit.

If someone drops ``SuccessExitStatus=3`` into the unit thinking they're
"cleaning up spurious alerts", they silently disable the regression
detector — the whole reason the timer exists. Pin it shut.
"""

from __future__ import annotations

import configparser
from pathlib import Path

# Walk up from ``genlab-core/tests/deploy/test_*.py`` to workspace root.
_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_PHASE2 = _GENLAB_ROOT / "deploy" / "systemd-phase2"
_SERVICE = _PHASE2 / "genlab-verify-intelligent-transform.service"
_TIMER = _PHASE2 / "genlab-verify-intelligent-transform.timer"


def _parse(unit_path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read(unit_path)
    return parser


# ── existence ───────────────────────────────────────────────────────


def test_service_present_in_phase2():
    assert _SERVICE.is_file(), (
        f"{_SERVICE} missing — the verify script needs a systemd wrapper "
        "so activation regressions surface on Mission Control, not in an "
        "operator's memory."
    )


def test_timer_present_in_phase2():
    assert _TIMER.is_file()


# ── service invariants ──────────────────────────────────────────────


def test_service_invokes_verify_script_by_file_path():
    """``-m scripts.X`` would ModuleNotFoundError because ``scripts/`` is
    not a Python package. Sibling gotcha:
    ``[[systemd-scripts-invocation-gotcha]]``.
    """
    exec_start = _parse(_SERVICE)["Service"]["ExecStart"]
    assert "scripts/verify_intelligent_transform.py" in exec_start
    assert "-m scripts." not in exec_start


def test_service_does_NOT_whitelist_exit_3():
    """Regression pin — see module docstring.

    ``SuccessExitStatus=3`` would silence the whole alert path.
    """
    content = _SERVICE.read_text()
    assert "SuccessExitStatus=3" not in content, (
        "Exit code 3 = activation regression alert. Whitelisting it "
        "with SuccessExitStatus=3 silences the OnFailure hook and "
        "defeats the timer's whole purpose."
    )


def test_service_has_on_failure_alert_hook():
    """The whole point of the timer is to route exit-3 into a dashboard
    alert. Without the OnFailure hook, an activation regression fires
    silently.
    """
    content = _SERVICE.read_text()
    assert "OnFailure=genlab-service-failure-alert@" in content


def test_service_sources_env_file():
    """DATABASE_URL lives in ``/opt/genlab/.env`` — script won't connect
    without it.
    """
    svc = _parse(_SERVICE)["Service"]
    assert svc.get("EnvironmentFile") == "/opt/genlab/.env"


# ── timer invariants ────────────────────────────────────────────────


def test_timer_has_persistent_true():
    """A skipped run (reboot / downtime) shouldn't drop the check."""
    timer = _parse(_TIMER)["Timer"]
    assert timer.getboolean("Persistent") is True


def test_timer_has_on_calendar():
    timer = _parse(_TIMER)["Timer"]
    assert "OnCalendar" in timer, "Timer must have OnCalendar= scheduling"


def test_timer_binds_to_the_service():
    timer = _parse(_TIMER)["Timer"]
    assert timer["Unit"] == "genlab-verify-intelligent-transform.service"
