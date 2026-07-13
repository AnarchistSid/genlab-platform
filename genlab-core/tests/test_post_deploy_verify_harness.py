"""Pin: post-deploy verify harness script + systemd units exist and
reference the right invariants.

Background: 2026-06-26 deploy surfaced two "shipped but not activated"
failures that NOTHING auto-detected:
  1. PR #588's backfill SQL targeted a wide-column schema that doesn't
     exist on prod (actual is jsonb EAV)
  2. 3 of 4 dormant env flags were set in .env but never restarted
     into the dashboard process — silent no-op for weeks

The post_deploy_verify.sh harness + weekly systemd timer guard against
recurrence of this class of bug. This test file pins the harness
contract so accidental deletion / template-copy-paste regressions
fail loudly in CI.
"""

from __future__ import annotations

import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "post_deploy_verify.sh"
_DEPLOY_DIR = _ROOT / "deploy" / "systemd-phase2"
_SERVICE = _DEPLOY_DIR / "genlab-post-deploy-verify.service"
_TIMER = _DEPLOY_DIR / "genlab-post-deploy-verify.timer"


class TestPostDeployVerifyScript:
    def test_script_exists(self):
        """Pin: the verify script must exist at scripts/post_deploy_verify.sh."""
        assert _SCRIPT.exists(), (
            f"Missing post-deploy verify script at {_SCRIPT}. "
            f"This script is the foundation of the 'shipped but not "
            f"activated' detection loop — restore from git history."
        )

    def test_script_is_executable(self):
        """Pin: must have +x bit so systemd ExecStart works without chmod."""
        assert os.access(_SCRIPT, os.X_OK), (
            f"{_SCRIPT} is not executable. Run `chmod +x` on it before "
            f"committing — systemd ExecStart requires the bit."
        )

    def test_script_uses_env_bash_shebang(self):
        """Pin: portable shebang via /usr/bin/env bash (NOT /bin/bash) so
        the script works on both Hetzner Ubuntu prod box and macOS dev
        boxes where bash lives in different paths."""
        first_line = _SCRIPT.read_text().splitlines()[0]
        assert first_line == "#!/usr/bin/env bash", (
            f"Shebang must be '#!/usr/bin/env bash' (got {first_line!r}). "
            f"/bin/bash hardcodes a path that differs across OSes."
        )

    def test_script_references_key_invariants(self):
        """Pin: each check must be present in the script body. If a check
        gets accidentally deleted during refactor, the harness silently
        loses coverage — this test catches that."""
        content = _SCRIPT.read_text()
        required = [
            "niche_pauses",  # check #3 — niche_pauses table presence
            "compliance_events",  # check #3 — compliance_events table presence
            "source_channel_id",  # check #3 — source-discovery column presence
            "/etc/genlab/version.env",  # check #5 — version pin file
            "/api/v1/",  # check #4 — route reachability prefix
            "compliance/stats",  # check #4 — compliance stats endpoint path
            "scheduling/pauses",  # check #4 — niche-pauses endpoint path
            "source-discovery/proposals",  # check #4 — source discovery endpoint
            # Check #8 (2026-07-13 W1 audit follow-up): the writer wire
            # smoke check — queries recent captions for credit markers.
            # Deletion of this check would let a writer-wire class-of-bug
            # regression ship silently again (as it did for weeks before
            # the tightening exposed it). Reference multiple invariants
            # so a partial refactor still fires.
            "writer wire",  # the section note
            "🎬 Original:",  # the marker being queried
            "Footage:",  # the alternate marker
        ]
        missing = [r for r in required if r not in content]
        assert not missing, (
            f"post_deploy_verify.sh is missing references to: {missing}. "
            f"Each represents a critical activation check — re-add or "
            f"document why the check was removed."
        )


class TestPostDeployVerifySystemdUnits:
    def test_service_unit_exists(self):
        """Pin: service template must exist in deploy/systemd-phase2/."""
        assert _SERVICE.exists(), f"Missing {_SERVICE}. Restore from git history."

    def test_timer_unit_exists(self):
        """Pin: timer template must exist. Without it, the weekly drift
        check never fires and silent-activation bugs accumulate again."""
        assert _TIMER.exists(), f"Missing {_TIMER}. Restore from git history."

    def test_service_has_install_section(self):
        """Pin: [Install] WantedBy=multi-user.target so `systemctl enable`
        works. Without [Install], enable fails with 'No installation
        information found' and the unit silently never gets wired."""
        content = _SERVICE.read_text()
        assert "[Install]" in content, f"{_SERVICE} missing [Install] section"
        assert "WantedBy=multi-user.target" in content, (
            f"{_SERVICE} must declare WantedBy=multi-user.target so "
            f"systemctl enable persists across reboots."
        )

    def test_timer_has_install_section(self):
        """Pin: [Install] WantedBy=timers.target so the timer fires after
        every reboot. The standard target for all systemd timers."""
        content = _TIMER.read_text()
        assert "[Install]" in content, f"{_TIMER} missing [Install] section"
        assert "WantedBy=timers.target" in content, (
            f"{_TIMER} must declare WantedBy=timers.target — without it "
            f"the timer doesn't survive reboots."
        )

    def test_service_invokes_the_verify_script(self):
        """Pin: ExecStart must invoke the verify script — catches accidental
        copy-paste from a sibling unit that leaves the wrong path."""
        content = _SERVICE.read_text()
        assert "/opt/genlab/scripts/post_deploy_verify.sh" in content, (
            f"{_SERVICE} ExecStart must invoke /opt/genlab/scripts/"
            f"post_deploy_verify.sh — check for copy-paste errors."
        )
