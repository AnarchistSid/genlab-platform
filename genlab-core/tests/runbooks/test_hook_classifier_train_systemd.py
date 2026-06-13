"""Pins for the hook classifier training systemd unit + timer.

Closes the half-wired gap from the 2026-06-13 autonomy deep-dive: the
XGBoost hook quality predictor existed since Sprint 63 but its only
caller was a manual CLI. This service + timer pair runs it daily on
the Hetzner prod box.

The headline pin: **the .service file's ExecStart must invoke a real
Python module that actually exists**. If a future PR renames or
deletes ``genlab_core.scripts.train_hook_classifier``, this test fails
loudly instead of the cron silently doing nothing in prod.

Plus the schedule + safety invariants — operator-facing details that
shouldn't drift without intent.

Why systemd (not launchd):
    The prod box is Hetzner Linux. The local Mac launchd plists at
    `genlab-core/runbooks/*.plist` are gitignored operator-local
    config; the source of truth for prod schedulers is
    `deploy/systemd-phase2/*.service` + `*.timer` pairs.
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_SYSTEMD_DIR = _REPO_ROOT / "deploy" / "systemd-phase2"
_SERVICE_PATH = _SYSTEMD_DIR / "genlab-hook-classifier-train.service"
_TIMER_PATH = _SYSTEMD_DIR / "genlab-hook-classifier-train.timer"


def _parse_unit(path: Path) -> configparser.ConfigParser:
    """systemd unit files are INI-shaped; configparser handles them
    with strict=False to permit the duplicate-key permissiveness
    systemd allows (e.g., multiple Environment= lines)."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read(path)
    return parser


@pytest.fixture(scope="module")
def service_data() -> configparser.ConfigParser:
    return _parse_unit(_SERVICE_PATH)


@pytest.fixture(scope="module")
def timer_data() -> configparser.ConfigParser:
    return _parse_unit(_TIMER_PATH)


# ── Service file invariants ───────────────────────────────────────────────


def test_service_file_exists():
    assert _SERVICE_PATH.is_file(), f"Service unit missing at {_SERVICE_PATH}"


def test_service_parses_as_valid_ini(service_data):
    """A typo in the unit syntax would make systemd refuse to load it."""
    assert service_data.has_section("Unit")
    assert service_data.has_section("Service")
    assert "Description" in service_data["Unit"]


def test_service_execstart_invokes_train_hook_classifier(service_data):
    """THE headline pin: ExecStart must invoke the actual Python module.

    A drift here (rename, refactor, deletion of the script) would
    cause the timer to run nothing in prod with no visibility
    beyond a noisy journal log. The companion
    test_target_module_is_importable below verifies the module ALSO
    actually exists on disk.
    """
    exec_start = service_data["Service"]["ExecStart"]
    assert "-m genlab_core.scripts.train_hook_classifier" in exec_start, (
        f"ExecStart must invoke genlab_core.scripts.train_hook_classifier, got: {exec_start}"
    )
    # --niche-id all → trains every niche in one pass
    assert "--niche-id all" in exec_start, (
        f"Must pass --niche-id all to retrain across all 5 niches, got: {exec_start}"
    )


def test_target_module_is_importable():
    """The unit's invoked module must actually exist + be importable.

    Defense-in-depth on top of the ExecStart string match: that test
    only checks the string in the unit; this one verifies the import
    resolves. If a future PR deletes the module, both tests fail.
    """
    import importlib

    module = importlib.import_module("genlab_core.scripts.train_hook_classifier")
    # The module exposes a `main()` entry point (CLI contract)
    assert hasattr(module, "main"), "train_hook_classifier must expose main()"
    assert callable(module.main)


def test_service_runs_as_genlab_user(service_data):
    """Match the established pattern across the other systemd units —
    never run as root, never run as the deployer's user."""
    assert service_data["Service"]["User"] == "genlab"
    assert service_data["Service"]["Group"] == "genlab"


def test_service_loads_env_file(service_data):
    """Must source /opt/genlab/.env so DATABASE_URL + per-niche
    credentials are present at training time. Without this, the
    BacklogClient construction would fall back to defaults that
    don't reach the right Postgres on prod."""
    assert service_data["Service"]["EnvironmentFile"] == "/opt/genlab/.env"


def test_service_has_generous_timeout(service_data):
    """XGBoost convergence on the per-niche labelled dataset is
    bounded but can stretch. 1800s caps the worst case."""
    timeout_sec = int(service_data["Service"]["TimeoutSec"])
    assert timeout_sec >= 600, (
        f"TimeoutSec must be ≥ 600s to allow XGBoost convergence, got {timeout_sec}"
    )


def test_service_type_is_oneshot(service_data):
    """Training is a finite job, not a long-running daemon. Type=oneshot
    is the systemd idiom for cron-like jobs and the convention used by
    every other GenLab timer-driven service in this directory."""
    assert service_data["Service"]["Type"] == "oneshot"


# ── Timer file invariants ─────────────────────────────────────────────────


def test_timer_file_exists():
    assert _TIMER_PATH.is_file(), f"Timer unit missing at {_TIMER_PATH}"


def test_timer_runs_daily_at_off_peak(timer_data):
    """Hourly would be wasteful (training data grows ~5-15 per niche
    per day). Weekly would delay crossing MIN_EXAMPLES by up to 7 days.
    Daily at 03:30 UTC sits in the sweet spot — before the publisher
    cron, off-peak compute on the Hetzner box."""
    on_calendar = timer_data["Timer"]["OnCalendar"]
    # Daily at 03:30 UTC — pin both the cadence (*-*-*) and the time
    assert on_calendar == "*-*-* 03:30:00", (
        f"Timer must fire daily at 03:30 UTC, got: {on_calendar}"
    )


def test_timer_persistent_catches_missed_runs(timer_data):
    """If the box was down at 03:30 (deploy, reboot), Persistent=true
    causes systemd to run the missed job once the timer comes back
    up. Without this, a missed day's training is silently lost."""
    assert timer_data["Timer"]["Persistent"].lower() == "true"


def test_timer_installs_to_timers_target(timer_data):
    """All GenLab timers in this directory install to timers.target —
    matches the convention so `systemctl list-timers` shows the
    hook classifier alongside the others."""
    assert timer_data["Install"]["WantedBy"] == "timers.target"
