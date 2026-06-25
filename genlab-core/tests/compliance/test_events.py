"""PR #566 (2026-06-25) — compliance_events durable log helpers.

Pins:

  * ComplianceDecision frozen dataclass + decision validation
  * VALID_EVENT_TYPES + VALID_DECISIONS closed enums
  * log_compliance_event fail-OPEN (audit-log loss never blocks)
  * Input validation rejects bad types/decisions with WARNING
  * Migration shape (table + RLS + indexes)
"""

from __future__ import annotations

import dataclasses
import uuid
from unittest.mock import MagicMock, patch

import pytest

# ── ComplianceDecision dataclass ───────────────────────────────────


def test_decision_dataclass_is_frozen():
    """Mutations raise — same safety rationale as the Tenant + User
    DTOs from PRs #557 and #559."""
    from genlab_core.compliance.events import ComplianceDecision

    d = ComplianceDecision(decision="allow")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.decision = "block"  # type: ignore[misc]


def test_decision_validates_decision_at_construct_time():
    from genlab_core.compliance.events import ComplianceDecision

    with pytest.raises(ValueError, match="decision"):
        ComplianceDecision(decision="not_a_valid_value")  # type: ignore[arg-type]


def test_decision_accepts_all_three_valid_decisions():
    """Pin all 3 valid decisions construct cleanly. Future
    refactors that drop one (e.g. removing 'warn') will fail here."""
    from genlab_core.compliance.events import VALID_DECISIONS, ComplianceDecision

    assert VALID_DECISIONS == frozenset({"allow", "block", "warn"})
    for d in VALID_DECISIONS:
        decision = ComplianceDecision(decision=d)
        assert decision.decision == d


def test_decision_reasons_and_metadata_default_empty():
    from genlab_core.compliance.events import ComplianceDecision

    d = ComplianceDecision(decision="allow")
    assert d.reasons == []
    assert d.metadata == {}


# ── VALID_EVENT_TYPES ──────────────────────────────────────────────


def test_valid_event_types_documents_phase_a_set():
    """Pin the event types documented in the migration. PR #567-#570
    must update this set if they add new event types — surfacing
    the change in tests means reviewers see new audit-log shapes
    deliberately."""
    from genlab_core.compliance.events import VALID_EVENT_TYPES

    # Phase A (PRs #566-#570) event types
    phase_a_required = {
        "pre_publish_check",  # #566 — policy_gate aggregate
        "ai_disclosure_added",  # #567
        "copyright_flag",  # #568
        "spam_pattern_detected",  # #569
        "account_health_warning",  # #570
        "auto_publish_block",  # #576 (Phase B)
        "manual_override",  # operator escape hatch
    }
    assert phase_a_required.issubset(VALID_EVENT_TYPES)


# ── log_compliance_event fail-open ────────────────────────────────


def test_log_no_dsn_returns_false_no_raise(monkeypatch):
    """Fail-OPEN: audit-log loss never blocks. No DSN → False, no raise."""
    from genlab_core.compliance import events

    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = events.log_compliance_event(
        niche_id="gaming",
        event_type="pre_publish_check",
        decision="allow",
    )
    assert result is False


def test_log_db_exception_returns_false_no_raise(monkeypatch):
    """DB write raises → False, no propagation. Compliance logging
    is observability, not enforcement."""
    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB blowup")

    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.log_compliance_event(
            niche_id="gaming",
            event_type="pre_publish_check",
            decision="allow",
        )
    assert result is False


# ── input validation (typo guards) ─────────────────────────────────


def test_log_empty_niche_id_rejected(caplog):
    """Empty niche_id is caller misuse — log WARNING + return False.
    Don't silently write rows with NULL/blank niche (would orphan
    them from RLS scope)."""
    import logging

    from genlab_core.compliance import events

    with caplog.at_level(logging.WARNING):
        result = events.log_compliance_event(
            niche_id="",
            event_type="pre_publish_check",
            decision="allow",
        )
    assert result is False
    assert any("empty niche_id" in r.message for r in caplog.records)


def test_log_unknown_event_type_rejected(caplog):
    """Typo guard: unknown event_type → False + WARNING. Don't
    let a typo create new pseudo-types that fragment the audit log."""
    import logging

    from genlab_core.compliance import events

    with caplog.at_level(logging.WARNING):
        result = events.log_compliance_event(
            niche_id="gaming",
            event_type="prepublishchekc",  # typo
            decision="allow",
        )
    assert result is False
    assert any("unknown event_type" in r.message for r in caplog.records)


def test_log_unknown_decision_rejected(caplog):
    import logging

    from genlab_core.compliance import events

    with caplog.at_level(logging.WARNING):
        result = events.log_compliance_event(
            niche_id="gaming",
            event_type="pre_publish_check",
            decision="approve",  # not in VALID_DECISIONS
        )
    assert result is False
    assert any("unknown decision" in r.message for r in caplog.records)


# ── happy path ─────────────────────────────────────────────────────


def test_log_happy_path_writes_row(monkeypatch):
    """Valid inputs → INSERT runs with expected params + True."""
    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    bp_id = uuid.uuid4()
    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.log_compliance_event(
            niche_id="gaming",
            event_type="pre_publish_check",
            decision="warn",
            blueprint_id=bp_id,
            platform="instagram",
            reasons=["test_reason"],
            metadata={"k": "v"},
        )
    assert result is True
    # Pin the SQL shape (INSERT with the 7 documented columns)
    sql = str(fake_conn.execute.call_args.args[0])
    assert "INSERT INTO compliance_events" in sql
    assert "niche_id, blueprint_id, platform, event_type" in sql
    # Bound params shape — niche_id, blueprint_id-as-str, platform,
    # event_type, decision, reasons_json, metadata_json
    bound = fake_conn.execute.call_args.args[1]
    assert bound[0] == "gaming"
    assert bound[1] == str(bp_id)
    assert bound[2] == "instagram"
    assert bound[3] == "pre_publish_check"
    assert bound[4] == "warn"


def test_log_nullable_fields_default_to_none(monkeypatch):
    """blueprint_id + platform are nullable per migration; passing
    None should bind NULL, not crash."""
    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.log_compliance_event(
            niche_id="gaming",
            event_type="pre_publish_check",
            decision="allow",
        )
    assert result is True
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] is None  # blueprint_id slot
    assert bound[2] is None  # platform slot


def test_log_empty_reasons_and_metadata_serialize_correctly(monkeypatch):
    """JSON encoding of empty list/dict should not produce 'null'
    (would violate NOT NULL on both columns)."""
    import json

    from genlab_core.compliance import events

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(events, "_connect", return_value=fake_conn):
        events.log_compliance_event(
            niche_id="gaming",
            event_type="pre_publish_check",
            decision="allow",
        )
    bound = fake_conn.execute.call_args.args[1]
    # reasons + metadata slots — bound[5] and bound[6]
    assert json.loads(bound[5]) == []
    assert json.loads(bound[6]) == {}


# ── migration shape pins ───────────────────────────────────────────


def test_migration_file_exists_and_has_rls():
    """Pin migration file shape so future schema changes can't
    drop RLS or the standard indexes without surfacing in tests."""
    from pathlib import Path

    mig = (
        Path(__file__).resolve().parent.parent.parent
        / "migrations"
        / "versions"
        / "e1f2g3h4i5j6_compliance_events.py"
    )
    assert mig.exists(), f"missing migration at {mig}"
    src = mig.read_text()
    # Table shape
    assert "CREATE TABLE IF NOT EXISTS compliance_events" in src
    assert "id UUID PRIMARY KEY" in src
    assert "niche_id TEXT NOT NULL" in src
    assert "decision TEXT NOT NULL" in src
    # Indexes — all 3 documented in migration docstring
    assert "idx_compliance_niche_ts" in src
    assert "idx_compliance_event_type" in src
    assert "idx_compliance_blueprint" in src
    # RLS policy — matches the 16-table standard from PR #535 SR-B
    assert "ENABLE ROW LEVEL SECURITY" in src
    assert "current_setting('app.niche_id'" in src
    # Admin escape preserved
    assert "IN ('', 'all')" in src
