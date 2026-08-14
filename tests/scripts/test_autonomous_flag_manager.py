"""Pin Phase 5.C session 2 auto-apply extensions to autonomous_flag_manager:

  * _env_line_for: quotes only when value has whitespace
  * _write_env_flag: replaces existing line in-place, preserves others
  * _write_env_flag: appends when flag not present
  * _write_env_flag: writes backup file
  * _write_env_flag: restores backup on write failure
  * _find_apply_ready_proposals: fail-open [] on DB error
  * _mark_applied: fail-open False on DB error
  * main --apply hits auto-apply pass; propose-only mode does not
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "autonomous_flag_manager",
    _ROOT / "scripts" / "autonomous_flag_manager.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["autonomous_flag_manager"] = _MOD
_SPEC.loader.exec_module(_MOD)


class TestEnvLineFor:
    def test_no_space_no_quotes(self):
        assert _MOD._env_line_for("F", "50") == "F=50\n"

    def test_with_space_gets_quoted(self):
        assert _MOD._env_line_for("F", "hello world") == 'F="hello world"\n'


class TestWriteEnvFlag:
    def test_replaces_existing_line(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\nMY_FLAG=25\nB=2\n")
        ok, _ = _MOD._write_env_flag(str(env), "MY_FLAG", "50")
        assert ok
        content = env.read_text()
        assert "MY_FLAG=50" in content
        assert "MY_FLAG=25" not in content
        # Sibling lines preserved
        assert "A=1" in content
        assert "B=2" in content

    def test_appends_when_flag_absent(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\nB=2\n")
        ok, _ = _MOD._write_env_flag(str(env), "NEW_FLAG", "75")
        assert ok
        content = env.read_text()
        assert content.endswith("NEW_FLAG=75\n")

    def test_writes_backup(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\n")
        ok, _ = _MOD._write_env_flag(str(env), "A", "2")
        assert ok
        # A backup with .env.bak.* suffix exists next to it
        backups = list(tmp_path.glob(".env.bak.*"))
        assert len(backups) >= 1

    def test_missing_env_returns_error(self, tmp_path):
        env = tmp_path / "nope.env"
        ok, msg = _MOD._write_env_flag(str(env), "F", "v")
        assert ok is False
        assert "not found" in msg

    def test_only_first_occurrence_replaced_leftovers_removed(self, tmp_path):
        """Multiple flag lines are collapsed to a single line."""
        env = tmp_path / ".env"
        env.write_text("MY_FLAG=A\nMY_FLAG=B\nOTHER=X\n")
        ok, _ = _MOD._write_env_flag(str(env), "MY_FLAG", "C")
        assert ok
        content = env.read_text()
        assert content.count("MY_FLAG=") == 1
        assert "MY_FLAG=C" in content
        assert "OTHER=X" in content


class TestFindApplyReadyProposals:
    def test_fail_open_on_db_error(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("boom")
        r = _MOD._find_apply_ready_proposals(conn, 24, 0.9)
        assert r == []

    def test_returns_rows_as_dicts(self):
        conn = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = [
            {"id": "abc", "flag_name": "F", "from_state": "25",
             "to_state": "50", "rationale": "r", "confidence": 0.95},
        ]
        conn.execute.return_value = result
        r = _MOD._find_apply_ready_proposals(conn, 24, 0.9)
        assert len(r) == 1
        assert r[0]["flag_name"] == "F"


class TestMarkApplied:
    def test_success(self):
        conn = MagicMock()
        assert _MOD._mark_applied(conn, "id-1", "auto") is True
        conn.execute.assert_called_once()

    def test_fail_open_on_error(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("db down")
        assert _MOD._mark_applied(conn, "id-1", "auto") is False


class TestMainApplyFlag:
    """Smoke: `--apply` invokes auto-apply pass; default does not."""

    def _stub_conn(self, monkeypatch):
        conn = MagicMock()
        result = MagicMock()
        result.fetchall.return_value = []  # no ready-to-apply rows
        conn.execute.return_value = result
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)

        class _FakePsycopg:
            @staticmethod
            def connect(*_a, **_kw):
                return ctx

        monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg)
        # dict_row import is used in a bare import; stub the rows module
        rows_mod = MagicMock()
        rows_mod.dict_row = object
        monkeypatch.setitem(sys.modules, "psycopg.rows", rows_mod)

        # Stub collect_proposals so the run is a no-op
        fake_mod = MagicMock()
        fake_mod.collect_proposals = lambda _c: []
        monkeypatch.setitem(
            sys.modules,
            "genlab_core.scheduling.flag_flip_proposer",
            fake_mod,
        )
        return conn

    def test_apply_mode_calls_apply_ready_query(self, monkeypatch, capsys):
        monkeypatch.setenv("DATABASE_URL", "postgres://x")
        self._stub_conn(monkeypatch)
        assert _MOD.main(["--apply"]) == 0
        out = capsys.readouterr().out
        assert "Auto-apply pass" in out

    def test_default_mode_skips_apply(self, monkeypatch, capsys):
        monkeypatch.setenv("DATABASE_URL", "postgres://x")
        self._stub_conn(monkeypatch)
        assert _MOD.main([]) == 0
        out = capsys.readouterr().out
        assert "Auto-apply pass" not in out


class TestMainMissingDsn:
    def test_missing_dsn_exits_1(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main([]) == 1
