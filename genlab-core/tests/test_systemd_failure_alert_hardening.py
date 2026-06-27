"""Pin: 2026-06-27 hardening of systemd_failure_alert.sh — journal
fallback + psql `timeout 15` + bumped systemd `TimeoutSec=120`.

Background: on 2026-06-22 the failure-alert script's psql write hung
during a transient DB lock. systemd SIGTERMed it at the 60s TimeoutSec.
The failure-alert instance entered "failed" state and STAYED there
for 4 days because no operator was checking, AND nothing in the deploy
or verify path was watching for failed-state instances of this
template unit. The whole point of this script is to surface OTHER
services' failures — if it silently fails, every other monitored
service becomes unobservable.

This pin locks in 4 hardenings so a future refactor can't quietly
re-create the 4-day blackout window:
  1. logger -p user.crit fallback runs BEFORE the DB attempt
  2. psql is wrapped with `timeout 15`
  3. systemd unit TimeoutSec is 120 (was 60)
  4. post_deploy_verify.sh detects + auto-resets failed instances
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "systemd_failure_alert.sh"
UNIT = REPO_ROOT / "deploy" / "systemd-phase2" / "genlab-service-failure-alert@.service"
VERIFY = REPO_ROOT / "scripts" / "post_deploy_verify.sh"


class TestJournalFallback:
    def test_logger_fallback_present(self):
        """The `logger -p user.crit` call MUST exist so journalctl
        has a record of the failure even if the DB write times out."""
        content = SCRIPT.read_text()
        assert "logger -p user.crit" in content, (
            "systemd_failure_alert.sh must call `logger -p user.crit` "
            "to write a journal entry that survives DB hangs. Without "
            "it, a transient psql lock recreates the 2026-06-22 "
            "4-day silent failure window."
        )
        assert "genlab-failure-alert" in content, (
            "logger call must tag with `-t genlab-failure-alert` so "
            "journalctl -t genlab-failure-alert finds the records."
        )

    def test_logger_runs_before_psql(self):
        """The logger fallback must run BEFORE the psql attempt, not
        after. Otherwise a psql hang prevents the logger call entirely.
        Order matters."""
        content = SCRIPT.read_text()
        logger_pos = content.find("logger -p user.crit")
        psql_pos = content.find("psql -h")
        assert logger_pos > 0, "logger call missing"
        assert psql_pos > 0, "psql call missing"
        assert logger_pos < psql_pos, (
            f"logger -p user.crit (pos={logger_pos}) must run BEFORE "
            f"psql -h (pos={psql_pos}). If psql hangs, the journal "
            f"fallback only survives if it was already executed."
        )


class TestPsqlTimeout:
    def test_psql_wrapped_with_timeout(self):
        """psql MUST be wrapped with `timeout 15` so a hung DB
        connection can't drag the whole script past systemd's
        TimeoutSec budget. 15s is generous for healthy psql; 120s
        systemd ceiling gives 8x headroom over that worst case."""
        content = SCRIPT.read_text()
        # The wrapper invokes `timeout 15 bash -c '... psql ...'`.
        assert "timeout 15" in content, (
            "systemd_failure_alert.sh must wrap psql with `timeout 15` "
            "to prevent the 2026-06-22 hang failure mode. Without it, "
            "a transient DB lock can keep the script running past the "
            "systemd TimeoutSec and trip into failed state."
        )
        # And the psql command itself should be inside that timeout
        # block, not separate.
        timeout_pos = content.find("timeout 15")
        psql_pos = content.find("psql -h", timeout_pos)
        assert psql_pos > timeout_pos, (
            "the psql call after `timeout 15` should be inside the "
            "wrapper, not a separate unconstrained psql invocation."
        )


class TestSystemdTimeoutBump:
    def test_unit_timeout_is_120(self):
        """Template unit TimeoutSec MUST be 120s (bumped from 60s).
        The script's internal `timeout 15` provides the real safety;
        the systemd budget only needs to exceed worst-case healthy
        runtime, which is ~15s + cleanup. 120s gives 8x headroom +
        matches sibling oneshot units (niche_pause_sweeper, etc.)."""
        content = UNIT.read_text()
        assert "TimeoutSec=120" in content, (
            "TimeoutSec must be 120s in the template unit (was 60s "
            "before 2026-06-27 hardening). 60s was too tight when "
            "the DB hung transiently."
        )
        # And the OLD 60s value must NOT still be present.
        for line in content.splitlines():
            if line.strip().startswith("TimeoutSec="):
                assert line.strip() == "TimeoutSec=120", (
                    f"TimeoutSec line must be exactly `TimeoutSec=120`, got: {line!r}"
                )


class TestVerifyHarnessDetectsFailedInstances:
    def test_verify_harness_checks_failure_alert_template(self):
        """post_deploy_verify.sh MUST check for genlab-service-failure-
        alert@* instances in failed state AND auto-reset them.
        Without this, the 2026-06-22 "stuck-failed-for-4-days"
        scenario can recur silently."""
        content = VERIFY.read_text()
        assert "genlab-service-failure-alert@*" in content, (
            "post_deploy_verify.sh must include a check for the "
            "failure-alert template's failed-state instances."
        )
        assert "reset-failed" in content, (
            "post_deploy_verify.sh must auto-reset failed instances of "
            "the failure-alert template. Otherwise nothing recovers "
            "them between deploys."
        )


class TestJournalctlFallback:
    """Pin: 2026-06-28 — the failure-alert script must fall back to an
    untimed `journalctl -n 20` when the `--since "5 minutes ago"` window
    returns empty.

    The 2026-06-27 deploy-restart-sweep produced 7 unresolved CRITICAL
    alerts whose entire diagnostic context was the literal string
    "(journalctl failed)". Operators saw this in every alert and lost
    trust in the diagnostic text — zero useful info carried through.
    Root cause: the 5-min window was empty (busy moment, journal
    locked, oneshots that finished too fast for the window) and the
    `|| echo "(journalctl failed)"` fired.

    Pin guards against regressing to a single windowed call without
    untimed fallback.
    """

    def test_untimed_fallback_present(self):
        """The script must include a fallback `-n 20` call (no `--since`
        filter) for when the time-windowed query returns empty."""
        content = SCRIPT.read_text()
        # The fallback uses `-n 20` (last-20-lines syntax) without
        # `--since`. The first call uses `--since`. Both must coexist.
        assert "--since" in content, (
            "First-pass journalctl call should use --since to scope to "
            "the recent failure window."
        )
        assert "-n 20" in content, (
            "Fallback journalctl call must use `-n 20` to get the last "
            "20 lines regardless of time window. Without this, an empty "
            "5-min window leaves operators with no diagnostic context "
            "(2026-06-27 incident: 7 alerts with '(journalctl failed)' "
            "literal as their only context)."
        )

    def test_does_not_emit_journalctl_failed_string(self):
        """The legacy '(journalctl failed)' string is replaced with a
        more actionable '(journalctl unavailable — try: …)' message
        that includes the manual command. Pin against the legacy
        string regressing into the script."""
        content = SCRIPT.read_text()
        assert "(journalctl failed)" not in content, (
            "Legacy '(journalctl failed)' string must not appear. The "
            "operator-facing replacement is "
            "'(journalctl unavailable — try: journalctl -u <unit> -n 50)' "
            "which tells the operator what to do, not what didn't work."
        )

    def test_actionable_unavailable_message_present(self):
        """When both calls return empty, the message should tell the
        operator HOW to investigate, not just that something failed."""
        content = SCRIPT.read_text()
        assert "journalctl unavailable" in content, (
            "Fallback-of-fallback message should say 'journalctl "
            "unavailable' (descriptive, not blaming)."
        )
        assert "try:" in content.lower() or "journalctl -u " in content, (
            "Fallback message must include the manual command for the "
            "operator to run — operator-actionable diagnostics."
        )

    def test_fallback_runs_AFTER_windowed_call(self):
        """The order matters: the windowed call must come FIRST (gives
        recent context when it works), the untimed `-n 20` falls back
        only when the first call returns empty. Reversing the order
        would leak older/unrelated context into every alert."""
        content = SCRIPT.read_text()
        since_pos = content.find('--since "5 minutes ago"')
        # Find the LAST occurrence of -n 20 so we don't accidentally
        # match unrelated text earlier in the file.
        fallback_pos = content.rfind("-n 20")
        assert since_pos > 0, "--since '5 minutes ago' call must be present"
        assert fallback_pos > 0, "-n 20 fallback call must be present"
        assert since_pos < fallback_pos, (
            f"--since call (pos={since_pos}) must precede the -n 20 "
            f"fallback (pos={fallback_pos}). The windowed call is the "
            f"preferred path; -n 20 only fires when it returns empty."
        )
