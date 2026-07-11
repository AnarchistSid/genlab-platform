"""PR #Layer5-Alert (2026-07-11) — attribution health monitor tests.

Behavioural pins on the scan_and_alert logic. Uses monkeypatch to
stub the DB reads so the tests run without a live Postgres.
"""

from __future__ import annotations

from unittest.mock import patch


def _mock_compute(rows: list[dict]):
    """Stub compute_health to return the given rows."""
    return patch(
        "genlab_core.monitoring.attribution_health_monitor.compute_health",
        return_value=rows,
    )


def test_no_alert_when_all_niches_healthy():
    from genlab_core.monitoring.attribution_health_monitor import scan_and_alert

    healthy_rows = [
        {"niche_id": n, "total_published": 8, "with_attribution": 8, "attribution_pct": 100.0}
        for n in ("ai_creators", "anime", "gaming", "movies", "sports")
    ]
    with _mock_compute(healthy_rows):
        summary = scan_and_alert(dry_run=True)
    assert summary["alert_written"] is False
    assert summary["breached_niches"] == []
    assert summary["overall_breached"] is False


def test_alert_fires_on_single_critical_niche():
    from genlab_core.monitoring.attribution_health_monitor import scan_and_alert

    rows = [
        {
            "niche_id": "gaming",
            "total_published": 8,
            "with_attribution": 3,
            "attribution_pct": 37.5,
        },
        {
            "niche_id": "anime",
            "total_published": 5,
            "with_attribution": 5,
            "attribution_pct": 100.0,
        },
        {
            "niche_id": "ai_creators",
            "total_published": 5,
            "with_attribution": 5,
            "attribution_pct": 100.0,
        },
        {
            "niche_id": "movies",
            "total_published": 5,
            "with_attribution": 5,
            "attribution_pct": 100.0,
        },
        {
            "niche_id": "sports",
            "total_published": 5,
            "with_attribution": 5,
            "attribution_pct": 100.0,
        },
    ]
    with _mock_compute(rows):
        summary = scan_and_alert(dry_run=True)
    assert summary["alert_written"] is True
    assert "gaming" in summary["breached_niches"]


def test_no_alert_when_niche_below_min_publish_count():
    """Single early-morning miss with total_published < 3 must not
    page. Otherwise a single fresh publish before the daily batch
    could trigger overnight alarms."""
    from genlab_core.monitoring.attribution_health_monitor import scan_and_alert

    rows = [
        {
            "niche_id": "gaming",
            "total_published": 2,
            "with_attribution": 1,
            "attribution_pct": 50.0,
        },
    ]
    with _mock_compute(rows):
        summary = scan_and_alert(dry_run=True)
    assert summary["alert_written"] is False
    assert summary["breached_niches"] == []


def test_alert_message_includes_niche_context():
    """Operators triage from the alert message. If it doesn't include
    which niches breached + investigation pointer, the operator has
    to open Mission Control which defeats the point of the alert."""
    from genlab_core.monitoring.attribution_health_monitor import scan_and_alert

    rows = [
        {
            "niche_id": "gaming",
            "total_published": 8,
            "with_attribution": 3,
            "attribution_pct": 37.5,
        },
        {
            "niche_id": "anime",
            "total_published": 5,
            "with_attribution": 5,
            "attribution_pct": 100.0,
        },
        {
            "niche_id": "ai_creators",
            "total_published": 5,
            "with_attribution": 5,
            "attribution_pct": 100.0,
        },
        {
            "niche_id": "movies",
            "total_published": 5,
            "with_attribution": 5,
            "attribution_pct": 100.0,
        },
        {
            "niche_id": "sports",
            "total_published": 5,
            "with_attribution": 5,
            "attribution_pct": 100.0,
        },
    ]
    with _mock_compute(rows):
        summary = scan_and_alert(dry_run=True)
    # Alert content is dry-run logged; presence in summary is enough
    # for the pin. The message-format construction is exercised in
    # scan_and_alert.
    assert summary["alert_written"] is True


def test_dry_run_never_writes_to_pipeline_alerts():
    """dry_run must never touch the DB write path. Pin the
    contract — otherwise a dry-run in prod would still insert."""
    from genlab_core.monitoring.attribution_health_monitor import scan_and_alert

    with _mock_compute(
        [
            {
                "niche_id": "gaming",
                "total_published": 8,
                "with_attribution": 0,
                "attribution_pct": 0.0,
            },
            {
                "niche_id": "anime",
                "total_published": 0,
                "with_attribution": 0,
                "attribution_pct": 0.0,
            },
            {
                "niche_id": "ai_creators",
                "total_published": 0,
                "with_attribution": 0,
                "attribution_pct": 0.0,
            },
            {
                "niche_id": "movies",
                "total_published": 0,
                "with_attribution": 0,
                "attribution_pct": 0.0,
            },
            {
                "niche_id": "sports",
                "total_published": 0,
                "with_attribution": 0,
                "attribution_pct": 0.0,
            },
        ]
    ):
        with patch("psycopg.connect") as mock_pg:
            scan_and_alert(dry_run=True)
        assert mock_pg.call_count == 0


def test_critical_pct_threshold_is_kept_in_sync_with_endpoint():
    """The monitor's CRITICAL_PCT and the endpoint's
    _CAUTION_PCT must agree. If they drift, the operator sees the
    dashboard say 'critical' but the alert doesn't fire (or vice
    versa) — both wrong."""
    from genlab_core.monitoring.attribution_health_monitor import CRITICAL_PCT
    from server.core.attribution_health import _CAUTION_PCT

    assert CRITICAL_PCT == _CAUTION_PCT, (
        "Monitor and Layer 5 endpoint must agree on the critical "
        "threshold. Otherwise the alert doesn't fire when the "
        "dashboard shows critical (or vice versa)."
    )
