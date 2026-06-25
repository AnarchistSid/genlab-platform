"""Per-niche compliance-event aggregation — PR after #581.

Pins for ``compliance.events.stats_by_niche``:

  * Empty dict when DSN missing (fail-OPEN)
  * Empty dict when SQL raises (fail-OPEN)
  * window_days clamped to [1, 90] both directions
  * Counts roll up: warn/block/allow per (niche, event_type)
  * top_event_type derived from MAX(warn+block) — allows excluded
  * top_event_count reflects the warn+block sum
  * by_event_type contains the full breakdown for downstream UIs
  * Niches with zero events are NOT in the response (caller fills)
  * Row factory tolerance: tuple AND dict-row both parsed
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _fake_conn(rows):
    """Build a MagicMock matching the events._connect contract."""
    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = rows
    fake_conn.execute.return_value = fake_cursor
    return fake_conn


# ── fail-OPEN paths ───────────────────────────────────────────────


def test_stats_returns_empty_dict_when_no_dsn(monkeypatch):
    """fail-OPEN: dashboard renders zero-state, never crashes."""
    from genlab_core.compliance import events

    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert events.stats_by_niche() == {}


def test_stats_returns_empty_dict_when_sql_raises(monkeypatch, caplog):
    import logging

    from genlab_core.compliance import events

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.execute.side_effect = RuntimeError("simulated SQL error")

    with patch.object(events, "_connect", return_value=fake_conn):
        with caplog.at_level(logging.WARNING):
            result = events.stats_by_niche()

    assert result == {}
    assert any("stats_by_niche failed" in r.getMessage() for r in caplog.records)


# ── input clamping ────────────────────────────────────────────────


def test_window_days_clamped_to_max_90(monkeypatch):
    """window_days=9999 → 90 (defence against runaway scans)."""
    from genlab_core.compliance import events

    fake_conn = _fake_conn(rows=[])
    with patch.object(events, "_connect", return_value=fake_conn):
        events.stats_by_niche(window_days=9999)

    # The SQL parameter list should be [90], not [9999]
    call_args = fake_conn.execute.call_args
    params = call_args.args[1]
    assert params == (90,)


def test_window_days_clamped_to_min_1(monkeypatch):
    """window_days=0 → 1 (the minimum meaningful window)."""
    from genlab_core.compliance import events

    fake_conn = _fake_conn(rows=[])
    with patch.object(events, "_connect", return_value=fake_conn):
        events.stats_by_niche(window_days=0)

    params = fake_conn.execute.call_args.args[1]
    assert params == (1,)


# ── aggregation correctness ──────────────────────────────────────


def test_counts_roll_up_per_niche(monkeypatch):
    """Multiple rows for the same niche → totals sum correctly."""
    from genlab_core.compliance import events

    # 3 warns + 1 block + 2 allows on gaming, all across the same
    # event_type 'spam_pattern_detected'
    rows = [
        ("gaming", "spam_pattern_detected", "warn", 3),
        ("gaming", "spam_pattern_detected", "block", 1),
        ("gaming", "spam_pattern_detected", "allow", 2),
    ]

    fake_conn = _fake_conn(rows=rows)
    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.stats_by_niche(window_days=7)

    assert "gaming" in result
    gaming = result["gaming"]
    assert gaming["total"] == 6
    assert gaming["warns"] == 3
    assert gaming["blocks"] == 1
    assert gaming["allows"] == 2


def test_top_event_type_picks_max_attention_event(monkeypatch):
    """top_event_type = argmax(warn + block), allows excluded.

    This is the operator-actionability signal: 'what should I look
    at first?' is a function of warns + blocks, not allows.
    """
    from genlab_core.compliance import events

    rows = [
        # spam_pattern: 1 warn + 1 block = 2 attention
        ("gaming", "spam_pattern_detected", "warn", 1),
        ("gaming", "spam_pattern_detected", "block", 1),
        # copyright: 5 allows (no attention)
        ("gaming", "copyright_flag", "allow", 5),
        # account_health: 3 warns = 3 attention ← winner
        ("gaming", "account_health_warning", "warn", 3),
    ]

    fake_conn = _fake_conn(rows=rows)
    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.stats_by_niche(window_days=7)

    assert result["gaming"]["top_event_type"] == "account_health_warning"
    assert result["gaming"]["top_event_count"] == 3


def test_top_event_type_is_none_when_only_allows(monkeypatch):
    """All 'allow' rows → top_event_type=None (nothing actionable)."""
    from genlab_core.compliance import events

    rows = [
        ("anime", "pre_publish_check", "allow", 100),
        ("anime", "copyright_flag", "allow", 50),
    ]

    fake_conn = _fake_conn(rows=rows)
    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.stats_by_niche(window_days=7)

    assert result["anime"]["top_event_type"] is None
    assert result["anime"]["top_event_count"] == 0
    # Allows still counted for visibility
    assert result["anime"]["allows"] == 150


def test_by_event_type_breakdown_present(monkeypatch):
    """The full per-event-type breakdown should be present for
    downstream UIs (e.g. a future drill-down popover)."""
    from genlab_core.compliance import events

    rows = [
        ("movies", "spam_pattern_detected", "warn", 4),
        ("movies", "copyright_flag", "block", 1),
    ]

    fake_conn = _fake_conn(rows=rows)
    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.stats_by_niche(window_days=7)

    by_et = result["movies"]["by_event_type"]
    assert by_et["spam_pattern_detected"]["warn"] == 4
    assert by_et["spam_pattern_detected"]["block"] == 0
    assert by_et["spam_pattern_detected"]["allow"] == 0
    assert by_et["copyright_flag"]["block"] == 1


def test_niches_with_zero_events_not_in_response(monkeypatch):
    """A quiet niche with no rows in the window should NOT appear
    in the dict — caller fills the gap with zero-state rendering."""
    from genlab_core.compliance import events

    rows = [
        ("gaming", "spam_pattern_detected", "warn", 1),
    ]

    fake_conn = _fake_conn(rows=rows)
    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.stats_by_niche(window_days=7)

    # gaming present; anime, movies, sports, ai_creators absent
    assert "gaming" in result
    for absent in ("anime", "movies", "sports", "ai_creators"):
        assert absent not in result


# ── row-factory tolerance ────────────────────────────────────────


def test_dict_row_factory_also_parsed(monkeypatch):
    """compliance.events._connect doesn't pin dict_row, but if a
    future refactor switches the factory the aggregation still works."""
    from genlab_core.compliance import events

    rows = [
        {"niche_id": "sports", "event_type": "account_health_warning", "decision": "warn", "n": 7},
    ]

    fake_conn = _fake_conn(rows=rows)
    with patch.object(events, "_connect", return_value=fake_conn):
        result = events.stats_by_niche(window_days=7)

    assert result["sports"]["warns"] == 7
    assert result["sports"]["top_event_type"] == "account_health_warning"
