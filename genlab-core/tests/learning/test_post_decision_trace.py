"""Pins for the per-post decision trace UPSERT helpers.

Covers:
  * record_bandit_pick INSERTs with bandit fields
  * record_operator_decision UPDATEs gate/operator fields without
    clobbering bandit fields (ON CONFLICT scope)
  * record_engagement_window UPDATEs engagement/reward without
    clobbering bandit + operator fields
  * Non-UUID blueprint_ids are skipped (SharePoint legacy compat)
  * All 3 wire-points fail-OPEN on DSN missing
  * UPSERT semantics: 3 calls produce ONE row, enriched across events

The "no duplicate rows" pin is enforced via the ON CONFLICT clause
in SQL — we test that the SQL string contains the right ON CONFLICT
+ that the wire-points use the same conflict target.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _patch_conn() -> MagicMock:
    """Standard mocked psycopg connection that records execute calls."""
    conn = MagicMock()
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False
    return conn


# ── _coerce_uuid ─────────────────────────────────────────────────────


def test_coerce_uuid_passes_valid_uuid():
    """A valid UUID string is returned as canonical form."""
    from genlab_core.learning.post_decision_trace import _coerce_uuid

    assert (
        _coerce_uuid("11111111-2222-3333-4444-555555555555")
        == "11111111-2222-3333-4444-555555555555"
    )


def test_coerce_uuid_derives_uuid5_from_non_uuid():
    """2026-07-14 fix: non-UUID inputs (SHA256 candidate_id,
    SharePoint record ids) now derive a stable UUID5 from the
    genlab namespace. Empty inputs still return None. Prior
    behavior returned None for everything non-UUID, causing
    post_decision_trace to stay at 0 rows for months."""
    from genlab_core.learning.post_decision_trace import _coerce_uuid
    import uuid

    # SharePoint-style id → derived UUID (was None pre-fix)
    result = _coerce_uuid("sp-1234-5678")
    assert result is not None
    uuid.UUID(result)  # valid UUID string

    # SHA256 candidate_id → derived UUID (was None pre-fix)
    sha = "e70eef4332c83f8c06f7fd616ed9852ad42bcb5fe00cc81ccef2d04a118631ee"
    result = _coerce_uuid(sha)
    assert result is not None
    uuid.UUID(result)

    # Empty inputs still return None
    assert _coerce_uuid("") is None
    assert _coerce_uuid(None) is None


# ── record_bandit_pick ──────────────────────────────────────────────


def test_record_bandit_pick_writes_insert(monkeypatch):
    """Happy path: ONE INSERT with the bandit fields + ON CONFLICT clause."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    conn = _patch_conn()

    from genlab_core.learning.post_decision_trace import record_bandit_pick

    with patch("genlab_core.learning.post_decision_trace._connect", return_value=conn):
        ok = record_bandit_pick(
            blueprint_id="11111111-2222-3333-4444-555555555555",
            niche_id="gaming",
            bandit_arm_id="source:youtube_trending",
            bandit_predicted_score=0.65,
            bandit_confidence=0.42,
        )

    assert ok is True
    conn.execute.assert_called_once()
    sql, params = conn.execute.call_args.args
    assert "INSERT INTO post_decision_trace" in sql
    assert "ON CONFLICT (blueprint_id)" in sql
    assert "bandit_arm_id = EXCLUDED.bandit_arm_id" in sql
    # Crucial: must NOT clobber engagement/gate fields in the UPSERT
    assert "gate_verdict" not in sql.split("DO UPDATE")[1]
    assert "engagement_reach" not in sql.split("DO UPDATE")[1]
    assert params == (
        "11111111-2222-3333-4444-555555555555",
        "gaming",
        "source:youtube_trending",
        0.65,
        0.42,
    )


def test_record_bandit_pick_accepts_non_uuid_via_uuid5(monkeypatch):
    """2026-07-14: non-UUID inputs (SharePoint ids, SHA256 candidate_ids)
    now derive a UUID5 and proceed with the write. Prior behavior
    skipped silently → 0-rows table for months."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    conn = _patch_conn()
    from genlab_core.learning.post_decision_trace import record_bandit_pick

    with patch(
        "genlab_core.learning.post_decision_trace._connect", return_value=conn
    ):
        ok = record_bandit_pick(
            blueprint_id="sp-1234-5678",  # not a UUID; now converted via uuid5
            niche_id="gaming",
            bandit_arm_id="source:youtube_trending",
        )
    # 2026-07-14 fix: write now succeeds (was False pre-fix)
    assert ok is True
    conn.execute.assert_called_once()


def test_record_bandit_pick_fails_open_on_missing_dsn(monkeypatch):
    """No DSN → returns False, never raises."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from genlab_core.learning.post_decision_trace import record_bandit_pick

    ok = record_bandit_pick(
        blueprint_id="11111111-2222-3333-4444-555555555555",
        niche_id="gaming",
        bandit_arm_id="source:youtube_trending",
    )
    assert ok is False


def test_record_bandit_pick_skips_empty_args(monkeypatch):
    """Required args missing → returns False, no connect attempt."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.learning.post_decision_trace import record_bandit_pick

    with patch("genlab_core.learning.post_decision_trace._connect") as mock_connect:
        assert (
            record_bandit_pick(
                blueprint_id="11111111-2222-3333-4444-555555555555",
                niche_id="",
                bandit_arm_id="arm",
            )
            is False
        )
        assert (
            record_bandit_pick(
                blueprint_id="11111111-2222-3333-4444-555555555555",
                niche_id="gaming",
                bandit_arm_id="",
            )
            is False
        )
    mock_connect.assert_not_called()


# ── record_operator_decision ────────────────────────────────────────


def test_record_operator_decision_writes_upsert(monkeypatch):
    """Operator decision wire: UPSERT updates gate + operator fields."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")

    conn = _patch_conn()
    from genlab_core.learning.post_decision_trace import record_operator_decision

    with patch("genlab_core.learning.post_decision_trace._connect", return_value=conn):
        ok = record_operator_decision(
            blueprint_id="11111111-2222-3333-4444-555555555555",
            niche_id="gaming",
            gate_verdict=True,
            gate_confidence=0.85,
            operator_action="approved",
        )

    assert ok is True
    sql, params = conn.execute.call_args.args
    assert "ON CONFLICT (blueprint_id)" in sql
    update_clause = sql.split("DO UPDATE")[1]
    assert "gate_verdict = EXCLUDED.gate_verdict" in update_clause
    assert "operator_action = EXCLUDED.operator_action" in update_clause
    # Must NOT touch bandit or engagement fields — those belong to
    # other wire-points
    assert "bandit_arm_id" not in update_clause
    assert "engagement_reach" not in update_clause


def test_record_operator_decision_accepts_null_gate(monkeypatch):
    """When auto_approval_gate.evaluate raised, decision is None — the
    helper must still record the operator's action so AUTO #2 readiness
    has the operator's side of the pair."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    conn = _patch_conn()
    from genlab_core.learning.post_decision_trace import record_operator_decision

    with patch("genlab_core.learning.post_decision_trace._connect", return_value=conn):
        ok = record_operator_decision(
            blueprint_id="11111111-2222-3333-4444-555555555555",
            niche_id="gaming",
            gate_verdict=None,
            gate_confidence=None,
            operator_action="rejected",
        )
    assert ok is True
    _sql, params = conn.execute.call_args.args
    assert params[2] is None  # gate_verdict
    assert params[3] is None  # gate_confidence
    assert params[4] == "rejected"


# ── record_engagement_window ────────────────────────────────────────


def test_record_engagement_window_24h_only(monkeypatch):
    """24h window: sets engagement_reach_24h but leaves 168h NULL +
    leaves computed_reward NULL (only 48h window populates that)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    conn = _patch_conn()
    from genlab_core.learning.post_decision_trace import record_engagement_window

    with patch("genlab_core.learning.post_decision_trace._connect", return_value=conn):
        ok = record_engagement_window(
            blueprint_id="11111111-2222-3333-4444-555555555555",
            niche_id="gaming",
            published_at="2026-06-30T10:00:00+00:00",
            engagement_reach_24h=1500,
            engagement_reach_168h=None,
            engagement_likes=42,
            computed_reward=None,
        )
    assert ok is True
    sql, params = conn.execute.call_args.args
    # COALESCE preserves prior values on UPDATE
    assert "COALESCE(" in sql
    assert params[3] == 1500  # engagement_reach_24h
    assert params[4] is None  # engagement_reach_168h
    assert params[6] is None  # computed_reward


def test_record_engagement_window_168h_with_reward(monkeypatch):
    """168h window with computed_reward set (from preceding 48h)."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    conn = _patch_conn()
    from genlab_core.learning.post_decision_trace import record_engagement_window

    with patch("genlab_core.learning.post_decision_trace._connect", return_value=conn):
        ok = record_engagement_window(
            blueprint_id="11111111-2222-3333-4444-555555555555",
            niche_id="gaming",
            engagement_reach_168h=5000,
            computed_reward=0.72,
        )
    assert ok is True
    _sql, params = conn.execute.call_args.args
    assert params[4] == 5000  # engagement_reach_168h
    assert params[6] == 0.72  # computed_reward


def test_record_engagement_window_fails_open_on_missing_dsn(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from genlab_core.learning.post_decision_trace import record_engagement_window

    ok = record_engagement_window(
        blueprint_id="11111111-2222-3333-4444-555555555555",
        niche_id="gaming",
        engagement_reach_24h=100,
    )
    assert ok is False


def test_record_engagement_window_accepts_non_uuid_via_uuid5(monkeypatch):
    """2026-07-14: non-UUID inputs now derive UUID5 + write succeeds."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    conn = _patch_conn()
    from genlab_core.learning.post_decision_trace import record_engagement_window

    with patch(
        "genlab_core.learning.post_decision_trace._connect", return_value=conn
    ):
        ok = record_engagement_window(
            blueprint_id="not-a-uuid",
            niche_id="gaming",
            engagement_reach_24h=100,
        )
    assert ok is True
    conn.execute.assert_called_once()


# ── UPSERT no-duplicate semantics — assert all 3 wire-points share
#    the same ON CONFLICT (blueprint_id) clause so multiple calls
#    produce ONE row, enriched ─────────────────────────────────────


def test_all_three_wire_points_use_blueprint_id_conflict(monkeypatch):
    """The three writers MUST share the same conflict target so a
    blueprint going through push → operator review → engagement
    windows ends up as ONE row, not 4."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    conn = _patch_conn()
    from genlab_core.learning.post_decision_trace import (
        record_bandit_pick,
        record_engagement_window,
        record_operator_decision,
    )

    with patch("genlab_core.learning.post_decision_trace._connect", return_value=conn):
        record_bandit_pick(
            blueprint_id="11111111-2222-3333-4444-555555555555",
            niche_id="gaming",
            bandit_arm_id="arm",
        )
        record_operator_decision(
            blueprint_id="11111111-2222-3333-4444-555555555555",
            niche_id="gaming",
            gate_verdict=True,
            gate_confidence=0.9,
            operator_action="approved",
        )
        record_engagement_window(
            blueprint_id="11111111-2222-3333-4444-555555555555",
            niche_id="gaming",
            engagement_reach_168h=1000,
            computed_reward=0.65,
        )

    assert conn.execute.call_count == 3
    for call in conn.execute.call_args_list:
        sql = call.args[0]
        assert "ON CONFLICT (blueprint_id)" in sql, (
            "Wire-points must share conflict target to avoid duplicate rows"
        )
