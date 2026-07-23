"""Pin tests for the engagement-drainer systemd units."""

from __future__ import annotations

from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_PHASE2_DIR = _GENLAB_ROOT / "deploy" / "systemd-phase2"


def test_service_present():
    unit = _PHASE2_DIR / "genlab-engagement-drainer.service"
    assert unit.is_file()


def test_timer_present():
    unit = _PHASE2_DIR / "genlab-engagement-drainer.timer"
    assert unit.is_file()


def test_service_uses_apply_flag():
    """Timer-run must call the script with --apply (dry-run is the
    default without it — pointless from systemd)."""
    content = (_PHASE2_DIR / "genlab-engagement-drainer.service").read_text()
    assert "drain_engagement_review_queue.py --apply" in content


def test_timer_persistent():
    content = (_PHASE2_DIR / "genlab-engagement-drainer.timer").read_text()
    assert "Persistent=true" in content


def test_timer_fires_at_least_daily():
    """Must fire ≥ 4 times/day so 24h-stale items get drained in a
    predictable window."""
    content = (_PHASE2_DIR / "genlab-engagement-drainer.timer").read_text()
    # Check that at least 4 hours are listed in OnCalendar
    on_calendar_lines = [
        line for line in content.splitlines()
        if line.strip().startswith("OnCalendar=")
    ]
    assert on_calendar_lines
    # The single-line form with comma-separated hours must include ≥4 hours
    hour_count = 0
    for line in on_calendar_lines:
        # 00,04,08,12,16,20:30:00 UTC → 6 hours
        parts = line.split("=", 1)[1].strip().split(":")[0]
        hour_count += len([h for h in parts.split(",") if h.strip().isdigit()])
    assert hour_count >= 4, (
        f"Timer must fire ≥4×/day for a 4-hour SLA on 24h-stale items; "
        f"found {hour_count} scheduled hours."
    )


def test_service_has_failure_alert():
    content = (_PHASE2_DIR / "genlab-engagement-drainer.service").read_text()
    assert "OnFailure=genlab-service-failure-alert@%n.service" in content
