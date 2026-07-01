"""Tests for :mod:`genlab_core.monitoring.alerting_config`.

The audit (M-1) found ``alerting.yaml`` was effectively dead — 11 keys,
0 readers. These tests pin the loader's behaviour so it stays alive.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from genlab_core.monitoring.alerting_config import (
    AlertingConfig,
    Thresholds,
    _reset_cache_for_tests,
    get_alerting_config,
)
from pydantic import ValidationError


@pytest.fixture(autouse=True)
def _isolate_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


class TestAlertingConfigLoader:
    def test_returns_pydantic_model_with_defaults_when_yaml_missing(
        self, tmp_path, monkeypatch
    ) -> None:
        """Missing YAML → field defaults (unit-test path is zero-config)."""
        monkeypatch.setattr(
            "genlab_core.monitoring.alerting_config._DEFAULT_YAML_PATH",
            tmp_path / "nope.yaml",
        )
        cfg = get_alerting_config()
        assert isinstance(cfg, AlertingConfig)
        # Default values match what was previously inlined.
        assert cfg.thresholds.disk_usage_pct == 80
        assert cfg.thresholds.swap_warning_mb == 500
        assert cfg.thresholds.swap_critical_pct == 0.9

    def test_loads_real_yaml_from_repo(self) -> None:
        """The committed alerting.yaml must parse cleanly + roundtrip
        the values we wired in health_monitor.py."""
        cfg = get_alerting_config()
        # These are the values committed in the YAML; if a future edit
        # changes them, this test catches the drift.
        assert cfg.thresholds.disk_usage_pct == 80
        assert cfg.thresholds.swap_warning_mb == 500
        assert cfg.thresholds.swap_critical_pct == 0.9
        assert cfg.thresholds.pipeline_duration_p95_seconds == 600

    def test_validates_field_constraints(self) -> None:
        """Pydantic rejects out-of-range values at load time — no more
        silent garbage in the YAML."""
        with pytest.raises(ValidationError):
            Thresholds(disk_usage_pct=150)  # > 100
        with pytest.raises(ValidationError):
            Thresholds(swap_critical_pct=2.0)  # > 1.0
        with pytest.raises(ValidationError):
            Thresholds(swap_warning_mb=0)  # not > 0

    def test_unknown_yaml_keys_ignored(self) -> None:
        """An unknown key in the YAML mustn't crash — Pydantic's default
        is to ignore extras (we want this for forward compat)."""
        cfg = AlertingConfig.model_validate({"thresholds": {"future_key": 42}})
        assert cfg.thresholds.disk_usage_pct == 80  # field default unchanged


class TestHealthMonitorWiring:
    """Confirm the new alerting_config values actually reach
    check_disk and check_swap (the two wired sites)."""

    def test_check_disk_reads_threshold_from_alerting_yaml(self, monkeypatch) -> None:
        """A tighter disk threshold (60%) must change the warn boundary
        without a code change.

        2026-07-01 rewrite: check_disk now uses shutil.disk_usage instead
        of subprocess df. Test updated to mock that.
        """
        from genlab_core.monitoring import health_monitor

        # / at 65%, /mnt/genlab-media at 10%
        def _fake_disk_usage(path):
            from shutil import _ntuple_diskusage

            if path == "/":
                return _ntuple_diskusage(total=100, used=65, free=35)
            return _ntuple_diskusage(total=100, used=10, free=90)

        with (
            patch("shutil.disk_usage", side_effect=_fake_disk_usage),
            patch("genlab_core.monitoring.alerting_config.get_alerting_config") as mock_cfg,
        ):
            mock_cfg.return_value = AlertingConfig(thresholds=Thresholds(disk_usage_pct=60))
            alerts = health_monitor.check_disk()

        # 65% > 60% warn → warning alert for /
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"
        assert "/" in alerts[0].message

    def test_check_disk_missing_mount_does_not_hide_pressure_on_root(self, monkeypatch) -> None:
        """The 2026-07-01 crash regression pin.

        On the Hetzner VPS, /mnt/genlab-media doesn't exist. Previously
        check_disk ran `df / /mnt/genlab-media` in one subprocess; df
        errored on the missing mount, exception logged at DEBUG, and
        check_disk silently returned zero alerts even when / was full.

        Prod PG crashed at 100% disk with no advance warning as a result.
        This test locks in the fix: each mount queried independently, so
        a missing /mnt/genlab-media never hides a full /.
        """
        from genlab_core.monitoring import health_monitor

        def _fake_disk_usage(path):
            if path == "/mnt/genlab-media":
                raise FileNotFoundError(path)
            from shutil import _ntuple_diskusage

            # / at 95% — should ABSOLUTELY fire critical
            return _ntuple_diskusage(total=100, used=95, free=5)

        with (
            patch("shutil.disk_usage", side_effect=_fake_disk_usage),
            patch("genlab_core.monitoring.alerting_config.get_alerting_config") as mock_cfg,
        ):
            mock_cfg.return_value = AlertingConfig(thresholds=Thresholds(disk_usage_pct=80))
            alerts = health_monitor.check_disk()

        assert len(alerts) == 1, "critical / pressure MUST fire even when /mnt/genlab-media missing"
        assert alerts[0].severity == "critical"
        assert alerts[0].check == "disk_pressure"
        assert "/" in alerts[0].message
        # Auto-fix pointer for operator convenience — added 2026-07-01
        assert "disk_cleanup.sh" in alerts[0].auto_fix

    def test_check_disk_no_alert_when_all_mounts_healthy(self, monkeypatch) -> None:
        """Complements the pressure tests: verify no false-positive when
        everything is genuinely fine."""
        from genlab_core.monitoring import health_monitor

        def _fake_disk_usage(path):
            from shutil import _ntuple_diskusage

            return _ntuple_diskusage(total=100, used=30, free=70)

        with (
            patch("shutil.disk_usage", side_effect=_fake_disk_usage),
            patch("genlab_core.monitoring.alerting_config.get_alerting_config") as mock_cfg,
        ):
            mock_cfg.return_value = AlertingConfig(thresholds=Thresholds(disk_usage_pct=80))
            alerts = health_monitor.check_disk()
        assert alerts == []

    def test_check_swap_reads_warning_mb_from_alerting_yaml(self, monkeypatch) -> None:
        from genlab_core.monitoring import health_monitor

        # Simulate `free -b` with total=1GB, used=600MB (above the 500 default).
        free_output = (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:    4136562688  3000000000   136562688           0  1000000000   1136562688\n"
            "Swap:   1073741824   629145600   444596224\n"
        )

        class _R:
            stdout = free_output

        with (
            patch.object(health_monitor.subprocess, "run", return_value=_R()),
            patch("genlab_core.monitoring.alerting_config.get_alerting_config") as mock_cfg,
        ):
            # warning at >500 MB, critical at >0.9 of total (>966 MB)
            mock_cfg.return_value = AlertingConfig(
                thresholds=Thresholds(swap_warning_mb=500, swap_critical_pct=0.9)
            )
            alerts = health_monitor.check_swap()
        # 600 MB > 500 MB warning, not > 966 MB critical → warning
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"
        assert "Swap" in alerts[0].message
