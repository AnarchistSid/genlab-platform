"""Pin: systemd refit timers for the 2 stateful learning engines exist.

The conformal router (PR #605) and Bayesian gate (PR #608) both ship
with `refit_from_calibration()` / `refit_all_niches()` entry points,
but the inference path returns safe defaults forever unless those
refits actually run. The systemd timers in this PR fill that gap.

These pins catch the "shipped-but-not-activated" pattern at the
repo level — if anyone removes the unit files in a future cleanup,
CI breaks before the regression hits prod.
"""

from __future__ import annotations

import configparser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UNITS_DIR = REPO_ROOT / "deploy" / "systemd-phase2"

REQUIRED_PAIRS = [
    "genlab-conformal-router-refit",
    "genlab-bayesian-gate-refit",
]


def _read_unit(path: Path) -> configparser.ConfigParser:
    """systemd unit files are ini-shaped; configparser handles them."""
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.read(path)
    return cfg


class TestRefitUnitsExist:
    def test_all_required_unit_files_present(self):
        for base in REQUIRED_PAIRS:
            svc = UNITS_DIR / f"{base}.service"
            tim = UNITS_DIR / f"{base}.timer"
            assert svc.is_file(), f"missing systemd service unit: {svc}"
            assert tim.is_file(), f"missing systemd timer unit: {tim}"


class TestServiceUnitShape:
    def test_services_run_as_genlab_user(self):
        """Refit must NOT run as root (writes only to genlab-owned
        paths; security principle of least privilege)."""
        for base in REQUIRED_PAIRS:
            cfg = _read_unit(UNITS_DIR / f"{base}.service")
            assert cfg["Service"]["User"] == "genlab", (
                f"{base}.service must run as genlab user, "
                f"got {cfg['Service'].get('User', '(unset)')}"
            )

    def test_services_are_oneshot(self):
        """Refit is a single run, not a long-lived daemon."""
        for base in REQUIRED_PAIRS:
            cfg = _read_unit(UNITS_DIR / f"{base}.service")
            assert cfg["Service"]["Type"] == "oneshot"

    def test_services_load_env_file(self):
        """EnvironmentFile=/opt/genlab/.env must be loaded (refit
        needs DATABASE_URL + GENLAB_* feature flags)."""
        for base in REQUIRED_PAIRS:
            cfg = _read_unit(UNITS_DIR / f"{base}.service")
            assert cfg["Service"]["EnvironmentFile"] == "/opt/genlab/.env"

    def test_services_have_finite_timeout(self):
        """Refit must not hang forever on a stuck DB query."""
        for base in REQUIRED_PAIRS:
            cfg = _read_unit(UNITS_DIR / f"{base}.service")
            timeout = int(cfg["Service"]["TimeoutSec"])
            assert 60 <= timeout <= 600, f"{base} TimeoutSec={timeout} outside [60, 600] window"

    def test_services_call_correct_refit_entrypoint(self):
        """ExecStart must invoke the refit function from the right
        module. Pins protect against accidental rename of refit
        entry points."""
        conf_svc = _read_unit(UNITS_DIR / "genlab-conformal-router-refit.service")
        assert "conformal_router" in conf_svc["Service"]["ExecStart"]
        assert "refit_from_calibration" in conf_svc["Service"]["ExecStart"]

        bayes_svc = _read_unit(UNITS_DIR / "genlab-bayesian-gate-refit.service")
        assert "bayesian_gate" in bayes_svc["Service"]["ExecStart"]
        assert "refit_all_niches" in bayes_svc["Service"]["ExecStart"]


class TestTimerUnitShape:
    def test_timers_fire_daily(self):
        """Both timers should fire at most once per day (per-niche
        posteriors don't need sub-daily refresh)."""
        for base in REQUIRED_PAIRS:
            cfg = _read_unit(UNITS_DIR / f"{base}.timer")
            on_cal = cfg["Timer"]["OnCalendar"]
            # `*-*-* HH:MM:SS UTC` shape — daily
            assert "*-*-*" in on_cal, (
                f"{base}.timer should fire daily (OnCalendar=*-*-* ...), got {on_cal!r}"
            )
            assert "UTC" in on_cal, f"{base}.timer should specify UTC, got {on_cal!r}"

    def test_timers_use_distinct_slots(self):
        """Refit timers must not collide on the same minute (DB
        pressure on the 4 GB VPS). Spaced 15 min apart per design."""
        conf_t = _read_unit(UNITS_DIR / "genlab-conformal-router-refit.timer")
        bayes_t = _read_unit(UNITS_DIR / "genlab-bayesian-gate-refit.timer")
        assert conf_t["Timer"]["OnCalendar"] != bayes_t["Timer"]["OnCalendar"]

    def test_timers_persistent_true(self):
        """Catch-up after reboot — a missed daily refit shouldn't be
        skipped silently."""
        for base in REQUIRED_PAIRS:
            cfg = _read_unit(UNITS_DIR / f"{base}.timer")
            assert cfg["Timer"]["Persistent"].lower() == "true"

    def test_timers_install_into_timers_target(self):
        """Required for `systemctl enable` to wire into timers.target."""
        for base in REQUIRED_PAIRS:
            cfg = _read_unit(UNITS_DIR / f"{base}.timer")
            assert cfg["Install"]["WantedBy"] == "timers.target"
