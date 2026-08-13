"""Pin `_emit_strategist_failure_alert`.

Prevents regression of the 2026-08-13 silent-3-week Strategist gap:
run outcomes with `status != 'persisted'` MUST reach pipeline_alerts
so the failure surfaces on Mission Control instead of dying in
journalctl.

Contract:
  * DATABASE_URL unset → no-op (returns None, no exception)
  * pg_connect throws → helper swallows (fail-open)
  * successful path issues INSERT
  * dedup on (check_name, niche_id) via existing-row SELECT
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_strategist.py"


@pytest.fixture
def strategist_module():
    spec = importlib.util.spec_from_file_location("run_strategist_mod", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_strategist_mod"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("run_strategist_mod", None)


def _outcome(status="validation_failed", err="parse err"):
    o = MagicMock()
    o.status = status
    o.week_of = date(2026, 7, 13)
    o.error = err
    o.cost_usd = 0.07
    o.duration_sec = 90.0
    return o


class TestFailOpen:
    def test_no_dsn_no_crash(self, strategist_module, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        strategist_module._emit_strategist_failure_alert("ai_creators", _outcome())

    def test_pg_connect_raises_swallowed(self, strategist_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://nowhere:1/none")
        # Actual bogus DSN → pg_connect will fail; helper must not raise
        strategist_module._emit_strategist_failure_alert("ai_creators", _outcome())


class TestInsertPath:
    def _fake_conn(self, existing_row_id=None):
        """Build a mock conn/cursor stack. When existing_row_id is set,
        the dedup SELECT returns a row (INSERT skipped)."""
        cur = MagicMock()
        cur.fetchone.return_value = (existing_row_id,) if existing_row_id else None
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=None)
        conn = MagicMock()
        conn.cursor.return_value = cur
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=None)
        return conn, cur

    def test_insert_fires_when_no_existing_alert(self, strategist_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:5432/db")
        conn, cur = self._fake_conn(existing_row_id=None)
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=conn,
        ):
            strategist_module._emit_strategist_failure_alert(
                "ai_creators", _outcome(status="llm_call_failed", err="anthropic 429"),
            )
        # Two execute calls: dedup SELECT + INSERT
        assert cur.execute.call_count == 2
        insert_call = cur.execute.call_args_list[1]
        assert "INSERT INTO pipeline_alerts" in insert_call.args[0]
        # check_name reflects the specific failure mode
        params = insert_call.args[1]
        assert params[1] == "strategist_llm_call_failed"
        assert params[0] == "ai_creators"
        assert params[2] == "warning"

    def test_dedup_prevents_duplicate_insert(self, strategist_module, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:5432/db")
        conn, cur = self._fake_conn(existing_row_id="existing-alert-uuid")
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=conn,
        ):
            strategist_module._emit_strategist_failure_alert(
                "ai_creators", _outcome(),
            )
        # Only the dedup SELECT ran; INSERT was skipped
        assert cur.execute.call_count == 1

    def test_check_name_per_status(self, strategist_module, monkeypatch):
        """Different failure statuses land distinct check_names so the
        confusion matrix on Mission Control shows the specific stage."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:5432/db")
        for status in (
            "state_collect_failed",
            "llm_call_failed",
            "validation_failed",
            "persist_failed",
        ):
            conn, cur = self._fake_conn(existing_row_id=None)
            with patch(
                "genlab_core.storage.tenant_context.pg_connect",
                return_value=conn,
            ):
                strategist_module._emit_strategist_failure_alert(
                    "ai_creators", _outcome(status=status),
                )
            insert_call = cur.execute.call_args_list[1]
            assert insert_call.args[1][1] == f"strategist_{status}"


class TestMaxOutputTokensBumped:
    """Pin the fix — MAX_OUTPUT_TOKENS must be >= 8_000 to prevent
    the 4_000-token truncation that caused the 3-week silent gap."""

    def test_max_output_tokens_at_least_8k(self):
        from genlab_core.intelligence.anthropic_client import MAX_OUTPUT_TOKENS
        assert MAX_OUTPUT_TOKENS >= 8_000, (
            f"MAX_OUTPUT_TOKENS={MAX_OUTPUT_TOKENS} is too tight. "
            "The 2026-08-13 investigation found LLM emissions of "
            "3634-3935 tokens hitting the 4_000 cap and truncating "
            "mid-JSON, causing silent validation_failed for 3+ weeks. "
            "Keep >= 8_000 as safety margin."
        )
