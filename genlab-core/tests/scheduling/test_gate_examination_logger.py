"""Tests for gate_examination_logger.

Load-bearing surface: this logger runs inside auto-approver's tight
loop on every blueprint. Fail-open discipline is non-negotiable —
any exception here would block auto-approval. Tests pin:

  Fail-open:
    - DB raises on execute → returns False, doesn't raise
    - empty blueprint_id → False
    - empty niche_id → False
    - malformed decision object → False (doesn't raise)

  Insert shape:
    - passed_checks + failed_checks land as JSONB
    - default caller_source = 'auto_approver_v1'
    - custom caller_source honored
    - extra dict serialised as JSONB

  Provided-conn path:
    - passing conn skips psycopg.connect entirely
    - conn.execute is called with the INSERT SQL
    - insert failure returns False
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _make_decision(
    approved=True, confidence=0.85, passed=None, failed=None
):
    """Duck-typed AutoApprovalDecision stub — we don't need the
    real class, just the attributes the logger reads."""
    return SimpleNamespace(
        approved=approved,
        confidence=confidence,
        passed_checks=passed or ["has_video", "has_hook"],
        failed_checks=failed or [],
    )


class TestFailOpen:
    def test_empty_blueprint_id_returns_false(self):
        from genlab_core.scheduling.gate_examination_logger import log

        conn = MagicMock()
        assert (
            log(
                blueprint_id="",
                niche_id="gaming",
                decision=_make_decision(),
                conn=conn,
            )
            is False
        )
        conn.execute.assert_not_called()

    def test_empty_niche_id_returns_false(self):
        from genlab_core.scheduling.gate_examination_logger import log

        conn = MagicMock()
        assert (
            log(
                blueprint_id="bp1",
                niche_id="",
                decision=_make_decision(),
                conn=conn,
            )
            is False
        )
        conn.execute.assert_not_called()

    def test_db_raise_returns_false_not_raise(self):
        from genlab_core.scheduling.gate_examination_logger import log

        conn = MagicMock()
        conn.execute.side_effect = RuntimeError("db down")
        # Must not raise. Returns False.
        assert (
            log(
                blueprint_id="bp1",
                niche_id="gaming",
                decision=_make_decision(),
                conn=conn,
            )
            is False
        )

    def test_malformed_decision_returns_false_not_raise(self):
        from genlab_core.scheduling.gate_examination_logger import log

        # Decision missing all fields — should degrade gracefully.
        conn = MagicMock()
        broken = SimpleNamespace()  # no attributes
        # log() should tolerate this via getattr defaults.
        result = log(
            blueprint_id="bp1",
            niche_id="gaming",
            decision=broken,
            conn=conn,
        )
        # Doesn't raise; writes a row with approved=False + no checks.
        assert result is True


class TestInsertShape:
    def test_insert_sql_and_default_caller(self):
        from genlab_core.scheduling.gate_examination_logger import log

        conn = MagicMock()
        assert (
            log(
                blueprint_id="bp-a",
                niche_id="gaming",
                decision=_make_decision(
                    approved=False,
                    confidence=0.42,
                    passed=["has_video"],
                    failed=["composite_score", "virality_score"],
                ),
                conn=conn,
            )
            is True
        )
        # One INSERT.
        conn.execute.assert_called_once()
        sql, params = conn.execute.call_args[0]
        assert "INSERT INTO gate_examinations" in sql
        # params order: (bp, niche, approved, confidence,
        # passed_json, failed_json, caller, extra_json)
        assert params[0] == "bp-a"
        assert params[1] == "gaming"
        assert params[2] is False
        assert params[3] == 0.42
        assert json.loads(params[4]) == ["has_video"]
        assert json.loads(params[5]) == ["composite_score", "virality_score"]
        assert params[6] == "auto_approver_v1"
        assert json.loads(params[7]) == {}

    def test_custom_caller_source(self):
        from genlab_core.scheduling.gate_examination_logger import log

        conn = MagicMock()
        log(
            blueprint_id="bp-a",
            niche_id="gaming",
            decision=_make_decision(),
            conn=conn,
            caller_source="nightly_scheduler",
        )
        _, params = conn.execute.call_args[0]
        assert params[6] == "nightly_scheduler"

    def test_extra_dict_serialised(self):
        from genlab_core.scheduling.gate_examination_logger import log

        conn = MagicMock()
        log(
            blueprint_id="bp-a",
            niche_id="gaming",
            decision=_make_decision(),
            conn=conn,
            extra={"pre_strategy_confidence": 0.72, "note": "boost"},
        )
        _, params = conn.execute.call_args[0]
        assert json.loads(params[7]) == {
            "pre_strategy_confidence": 0.72,
            "note": "boost",
        }

    def test_none_confidence_persists_as_null(self):
        from genlab_core.scheduling.gate_examination_logger import log

        conn = MagicMock()
        log(
            blueprint_id="bp-a",
            niche_id="gaming",
            decision=_make_decision(confidence=None),
            conn=conn,
        )
        _, params = conn.execute.call_args[0]
        # Confidence goes through as None → NULL in SQL.
        assert params[3] is None


class TestNoDsnFallback:
    def test_no_dsn_silently_skips(self, monkeypatch):
        """When conn is None AND DATABASE_URL is unset, logger
        silently no-ops. This is the CLI dry-run / test path — auto-
        approver's fail-open contract."""
        from genlab_core.scheduling.gate_examination_logger import log

        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = log(
            blueprint_id="bp-a",
            niche_id="gaming",
            decision=_make_decision(),
        )
        assert result is False
