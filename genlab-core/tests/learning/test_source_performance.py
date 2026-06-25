"""PR #571 (2026-06-25) — per-source bandit foundation.

Pins:

  * SourcePerformance frozen dataclass
  * arm_id convention: 'source:<source_name>'
  * record_source_outcome — Beta posterior update, clamping, UPSERT
  * get_source_performance — None on cold-start + DB error
  * list_source_performance — DESC by reward_mean, source: prefix filter
  * fail-OPEN at every external boundary (auth-path discipline)
"""

from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

# ── SourcePerformance dataclass ────────────────────────────────────


def test_source_performance_frozen():
    from genlab_core.learning.source_performance import SourcePerformance

    sp = SourcePerformance(
        niche_id="gaming",
        source="youtube_trending",
        alpha=10.0,
        beta=5.0,
        n_plays=15,
        reward_mean=10.0 / 15.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sp.alpha = 99.0  # type: ignore[misc]


# ── record_source_outcome ──────────────────────────────────────────


def test_record_outcome_no_dsn_returns_false(monkeypatch):
    """Fail-OPEN: missing DSN → False, no raise."""
    from genlab_core.learning import source_performance

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert source_performance.record_source_outcome("gaming", "youtube_trending", 0.5) is False


def test_record_outcome_empty_inputs_return_false():
    from genlab_core.learning.source_performance import record_source_outcome

    assert record_source_outcome("", "youtube_trending", 0.5) is False
    assert record_source_outcome("gaming", "", 0.5) is False


def test_record_outcome_clamps_reward_above_one(monkeypatch, caplog):
    """Reward > 1 → clamped to 1 + WARNING. Don't let a caller bug
    poison the bandit posterior."""
    import logging

    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        with caplog.at_level(logging.WARNING):
            source_performance.record_source_outcome("gaming", "youtube_trending", 1.5)
    # The bound reward should be 1.0 (clamped)
    bound = fake_conn.execute.call_args.args[1]
    # Bound params: (niche, arm_id, reward, 1-reward, n_plays, reward, 1-reward, n_plays)
    assert bound[2] == 1.0
    assert bound[3] == 0.0
    assert any("above 1" in r.getMessage() for r in caplog.records)


def test_record_outcome_clamps_reward_below_zero(monkeypatch, caplog):
    import logging

    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        with caplog.at_level(logging.WARNING):
            source_performance.record_source_outcome("gaming", "youtube_trending", -0.3)
    bound = fake_conn.execute.call_args.args[1]
    assert bound[2] == 0.0
    assert bound[3] == 1.0
    assert any("below 0" in r.getMessage() for r in caplog.records)


def test_record_outcome_arm_id_convention(monkeypatch):
    """Pin the 'source:' prefix on arm_id — distinguishes per-source
    arms from existing hook_style:, hour:, etc patterns."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.record_source_outcome("gaming", "youtube_trending", 0.5)
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == "source:youtube_trending"


def test_record_outcome_sql_uses_on_conflict_upsert(monkeypatch):
    """Pin the SQL contract: INSERT ... ON CONFLICT DO UPDATE — atomic
    upsert that handles both first-write (priors 1,1 + delta) and
    subsequent-write (existing row + delta) without a separate
    SELECT-then-UPDATE roundtrip."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.record_source_outcome("gaming", "youtube_trending", 0.7)
    sql = str(fake_conn.execute.call_args.args[0])
    assert "INSERT INTO bandit_arms" in sql
    assert "ON CONFLICT (niche_id, arm_id) DO UPDATE" in sql
    # Priors (1.0, 1.0) for the new-arm branch
    assert "1.0 + %s" in sql


def test_record_outcome_returns_true_on_success(monkeypatch):
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        result = source_performance.record_source_outcome("gaming", "youtube_trending", 0.5)
    assert result is True


def test_record_outcome_db_exception_returns_false(monkeypatch):
    """Fail-OPEN: write exception → False, no raise. Missed update is
    better than crashing the reward loop."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB error")

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        assert source_performance.record_source_outcome("gaming", "youtube_trending", 0.5) is False


def test_record_outcome_negative_n_plays_delta_clamped(monkeypatch):
    """Defensive: negative n_plays_delta clamped to 0 (n_plays is a
    monotonic counter)."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.record_source_outcome(
            "gaming", "youtube_trending", 0.5, n_plays_delta=-5
        )
    bound = fake_conn.execute.call_args.args[1]
    # n_plays slot (position 4 in the bound tuple) should be 0
    assert bound[4] == 0


# ── get_source_performance ─────────────────────────────────────────


def test_get_no_dsn_returns_none(monkeypatch):
    from genlab_core.learning import source_performance

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert source_performance.get_source_performance("gaming", "youtube_trending") is None


def test_get_empty_inputs_return_none():
    from genlab_core.learning.source_performance import get_source_performance

    assert get_source_performance("", "youtube_trending") is None
    assert get_source_performance("gaming", "") is None


def test_get_no_row_returns_none(monkeypatch):
    """Cold-start: never seen (niche, source) pair → None. Caller
    falls back to default policy."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = None
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        assert source_performance.get_source_performance("gaming", "new_source") is None


def test_get_returns_dto_with_reward_mean(monkeypatch):
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = {
        "niche_id": "gaming",
        "arm_id": "source:youtube_trending",
        "alpha": 8.0,
        "beta": 2.0,
        "n_plays": 10,
    }
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        sp = source_performance.get_source_performance("gaming", "youtube_trending")
    assert sp is not None
    # source name strips the 'source:' prefix
    assert sp.source == "youtube_trending"
    assert sp.alpha == 8.0
    assert sp.beta == 2.0
    assert sp.n_plays == 10
    assert sp.reward_mean == 0.8  # 8 / (8+2)


# ── list_source_performance ────────────────────────────────────────


def test_list_no_dsn_returns_empty(monkeypatch):
    from genlab_core.learning import source_performance

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert source_performance.list_source_performance("gaming") == []


def test_list_empty_niche_returns_empty_no_db_call():
    """Defensive short-circuit: empty niche → [], no DB roundtrip."""
    from genlab_core.learning import source_performance

    with patch.object(source_performance, "_connect") as mock_conn:
        assert source_performance.list_source_performance("") == []
    mock_conn.assert_not_called()


def test_list_filters_by_source_prefix(monkeypatch):
    """Pin SQL contract: WHERE arm_id LIKE 'source:%' — other arm
    types (hook_style:, hour:) are excluded."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = []
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.list_source_performance("gaming")
    sql = str(fake_conn.execute.call_args.args[0])
    assert "arm_id LIKE %s" in sql
    bound = fake_conn.execute.call_args.args[1]
    assert bound[1] == "source:%"  # the LIKE pattern


def test_list_orders_by_reward_mean_desc(monkeypatch):
    """Pin DESC ordering — dashboard takes top N directly without
    re-sorting; 'worst sources' view reverses the list."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = []
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        source_performance.list_source_performance("gaming")
    sql = str(fake_conn.execute.call_args.args[0])
    assert "DESC" in sql


def test_list_returns_dto_list(monkeypatch):
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        {
            "niche_id": "gaming",
            "arm_id": "source:youtube_trending",
            "alpha": 8.0,
            "beta": 2.0,
            "n_plays": 10,
        },
        {
            "niche_id": "gaming",
            "arm_id": "source:twitch_trending",
            "alpha": 3.0,
            "beta": 7.0,
            "n_plays": 10,
        },
    ]
    fake_conn.execute.return_value = fake_cursor

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        results = source_performance.list_source_performance("gaming")
    assert len(results) == 2
    assert results[0].source == "youtube_trending"
    assert results[0].reward_mean == 0.8
    assert results[1].source == "twitch_trending"
    assert results[1].reward_mean == 0.3


def test_list_db_exception_returns_empty(monkeypatch):
    """Fail-OPEN: query exception → [], no raise."""
    from genlab_core.learning import source_performance

    monkeypatch.setenv("DATABASE_URL", "postgresql://test")
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated DB error")

    with patch.object(source_performance, "_connect", return_value=fake_conn):
        assert source_performance.list_source_performance("gaming") == []
