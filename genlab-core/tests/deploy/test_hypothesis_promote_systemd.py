"""Pin: hypothesis-promote systemd units exist + are well-formed.

Added 2026-07-23 alongside scripts/auto_promote_hypotheses_to_findings.py.
Ensures the systemd bundle continues to ship the timer that keeps
learning_findings populated from fresh strategist reports.
"""

from __future__ import annotations

from pathlib import Path

_GENLAB_ROOT = Path(__file__).resolve().parents[3]
_PHASE2_DIR = _GENLAB_ROOT / "deploy" / "systemd-phase2"


def test_hypothesis_promote_service_present():
    unit = _PHASE2_DIR / "genlab-hypothesis-promote.service"
    assert unit.is_file(), (
        f"{unit} is missing. Without this the hypothesis promoter never "
        "runs on prod, and learning_findings stays empty even when the "
        "strategist writes fresh reports. Regression of the 2026-07-23 "
        "intelligence-loop closure."
    )


def test_hypothesis_promote_timer_present():
    unit = _PHASE2_DIR / "genlab-hypothesis-promote.timer"
    assert unit.is_file(), (
        f"{unit} is missing. Without the timer the service exists but "
        "never fires."
    )


def test_hypothesis_promote_service_calls_apply():
    """The unit MUST pass --apply to the script — dry-run in a systemd
    fire is worthless."""
    content = (
        _PHASE2_DIR / "genlab-hypothesis-promote.service"
    ).read_text()
    assert "auto_promote_hypotheses_to_findings.py --apply" in content, (
        "Service unit must invoke the script with --apply. Without it, "
        "the systemd fire produces a dry-run summary and writes NOTHING "
        "to learning_findings — the whole point of the timer is defeated."
    )


def test_hypothesis_promote_service_has_failure_alert():
    """Every genlab-* service must OnFailure into the shared failure
    alerter (matches the mass OnFailure coverage sweep, 2026-07-14)."""
    content = (
        _PHASE2_DIR / "genlab-hypothesis-promote.service"
    ).read_text()
    assert "OnFailure=genlab-service-failure-alert@%n.service" in content, (
        "Service must route failures to the shared failure-alert template. "
        "Without it a systemd exit=3 never reaches pipeline_alerts and "
        "operator won't know the promoter stopped working."
    )


def test_hypothesis_promote_timer_has_persistent():
    """Weekly-cadence timers must set Persistent=true so a boot-time
    catch-up runs missed fires (rule #21)."""
    content = (
        _PHASE2_DIR / "genlab-hypothesis-promote.timer"
    ).read_text()
    assert "Persistent=true" in content, (
        "Timer must set Persistent=true — otherwise a Sunday when the "
        "VPS was down silently skips the promote until next Sunday. "
        "Rule #21 (CLAUDE.md) applies to any timer where missing a fire "
        "leaves state stale."
    )


def test_hypothesis_promote_timer_fires_after_strategist():
    """Timer must fire AFTER the Sunday 02:00 UTC strategist run so it
    sees the freshest reports."""
    content = (
        _PHASE2_DIR / "genlab-hypothesis-promote.timer"
    ).read_text()
    # Time is 03:15 UTC daily; strategist runs 02:00 UTC. As long as
    # the fire is after 02:00 the sequencing is right.
    assert "03:15:00 UTC" in content or "03:1" in content
