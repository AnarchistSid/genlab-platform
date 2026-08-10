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


class TestCheck5ShaPrefixMatch:
    """2026-08-10 regression pin: Check 5 (`.version.env` matches git HEAD)
    must accept both short (deploy.sh's `--short` convention) AND full
    (40-char) SHA formats. Previously used strict ``[ "$X" = "$Y" ]``
    which false-fired when .version.env was hand-written with the full
    SHA. Surfaced during a drift-recovery deploy that wrote the full
    SHA — post_deploy_verify.sh reported failure even though the
    underlying commit was correct."""

    def test_check_5_uses_prefix_match_not_strict_equality(self):
        content = _SCRIPT.read_text()
        # The fix has BOTH short + full HEAD probes so either write style
        # produces a pass. Regression: if a refactor drops the full probe
        # and reverts to short-only, this test surfaces it.
        assert "rev-parse --short HEAD" in content, (
            "Check 5 must still probe the short SHA (deploy.sh convention)."
        )
        assert "rev-parse HEAD" in content, (
            "Check 5 must ALSO probe the full SHA (post 2026-08-10 fix) so "
            "hand-written .version.env with full SHA passes. Regression: "
            "reverting to short-only re-introduces the drift-recovery bug."
        )
        # Prefix-match idiom — `${git_head_full#$deployed_sha}` returns
        # the trailing bytes when deployed_sha is a prefix. This is the
        # POSIX shell way to do prefix-match without bash `==` glob.
        assert "${git_head_full#$deployed_sha}" in content, (
            "Check 5 must use prefix-match via parameter expansion "
            "'${git_head_full#$deployed_sha}' (POSIX shell prefix strip)."
        )

    def test_check_5_still_fails_on_completely_different_sha(self):
        """Pin: prefix-match relaxation is bounded — a totally unrelated
        SHA still fails. The check reads the file, so we look for the
        error path being preserved."""
        content = _SCRIPT.read_text()
        assert "re-run deploy.sh" in content, (
            "Check 5's fail-message hint must survive the prefix-match "
            "refactor — operators rely on it to know the fix path."
        )


class TestDeployShForceFlag:
    """2026-08-10 regression pin: deploy.sh must support --force AND
    auto-detect stale .version.env when HEAD is already at origin/main.
    Previously the 'nothing to deploy' short-circuit at line ~210
    unconditionally exited 0 when up-to-date, blocking Phase 6.5
    (.version.env write) + Phase 7 (service restart) — the exact fix
    path for drift recovery. Surfaced during Aug 10 drift-recovery
    when I'd already pulled manually and needed deploy.sh to run
    only the write + restart phases."""

    _DEPLOY_SH = _ROOT / "scripts" / "deploy.sh"

    def test_force_flag_parsed(self):
        content = self._DEPLOY_SH.read_text()
        assert "--force)" in content, (
            "deploy.sh must accept --force flag (added 2026-08-10). "
            "Without it, drift-recovery deploys where HEAD is already "
            "up-to-date can't run Phase 6.5 + 7 to fix .version.env "
            "and restart services."
        )
        assert "FORCE=1" in content, (
            "deploy.sh --force must set FORCE=1 in the arg parser."
        )

    def test_short_circuit_gates_on_force_or_stale_version_env(self):
        """The gate at line ~210 must consult FORCE and STALE_VERSION_ENV
        before exit-0. Regression: a refactor that removes this gate
        breaks drift recovery + re-introduces the Aug 10 bug."""
        content = self._DEPLOY_SH.read_text()
        assert "STALE_VERSION_ENV" in content, (
            "deploy.sh must compute STALE_VERSION_ENV by comparing "
            "on-disk .version.env's GENLAB_GIT_COMMIT against HEAD."
        )
        assert '"$FORCE" -ne 1 && "$STALE_VERSION_ENV" -ne 1' in content, (
            "The 'nothing to deploy' short-circuit must be gated on both "
            "FORCE and STALE_VERSION_ENV — otherwise --force alone can't "
            "override, and stale .version.env can't self-recover."
        )

    def test_help_text_no_longer_contradictory(self):
        """Old help text said '--apply --skip-migrate --skip-restart to
        force-restart anyway' — internally contradictory (skip-restart
        skips the restart you want to force). New help must reference
        --force clearly."""
        content = self._DEPLOY_SH.read_text()
        # The old contradiction shouldn't be present anymore
        assert "--skip-restart to force-restart" not in content, (
            "Old contradictory help ('--skip-restart to force-restart') "
            "must be replaced with the correct --force flag guidance."
        )
        assert "--force" in content, (
            "Help text must describe --force as the way to run Phases 5.5+ "
            "when HEAD is already up-to-date."
        )

    def test_phase_7_unit_installed_check_uses_systemctl_cat(self):
        """2026-08-10 (Bug 4): Phase 7's 'is unit installed?' check must
        use `systemctl cat <unit>` (exit-code based) rather than parsing
        `systemctl list-unit-files --plain | grep`. The grep approach
        false-negatived under heavy systemd load — during the 18:28
        deploy, 3 of 5 services were skipped as 'not installed' even
        though they were installed + running, because the intermediate
        dashboard restart (13s) put systemctl into a state where
        list-unit-files output was momentarily incomplete."""
        content = self._DEPLOY_SH.read_text()
        # The install check must use `systemctl cat` (exit-code based)
        assert 'if systemctl cat "$unit" >/dev/null 2>&1' in content, (
            "Phase 7's unit-installed check must be "
            "`if systemctl cat \"$unit\" >/dev/null 2>&1`. The old "
            "grep-based check on list-unit-files output was fragile "
            "under load; new check is exit-code based, no parsing."
        )
        # Old fragile check line must be gone — check that the ACTIVE
        # code line (not comments/docs) doesn't do the grep. Detect via
        # the specific `if systemctl list-unit-files ... grep -q "^$unit "`
        # pattern that WAS the check.
        assert "if systemctl list-unit-files" not in content, (
            "Old grep-based unit-installed check must be removed from "
            "the active code path. Regression: reverting to grep-based "
            "parsing produces intermittent 'not installed' false-negatives "
            "during multi-service restart loops."
        )


class TestEngagementWorkerSuccessExitStatus:
    """2026-08-10 regression pin: genlab-engagement-worker.service must
    declare SuccessExitStatus=1 SIGTERM to suppress OnFailure alerts
    from Dramatiq's graceful-shutdown exit-1 pattern. Every
    `systemctl restart genlab-engagement-worker` used to fire a
    spurious systemd_unit_failed CRITICAL row because Dramatiq exits
    with status 1 on SIGTERM (not 0)."""

    _UNIT = _DEPLOY_DIR / "genlab-engagement-worker.service"

    def test_unit_declares_success_exit_status(self):
        content = self._UNIT.read_text()
        assert "SuccessExitStatus=1 SIGTERM" in content, (
            "genlab-engagement-worker.service must declare "
            "'SuccessExitStatus=1 SIGTERM' so Dramatiq's SIGTERM-induced "
            "exit-1 doesn't fire spurious OnFailure alerts on every "
            "legitimate restart. Regression: removing this line brings "
            "back the Aug 10 alert-flood pattern."
        )

    def test_unit_still_has_restart_always(self):
        """SuccessExitStatus works in tandem with Restart=always —
        Restart still auto-recovers on real crashes; SuccessExitStatus
        only prevents the OnFailure alert on graceful-SIGTERM exits.
        If either is missing, the fix is incomplete."""
        content = self._UNIT.read_text()
        assert "Restart=always" in content, (
            "Restart=always must remain — SuccessExitStatus only affects "
            "failed-state classification, not auto-restart. Without "
            "Restart=always the service wouldn't recover from real crashes."
        )

    def test_unit_still_has_onfailure_alert(self):
        """OnFailure must stay wired — the fix is to reduce false positives
        from graceful shutdown, NOT to disable failure alerting entirely."""
        content = self._UNIT.read_text()
        assert "OnFailure=genlab-service-failure-alert" in content, (
            "OnFailure alerting must remain — SuccessExitStatus only "
            "suppresses alerts on graceful-SIGTERM exits (exit 1). Real "
            "crashes (other exit codes) still fire the alert as expected."
        )
