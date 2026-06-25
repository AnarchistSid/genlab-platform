"""Source-discovery proposer logic — PR after #586.

Pins for ``source_discovery.proposer.propose_source_channels``:

  * Empty list when niche_id is empty (defensive)
  * Empty list when DSN missing (fail-OPEN)
  * Empty list when SQL raises (fail-OPEN)
  * window_days clamped to [1, 180] both directions
  * min_clips clamped to >= 1
  * top_n clamped to [1, 100]
  * Successful query returns ChannelSuggestion list
  * ChannelSuggestion is frozen (defensive immutability)
  * sample_blueprints capped at 5
  * Row factory tolerance: tuple AND dict-row both parsed

Pins for the SQL contract:

  * WHERE includes b.source_channel_id IS NOT NULL guard
  * HAVING enforces min_clips at the SQL layer
  * ORDER BY uses avg_engagement DESC + clips_used DESC tiebreak
  * niche_id is parameterised (SQL injection guard)
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


def _fake_conn(rows):
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = rows
    fake_conn.execute.return_value = fake_cursor
    return fake_conn


# ── ChannelSuggestion dataclass ───────────────────────────────────


def test_channel_suggestion_is_frozen():
    """Mutation raises — same safety rationale as the other DTOs
    shipped this session."""
    from genlab_core.source_discovery import ChannelSuggestion

    s = ChannelSuggestion(
        source_channel_id="UC123",
        clips_used=3,
        total_engagement=1500.0,
        avg_engagement=500.0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.clips_used = 99  # type: ignore[misc]


# ── fail-OPEN paths ───────────────────────────────────────────────


def test_proposer_returns_empty_when_niche_id_empty():
    """Defensive: empty niche_id never even hits the DB."""
    from genlab_core.source_discovery import propose_source_channels

    assert propose_source_channels("") == []


def test_proposer_returns_empty_when_no_dsn(monkeypatch):
    from genlab_core.source_discovery import propose_source_channels

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert propose_source_channels("gaming") == []


def test_proposer_returns_empty_when_sql_raises(monkeypatch, caplog):
    import logging

    from genlab_core.source_discovery import proposer

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated SQL error")

    with patch.object(proposer, "_connect", return_value=fake_conn):
        with caplog.at_level(logging.WARNING):
            result = proposer.propose_source_channels("gaming")

    assert result == []
    assert any("propose_source_channels" in r.getMessage() for r in caplog.records)


# ── input clamping ────────────────────────────────────────────────


def test_window_days_clamped_to_180_max():
    from genlab_core.source_discovery import proposer

    fake_conn = _fake_conn([])
    with patch.object(proposer, "_connect", return_value=fake_conn):
        proposer.propose_source_channels("gaming", window_days=9999)

    # 2nd parameterised arg is window_days
    params = fake_conn.execute.call_args.args[1]
    assert params[1] == 180


def test_window_days_clamped_to_1_min():
    from genlab_core.source_discovery import proposer

    fake_conn = _fake_conn([])
    with patch.object(proposer, "_connect", return_value=fake_conn):
        proposer.propose_source_channels("gaming", window_days=0)

    params = fake_conn.execute.call_args.args[1]
    assert params[1] == 1


def test_min_clips_clamped_to_1_min():
    from genlab_core.source_discovery import proposer

    fake_conn = _fake_conn([])
    with patch.object(proposer, "_connect", return_value=fake_conn):
        proposer.propose_source_channels("gaming", min_clips=0)

    params = fake_conn.execute.call_args.args[1]
    assert params[2] == 1


def test_top_n_clamped_to_100_max():
    from genlab_core.source_discovery import proposer

    fake_conn = _fake_conn([])
    with patch.object(proposer, "_connect", return_value=fake_conn):
        proposer.propose_source_channels("gaming", top_n=9999)

    params = fake_conn.execute.call_args.args[1]
    assert params[3] == 100


# ── parsing correctness ──────────────────────────────────────────


def test_proposer_parses_dict_row_results():
    from genlab_core.source_discovery import propose_source_channels

    rows = [
        {
            "source_channel_id": "UCabc",
            "clips_used": 5,
            "total_engagement": 25000.0,
            "avg_engagement": 5000.0,
            "last_used_at": datetime(2026, 6, 25),
            "sample_candidates": ["cand1", "cand2"],
        },
        {
            "source_channel_id": "UCdef",
            "clips_used": 2,
            "total_engagement": 8000.0,
            "avg_engagement": 4000.0,
            "last_used_at": datetime(2026, 6, 20),
            "sample_candidates": ["cand3"],
        },
    ]
    with patch(
        "genlab_core.source_discovery.proposer._connect",
        return_value=_fake_conn(rows),
    ):
        result = propose_source_channels("gaming")

    assert len(result) == 2
    assert result[0].source_channel_id == "UCabc"
    assert result[0].clips_used == 5
    assert result[0].avg_engagement == 5000.0
    assert result[1].source_channel_id == "UCdef"


def test_proposer_parses_tuple_row_results():
    """Row factory tolerance: tuple shape (default psycopg) also parses."""
    from genlab_core.source_discovery import propose_source_channels

    # tuple order: source_channel_id, clips_used, total_engagement,
    # avg_engagement, last_used_at, sample_candidates
    rows = [("UCabc", 3, 9000.0, 3000.0, None, ["cand1"])]
    with patch(
        "genlab_core.source_discovery.proposer._connect",
        return_value=_fake_conn(rows),
    ):
        result = propose_source_channels("gaming")

    assert len(result) == 1
    assert result[0].source_channel_id == "UCabc"
    assert result[0].avg_engagement == 3000.0


def test_proposer_caps_sample_blueprints_at_5():
    """Long sample lists get capped — UI tooltips don't need 100 IDs."""
    from genlab_core.source_discovery import propose_source_channels

    rows = [
        {
            "source_channel_id": "UCabc",
            "clips_used": 50,
            "total_engagement": 50000.0,
            "avg_engagement": 1000.0,
            "last_used_at": None,
            "sample_candidates": [f"cand{i}" for i in range(50)],
        },
    ]
    with patch(
        "genlab_core.source_discovery.proposer._connect",
        return_value=_fake_conn(rows),
    ):
        result = propose_source_channels("gaming")

    assert len(result[0].sample_blueprints) == 5


def test_proposer_skips_null_channel_ids_defensively():
    """Even though SQL filters IS NOT NULL, the parser tolerates
    a NULL row coming back (defence against future SQL drift)."""
    from genlab_core.source_discovery import propose_source_channels

    rows = [
        {
            "source_channel_id": None,
            "clips_used": 3,
            "total_engagement": 100.0,
            "avg_engagement": 33.3,
            "last_used_at": None,
            "sample_candidates": [],
        },
        {
            "source_channel_id": "UCabc",
            "clips_used": 3,
            "total_engagement": 100.0,
            "avg_engagement": 33.3,
            "last_used_at": None,
            "sample_candidates": [],
        },
    ]
    with patch(
        "genlab_core.source_discovery.proposer._connect",
        return_value=_fake_conn(rows),
    ):
        result = propose_source_channels("gaming")

    # Only the non-null row should be returned
    assert len(result) == 1
    assert result[0].source_channel_id == "UCabc"


# ── SQL contract pins ────────────────────────────────────────────


def test_sql_includes_source_channel_id_not_null_guard():
    """SAFETY PIN: query MUST exclude NULL channel_ids. Dropping
    this would surface legacy rows with no source data as a 'null'
    proposal — confusing operator UX."""
    from genlab_core.source_discovery import proposer

    fake_conn = _fake_conn([])
    with patch.object(proposer, "_connect", return_value=fake_conn):
        proposer.propose_source_channels("gaming")

    sql = fake_conn.execute.call_args.args[0]
    import re

    normalized = re.sub(r"\s+", " ", sql).strip()
    assert "b.source_channel_id IS NOT NULL" in normalized


def test_sql_enforces_min_clips_via_having():
    """The min_clips filter MUST be at the SQL layer (HAVING) so
    the wire payload stays small. Dropping HAVING and filtering
    in Python would scale poorly as channels accumulate."""
    from genlab_core.source_discovery import proposer

    fake_conn = _fake_conn([])
    with patch.object(proposer, "_connect", return_value=fake_conn):
        proposer.propose_source_channels("gaming", min_clips=5)

    sql = fake_conn.execute.call_args.args[0]
    import re

    normalized = re.sub(r"\s+", " ", sql).strip()
    assert "HAVING COUNT(DISTINCT b.id) >= %s" in normalized


def test_sql_sort_order_avg_then_clips_tiebreak():
    """ORDER BY pinned: per-clip avg primary, sample-size tiebreak."""
    from genlab_core.source_discovery import proposer

    fake_conn = _fake_conn([])
    with patch.object(proposer, "_connect", return_value=fake_conn):
        proposer.propose_source_channels("gaming")

    sql = fake_conn.execute.call_args.args[0]
    import re

    normalized = re.sub(r"\s+", " ", sql).strip()
    assert "ORDER BY avg_engagement DESC, clips_used DESC" in normalized


def test_niche_id_is_parameterised_not_concatenated():
    """SAFETY PIN: niche_id flows through %s placeholders only.
    String concatenation would open SQL injection — operator input
    eventually reaches this function via the endpoint layer."""
    from genlab_core.source_discovery import proposer

    fake_conn = _fake_conn([])
    malicious = "gaming'; DROP TABLE blueprints; --"
    with patch.object(proposer, "_connect", return_value=fake_conn):
        proposer.propose_source_channels(malicious)

    sql = fake_conn.execute.call_args.args[0]
    params = fake_conn.execute.call_args.args[1]
    # niche_id must NOT appear in the SQL string
    assert malicious not in sql
    # It must appear in the params tuple
    assert malicious in params
