"""Pin: engagement-question A/B report systemd unit files exist.

Guards against the units being accidentally deleted from
deploy/systemd-phase2/. Installation to /etc/systemd/system/ on the
prod VPS is a separate operator step; this test only verifies the
templates exist + are syntactically valid.

If this test fails, the weekly A/B report timer was probably
removed during a deploy cleanup. Restore from git history.
"""

from __future__ import annotations

from pathlib import Path

_DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd-phase2"

_SERVICE = _DEPLOY_DIR / "genlab-engagement-question-ab-report.service"
_TIMER = _DEPLOY_DIR / "genlab-engagement-question-ab-report.timer"


class TestUnitFilesExist:
    def test_service_file_exists(self):
        assert _SERVICE.exists(), f"Missing service unit at {_SERVICE}"

    def test_timer_file_exists(self):
        assert _TIMER.exists(), f"Missing timer unit at {_TIMER}"


class TestServiceContent:
    def test_service_runs_correct_module(self):
        """Pin: ExecStart must call the A/B analysis module. Catches
        accidental copy-paste from another service template that
        leaves wrong module name."""
        content = _SERVICE.read_text()
        assert "python -m genlab_core.tools.analyze_engagement_question_ab" in content

    def test_service_type_oneshot(self):
        assert "Type=oneshot" in _SERVICE.read_text()

    def test_service_runs_as_genlab_user(self):
        """Rule #15: state files systemd services read/write must be
        owned by genlab:genlab, not root. Runs-as-user pin catches
        template that forgot User=/Group=."""
        content = _SERVICE.read_text()
        assert "User=genlab" in content
        assert "Group=genlab" in content

    def test_service_has_env_file(self):
        """Env-file pin so the flag flips propagate to this timer's
        fire (matches every other genlab systemd service)."""
        assert "EnvironmentFile=/opt/genlab/.env" in _SERVICE.read_text()

    def test_service_has_on_failure_alert(self):
        """Rule from 2026-07-14 mass OnFailure coverage — every
        service routes failures to the shared alert template."""
        assert "OnFailure=genlab-service-failure-alert@%n.service" in _SERVICE.read_text()

    def test_service_has_reasonable_timeout(self):
        """The A/B analysis reads 14 days of blueprints + JOINs
        publishing_analytics; on a 4 GB VPS with 5 niches × 3
        platforms this should complete in well under 5 minutes.
        Cap at 300s so a runaway query doesn't block other timers."""
        content = _SERVICE.read_text()
        assert "TimeoutSec=" in content
        # Extract and sanity-check
        import re
        match = re.search(r"TimeoutSec=(\d+)", content)
        assert match is not None
        timeout = int(match.group(1))
        assert 60 <= timeout <= 1800


class TestTimerContent:
    def test_timer_weekly_calendar(self):
        content = _TIMER.read_text()
        assert "OnCalendar=Mon" in content

    def test_timer_persistent_true(self):
        """Rule #21: weekly timers MUST be Persistent=true so a
        missed fire (VPS reboot the day the timer would run) catches
        up on next boot rather than silently skipping until next week."""
        content = _TIMER.read_text()
        assert "Persistent=true" in content, (
            "Weekly timer without Persistent=true will silently skip "
            "fires when the VPS is down at fire time (rule #21)"
        )

    def test_timer_points_to_correct_service(self):
        content = _TIMER.read_text()
        assert "Unit=genlab-engagement-question-ab-report.service" in content
