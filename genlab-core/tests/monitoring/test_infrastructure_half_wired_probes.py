"""PR #516 (2026-06-24) — health probes for engagement + content_pool.

Added after the 2026-06-24 infrastructure-half-wired audit
(``docs/architecture/infrastructure-half-wired.md``) found both
subsystems failing Q4 ("loud probe?") — engagement was silent for
22 days starting 2026-05-21 because no probe caught the missing
AGENT_ROOT env var (PR #513 fixed the env var; this PR adds the probe
so the next missing-wire is caught in hours not weeks).

Mirrors the test style of
``genlab-core/tests/monitoring/test_check_publish_silence.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from genlab_core.monitoring.health_monitor import (
    check_content_pool_health,
    check_engagement_health,
)


def _fake_pg_connect(fetchone_value):
    """Build a fake pg_connect context manager whose cursor.fetchone
    returns the given tuple."""
    cur = MagicMock()
    cur.fetchone.return_value = fetchone_value
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    conn.cursor.return_value.__exit__.return_value = None
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = None
    return cm


def _patch_engagement_connect(cm):
    return patch(
        "genlab_core.storage.tenant_context.pg_connect",
        return_value=cm,
    )


# ─── engagement health ─────────────────────────────────────────────


class TestEngagementHealth:
    def test_healthy_no_alerts(self):
        """Rows exist + writes in last 24h + recent completion → no alert."""
        # (total, last_24h, latest_completed)
        recent = datetime.now(UTC) - timedelta(hours=2)
        cm = _fake_pg_connect((50, 5, recent))
        with _patch_engagement_connect(cm):
            alerts = check_engagement_health()
        assert alerts == []

    def test_no_writes_in_24h_with_existing_rows_warns(self):
        """The exact 2026-05-21 outage shape: poller stopped writing
        but the table still has historical rows."""
        cm = _fake_pg_connect((50, 0, None))
        with _patch_engagement_connect(cm):
            alerts = check_engagement_health()
        assert len(alerts) >= 1
        assert any(a.check == "engagement_no_recent_writes" for a in alerts)
        no_writes = [a for a in alerts if a.check == "engagement_no_recent_writes"][0]
        assert no_writes.severity == "warning"
        assert "AGENT_ROOT" in no_writes.message  # actionable hint

    def test_completions_stalled_48h_warns(self):
        """Producer firing but consumer wedged on poison message."""
        old = datetime.now(UTC) - timedelta(hours=72)
        cm = _fake_pg_connect((100, 10, old))
        with _patch_engagement_connect(cm):
            alerts = check_engagement_health()
        stalled = [a for a in alerts if a.check == "engagement_completion_stalled"]
        assert len(stalled) == 1
        assert stalled[0].severity == "warning"
        assert "72h" in stalled[0].message

    def test_empty_table_no_alerts(self):
        """Fresh deployment / brand new install — table exists but empty.
        No alert (we don't want to false-alarm on initial state)."""
        cm = _fake_pg_connect((0, 0, None))
        with _patch_engagement_connect(cm):
            alerts = check_engagement_health()
        assert alerts == []

    def test_db_failure_silent_no_crash(self):
        """If DB is unreachable, probe must NOT crash the monitor."""
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            side_effect=Exception("connection refused"),
        ):
            alerts = check_engagement_health()
        assert alerts == []


# ─── content_pool health ─────────────────────────────────────────────


class TestContentPoolHealth:
    def test_healthy_no_alerts(self):
        """Producer fresh + consumer claiming >5% → no alert."""
        # (last_24h, last_7d, claimed_7d) — 30% claim rate
        cm = _fake_pg_connect((100, 1000, 300))
        with _patch_engagement_connect(cm):
            alerts = check_content_pool_health()
        assert alerts == []

    def test_no_24h_writes_warns(self):
        """shared_ingestion stopped firing."""
        cm = _fake_pg_connect((0, 1000, 100))
        with _patch_engagement_connect(cm):
            alerts = check_content_pool_health()
        silent = [a for a in alerts if a.check == "content_pool_producer_silent"]
        assert len(silent) == 1
        assert silent[0].severity == "warning"

    def test_low_claim_rate_warns(self):
        """The exact AUTONOMY-GAP-ANALYSIS finding: 77% bypass
        classifier means claim rate ~23%. Below 5% would mean
        producer firing but consumer entirely bypassing the pool."""
        # 100 routed, only 3 claimed (3% claim rate)
        cm = _fake_pg_connect((50, 100, 3))
        with _patch_engagement_connect(cm):
            alerts = check_content_pool_health()
        bypass = [a for a in alerts if a.check == "content_pool_consumer_bypass"]
        assert len(bypass) == 1
        assert "3.0%" in bypass[0].message
        assert bypass[0].details["claim_pct"] == 3.0

    def test_claim_rate_above_threshold_no_alert(self):
        """5% threshold — exactly at threshold is fine (>5% required)."""
        # 100 routed, 6 claimed (6% claim rate)
        cm = _fake_pg_connect((50, 100, 6))
        with _patch_engagement_connect(cm):
            alerts = check_content_pool_health()
        bypass = [a for a in alerts if a.check == "content_pool_consumer_bypass"]
        assert len(bypass) == 0

    def test_zero_in_7d_no_division_by_zero(self):
        """Defensive: when last_7d is 0, must not div/0."""
        cm = _fake_pg_connect((0, 0, 0))
        with _patch_engagement_connect(cm):
            alerts = check_content_pool_health()
        # Producer silent alert fires, claim-rate alert skipped (0/0 guard)
        assert any(a.check == "content_pool_producer_silent" for a in alerts)
        assert not any(a.check == "content_pool_consumer_bypass" for a in alerts)

    def test_db_failure_silent_no_crash(self):
        """Same fail-open posture as engagement health probe."""
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            side_effect=Exception("DB down"),
        ):
            alerts = check_content_pool_health()
        assert alerts == []


# ─── wired into run_all_checks ──────────────────────────────────────


class TestProbeWiring:
    def test_both_probes_wired_in_run_all_checks_system_wide(self):
        """Source-level pin: run_all_checks must invoke both probes
        in its system-wide section. A refactor that drops either re-
        introduces the half-wired invisibility this PR closes."""
        from pathlib import Path

        from genlab_core.monitoring import health_monitor as mod

        src = Path(mod.__file__).read_text()
        # Pin the call sites verbatim
        assert "all_alerts.extend(check_engagement_health())" in src
        assert "all_alerts.extend(check_content_pool_health())" in src

    def test_probes_only_fire_on_full_run(self):
        """Per-niche invocation shouldn't fire system-wide probes
        (avoid 5x duplicate alerts)."""
        from pathlib import Path

        from genlab_core.monitoring import health_monitor as mod

        src = Path(mod.__file__).read_text()
        # Pin that the CALL SITES (not the defs above) are inside the
        # ``if niche_id is None:`` block. Use rfind so the def's
        # function-name reference doesn't shadow the actual call.
        idx_if = src.find("if niche_id is None:")
        idx_engagement_call = src.rfind("all_alerts.extend(check_engagement_health())")
        idx_content_pool_call = src.rfind("all_alerts.extend(check_content_pool_health())")
        assert idx_if > 0 and idx_engagement_call > idx_if and idx_content_pool_call > idx_if, (
            "Probes must fire only on full (niche_id=None) run, not per-niche"
        )
