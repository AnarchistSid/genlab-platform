"""Pin Phase 5.D operator briefing runner:

  * main exits 0 without DATABASE_URL (rule #26)
  * dry-run skips DB persist + email send
  * --no-email persists row but skips send
  * Missing GENLAB_OPERATOR_EMAIL persists with email_error set
  * SendError caught; row persisted with error string
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "generate_operator_briefing",
    _ROOT / "scripts" / "generate_operator_briefing.py",
)
_MOD = importlib.util.module_from_spec(_SPEC)
sys.modules["generate_operator_briefing"] = _MOD
_SPEC.loader.exec_module(_MOD)


def _stub_generate_result():
    """A minimal successful BriefingResult stand-in."""
    return SimpleNamespace(
        summary_md="- line1\n- line2",
        structured={"k": "v"},
        llm_cost_usd=0.002,
        n_pending_flag_flips=0,
        n_pending_strategist_proposals=0,
    )


def _wire_psycopg(monkeypatch, insert_capture=None):
    conn = MagicMock()
    if insert_capture is not None:
        conn.execute.side_effect = (
            lambda sql, params=(): insert_capture.append((sql, params))
            or MagicMock()
        )
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=conn)
    ctx.__exit__ = MagicMock(return_value=False)

    class _FakePsycopg:
        @staticmethod
        def connect(*_a, **_kw):
            return ctx

    monkeypatch.setitem(sys.modules, "psycopg", _FakePsycopg)
    rows_mod = MagicMock()
    rows_mod.dict_row = object
    monkeypatch.setitem(sys.modules, "psycopg.rows", rows_mod)

    briefing_mod = MagicMock()
    briefing_mod.generate = lambda _conn: _stub_generate_result()
    monkeypatch.setitem(
        sys.modules,
        "genlab_core.intelligence.operator_briefing",
        briefing_mod,
    )
    return conn


class TestMain:
    def test_no_dsn_still_exits_0(self, monkeypatch):
        """Rule #26: data-side problem, not systemd problem."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert _MOD.main([]) == 0

    def test_dry_run_skips_persist_and_send(self, monkeypatch, capsys):
        monkeypatch.setenv("DATABASE_URL", "postgres://x")
        monkeypatch.setenv("GENLAB_OPERATOR_EMAIL", "op@example.com")
        capture = []
        _wire_psycopg(monkeypatch, insert_capture=capture)
        assert _MOD.main(["--dry-run"]) == 0
        # No INSERT — the dry-run path returns before persist.
        assert capture == []

    def test_no_email_flag_persists_but_skips_send(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://x")
        monkeypatch.setenv("GENLAB_OPERATOR_EMAIL", "op@example.com")
        capture = []
        _wire_psycopg(monkeypatch, insert_capture=capture)
        # Patch _send_email so we can prove it wasn't called
        called = {"n": 0}
        monkeypatch.setattr(
            _MOD, "_send_email",
            lambda *a, **k: (called.__setitem__("n", called["n"] + 1) or (True, None)),
        )
        assert _MOD.main(["--no-email"]) == 0
        assert called["n"] == 0

    def test_missing_operator_email_persists_with_error(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://x")
        monkeypatch.delenv("GENLAB_OPERATOR_EMAIL", raising=False)
        capture = []
        _wire_psycopg(monkeypatch, insert_capture=capture)
        assert _MOD.main([]) == 0
        # An INSERT must have fired
        assert any("INSERT INTO operator_briefings" in sql for sql, _ in capture)
        # And email_error param should carry the unset diagnostic
        insert_call = next(
            (params for sql, params in capture
             if "INSERT INTO operator_briefings" in sql), None,
        )
        assert insert_call is not None
        # Params: summary_md, structured, email_sent, recipient,
        # email_error, cost, flips, strat
        assert insert_call[2] is False  # email_sent
        assert insert_call[4] == "GENLAB_OPERATOR_EMAIL unset"

    def test_send_error_still_persists(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://x")
        monkeypatch.setenv("GENLAB_OPERATOR_EMAIL", "op@example.com")
        capture = []
        _wire_psycopg(monkeypatch, insert_capture=capture)
        # Force _send_email to report failure
        monkeypatch.setattr(
            _MOD, "_send_email",
            lambda *a, **k: (False, "AUTH_FAILED: token bad"),
        )
        assert _MOD.main([]) == 0
        insert_call = next(
            (params for sql, params in capture
             if "INSERT INTO operator_briefings" in sql), None,
        )
        assert insert_call is not None
        assert insert_call[2] is False  # email_sent
        assert insert_call[4] == "AUTH_FAILED: token bad"


class TestSendEmailHelper:
    def test_ok_result_returns_true_none(self, monkeypatch):
        fake_sender = MagicMock()
        fake_sender.send.return_value = SimpleNamespace(ok=True, reason=None)
        fake_module = MagicMock()
        fake_module.OutlookMailSender = lambda: fake_sender
        fake_module.SendError = Exception
        monkeypatch.setitem(
            sys.modules,
            "genlab_core.integrations.outlook_sender",
            fake_module,
        )
        sent, err = _MOD._send_email("s", "b", "to@x")
        assert sent is True
        assert err is None

    def test_bad_result_returns_false_reason(self, monkeypatch):
        fake_sender = MagicMock()
        fake_sender.send.return_value = SimpleNamespace(
            ok=False, reason="RATE_LIMITED",
        )
        fake_module = MagicMock()
        fake_module.OutlookMailSender = lambda: fake_sender
        fake_module.SendError = Exception
        monkeypatch.setitem(
            sys.modules,
            "genlab_core.integrations.outlook_sender",
            fake_module,
        )
        sent, err = _MOD._send_email("s", "b", "to@x")
        assert sent is False
        assert err == "RATE_LIMITED"

    def test_arbitrary_exception_caught(self, monkeypatch):
        """A non-SendError exception (e.g. RuntimeError from
        constructor) is still caught by the broad except so the
        runner never crashes."""
        fake_module = MagicMock()

        def _factory():
            raise RuntimeError("oh no")

        # Use a distinct SendError class so `except SendError` in
        # the runner doesn't accidentally match RuntimeError.
        class _RealSendError(Exception):
            def __init__(self, reason, detail=""):
                super().__init__(f"{reason}: {detail}")
                self.reason = reason
                self.detail = detail

        fake_module.OutlookMailSender = _factory
        fake_module.SendError = _RealSendError
        monkeypatch.setitem(
            sys.modules,
            "genlab_core.integrations.outlook_sender",
            fake_module,
        )
        sent, err = _MOD._send_email("s", "b", "to@x")
        assert sent is False
        assert err is not None
        assert "oh no" in err
