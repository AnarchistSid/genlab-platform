"""Pin tests for content_gap_remediator systemd units."""

from __future__ import annotations

from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_PHASE2_DIR = _GENLAB_ROOT / "deploy" / "systemd-phase2"


def test_service_present():
    assert (_PHASE2_DIR / "genlab-content-gap-remediator.service").is_file()


def test_timer_present():
    assert (_PHASE2_DIR / "genlab-content-gap-remediator.timer").is_file()


def test_service_uses_apply():
    content = (_PHASE2_DIR / "genlab-content-gap-remediator.service").read_text()
    assert "auto_remediate_content_gap.py --apply" in content


def test_service_has_failure_alert():
    content = (_PHASE2_DIR / "genlab-content-gap-remediator.service").read_text()
    assert "OnFailure=genlab-service-failure-alert@%n.service" in content


def test_timer_frequency_at_least_hourly():
    """Must fire ≥ 2×/hour so alerts get remediated within ~30 min."""
    content = (_PHASE2_DIR / "genlab-content-gap-remediator.timer").read_text()
    on_calendar_lines = [
        line for line in content.splitlines()
        if line.strip().startswith("OnCalendar=")
    ]
    assert on_calendar_lines
    # 15,45 fires twice per hour
    assert "15,45" in on_calendar_lines[0] or "*:15" in content


def test_timer_persistent():
    content = (_PHASE2_DIR / "genlab-content-gap-remediator.timer").read_text()
    assert "Persistent=true" in content
