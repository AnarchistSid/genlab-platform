"""Pin: auto_repair_permissions script behavior + systemd units present.

The 2026-06-22 operator-touchpoint audit found that permissions_drift
CRITICAL alerts required manual operator intervention (run
``sudo /opt/genlab/scripts/repair_permissions.sh --apply``). This
script automates that recovery by running as root via a 15-min timer.

These pins guard:
- The script fail-opens when DATABASE_URL is missing (won't crash on
  dev/test) — actually it returns 1 (pre-flight error) since DB is
  required; surfaces in journalctl so operator sees the misconfig.
- The kill switch (GENLAB_PERMISSIONS_REPAIR_ENABLED=0) actually
  short-circuits the script.
- Both systemd unit templates exist + reference the right script +
  run as root + load the right env.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

_DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy" / "systemd-phase2"
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "auto_repair_permissions.py"


class TestAutoRepairScript:
    def test_script_file_exists(self):
        """Pin: script must exist at the expected path so systemd unit
        can ExecStart it."""
        assert _SCRIPT.exists(), f"Script missing at {_SCRIPT}"

    def test_kill_switch_short_circuits(self, monkeypatch):
        """Pin: GENLAB_PERMISSIONS_REPAIR_ENABLED=0 must skip the DB
        connect + repair attempt entirely. The kill switch is the
        operator's panic button during incidents."""
        monkeypatch.setenv("GENLAB_PERMISSIONS_REPAIR_ENABLED", "0")

        # Import via path since the script isn't a package
        import importlib.util

        spec = importlib.util.spec_from_file_location("arp", str(_SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Patch _connect to ensure it would FAIL if reached — proves
        # the kill-switch short-circuits BEFORE touching the DB.
        with patch.object(mod, "_connect", side_effect=RuntimeError("DB called when disabled")):
            assert mod.main() == 0

    def test_default_enabled(self, monkeypatch):
        """Pin: default (no env set) is ENABLED. The kill switch is
        opt-OUT not opt-IN — the whole point of this script is
        unattended self-healing."""
        monkeypatch.delenv("GENLAB_PERMISSIONS_REPAIR_ENABLED", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)

        import importlib.util

        spec = importlib.util.spec_from_file_location("arp", str(_SCRIPT))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # With env-unset + DB missing, should attempt connect → exit 1
        # (pre-flight failure). NOT exit 0 (which would mean kill-switch
        # short-circuit).
        assert mod.main() == 1


class TestSystemdUnits:
    def test_service_unit_exists(self):
        path = _DEPLOY_DIR / "genlab-permission-auto-repair.service"
        assert path.exists(), f"Service unit missing at {path}"

    def test_timer_unit_exists(self):
        path = _DEPLOY_DIR / "genlab-permission-auto-repair.timer"
        assert path.exists(), f"Timer unit missing at {path}"

    def test_service_runs_as_root(self):
        """Pin: service MUST run as root — repair_permissions.sh chowns
        files back to genlab:genlab, which requires root capability.
        Running as genlab would fail with EPERM on every chown."""
        content = (_DEPLOY_DIR / "genlab-permission-auto-repair.service").read_text()
        assert "User=root" in content, (
            "Service must run as User=root — repair_permissions.sh requires root to chown files."
        )
        assert "Group=root" in content

    def test_service_invokes_correct_script(self):
        """Pin: ExecStart must point at auto_repair_permissions.py —
        guards against accidental copy-paste from sibling units."""
        content = (_DEPLOY_DIR / "genlab-permission-auto-repair.service").read_text()
        assert "scripts/auto_repair_permissions.py" in content

    def test_service_loads_dotenv(self):
        """Pin: must load /opt/genlab/.env for DATABASE_URL +
        GENLAB_PERMISSIONS_REPAIR_ENABLED."""
        content = (_DEPLOY_DIR / "genlab-permission-auto-repair.service").read_text()
        assert "EnvironmentFile=/opt/genlab/.env" in content

    def test_timer_fires_frequently(self):
        """Pin: timer must fire at sub-hourly cadence. Daily would
        leave drift unrepaired for up to 24h, breaking pipelines for
        operator-attendance windows. 15-min is the docstring's chosen
        cadence — pin the under-1-hour contract."""
        content = (_DEPLOY_DIR / "genlab-permission-auto-repair.timer").read_text()
        # The OnCalendar line should NOT be a daily-only fire
        assert "OnCalendar=*-*-* *:" in content, (
            "Timer must fire sub-hourly (OnCalendar=*-*-* *:MM... pattern). "
            "Daily fire would leave drift unrepaired too long."
        )

    def test_timer_has_persistent_catchup(self):
        """Pin: Persistent=true so a missed fire after reboot catches
        up. Without it, a reboot during drift would extend the broken
        window by the timer interval."""
        content = (_DEPLOY_DIR / "genlab-permission-auto-repair.timer").read_text()
        assert "Persistent=true" in content


class TestMarkAlertsResolvedSQL:
    """Schema pin: `mark_alerts_resolved` must write to the column that
    actually exists on `pipeline_alerts`.

    Backstory: a pre-2026-06-30 version of this script set ``resolved_note``,
    but the column on `pipeline_alerts` (per migration
    ``k1f2g3h4i5j6_adopt_content_pool_pipeline_alerts``) is
    ``auto_fix_result``. The mismatch crashed the script on every run with
    ``psycopg.errors.UndefinedColumn`` and fired a daily
    ``systemd_unit_failed`` critical alert. These pins guard against
    regression to the wrong column name.
    """

    def test_no_reference_to_nonexistent_resolved_note_column(self):
        """Pin: the literal string ``resolved_note`` must NOT appear in
        the script source. That column does not exist on `pipeline_alerts`
        and referencing it crashes the UPDATE."""
        source = _SCRIPT.read_text()
        # Tokenize check — exact column-name boundary, not substring match
        # (so prose in a docstring saying e.g. "the old `resolved_note`" in
        # an explanatory comment would still fail this pin, which is what
        # we want: zero references after the fix lands).
        assert "resolved_note" not in source, (
            "scripts/auto_repair_permissions.py must not reference "
            "`resolved_note` — the actual column on `pipeline_alerts` is "
            "`auto_fix_result`. See migration "
            "k1f2g3h4i5j6_adopt_content_pool_pipeline_alerts:93."
        )

    def test_update_statement_uses_auto_fix_result(self):
        """Pin: the UPDATE statement must target the real column,
        ``auto_fix_result``. Without this, the script writes only
        ``resolved_at`` and loses the audit trail (which run/note
        auto-resolved the alert)."""
        source = _SCRIPT.read_text()
        assert "auto_fix_result = %s" in source, (
            "UPDATE pipeline_alerts must SET auto_fix_result = %s — that's "
            "the real column for the audit note. See migration "
            "k1f2g3h4i5j6_adopt_content_pool_pipeline_alerts:93."
        )
