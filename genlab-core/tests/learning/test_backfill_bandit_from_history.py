"""Pins for the historical bandit backfill script.

Covers:
  * --dry-run does NOT call record_source_outcome
  * --apply calls record_source_outcome once per eligible row with the
    arguments the live wire would have used
  * Idempotency: a second --apply against the same row set is a no-op
    (the WHERE clause filters out rows already stamped)
  * Large-batch safety: --apply with >5000 rows refuses without
    --yes-large-batch
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _fake_row(
    *,
    pa_id: str = "00000000-0000-0000-0000-000000000001",
    niche_id: str = "gaming",
    platform: str = "youtube",
    post_id: str = "vid-1",
    source: str = "youtube_trending",
    pa_likes: int = 100,
    pa_comments: int = 20,
    pa_shares: int = 10,
    pa_views: int = 5000,
    a_extra: dict | None = None,
) -> dict:
    """Build a row dict matching the SELECT shape in _build_query."""
    return {
        "pa_id": pa_id,
        "niche_id": niche_id,
        "platform": platform,
        "post_id": post_id,
        "published_at": "2026-06-25T10:00:00+00:00",
        "pa_views": pa_views,
        "pa_likes": pa_likes,
        "pa_comments": pa_comments,
        "pa_shares": pa_shares,
        "pa_saves": 5,
        "blueprint_id": "bp-" + pa_id[-1:],
        "source": source,
        "a_value": float(pa_views),
        "a_extra": a_extra or {"reach": pa_views, "engagement_rate": 0.02},
    }


# ── _row_to_metrics ──────────────────────────────────────────────────


def test_row_to_metrics_prefers_analytics_extra():
    """When analytics.extra carries engagement keys, they take
    precedence over publishing_analytics counter columns."""
    from genlab_core.scripts.backfill_bandit_from_history import _row_to_metrics

    row = _fake_row(
        pa_likes=999,  # should be overridden
        a_extra={"likes": 50, "reach": 1000, "engagement_rate": 0.1},
    )
    metrics = _row_to_metrics(row)
    assert metrics["likes"] == 50.0  # analytics.extra wins
    assert metrics["reach"] == 1000.0
    assert metrics["engagement_rate"] == 0.1


def test_row_to_metrics_falls_back_to_pa_columns():
    """When analytics.extra is empty/None, fall back to the
    publishing_analytics counter columns."""
    from genlab_core.scripts.backfill_bandit_from_history import _row_to_metrics

    row = _fake_row(pa_likes=42, pa_comments=7, a_extra=None)
    metrics = _row_to_metrics(row)
    assert metrics["likes"] == 42.0
    assert metrics["comments"] == 7.0


# ── _process_rows (the core dispatch logic) ─────────────────────────


def test_process_rows_dry_run_does_not_call_record_source_outcome():
    """--dry-run must compute rewards but NEVER call the bandit writer."""
    from genlab_core.scripts.backfill_bandit_from_history import _process_rows

    rows = [_fake_row(pa_id="r1"), _fake_row(pa_id="r2", source="youtube_search")]
    with patch(
        "genlab_core.learning.source_performance.record_source_outcome",
        return_value=True,
    ) as mock_record:
        summary = _process_rows(rows, apply=False)

    mock_record.assert_not_called()
    assert summary["total"] == 2
    assert summary["errors"] == 0
    # Per-arm reward distribution still computed
    assert ("gaming", "youtube_trending") in summary["arm_rewards"]
    assert ("gaming", "youtube_search") in summary["arm_rewards"]


def test_process_rows_apply_calls_record_source_outcome_once_per_row():
    """--apply must call record_source_outcome exactly once per
    processed row with (niche_id, source, reward) forwarded correctly."""
    from genlab_core.scripts.backfill_bandit_from_history import _process_rows

    rows = [
        _fake_row(pa_id="r1", niche_id="gaming", source="youtube_trending"),
        _fake_row(pa_id="r2", niche_id="movies", source="youtube_search"),
        _fake_row(pa_id="r3", niche_id="gaming", source="youtube_trending"),
    ]
    with patch(
        "genlab_core.learning.source_performance.record_source_outcome",
        return_value=True,
    ) as mock_record:
        summary = _process_rows(rows, apply=True)

    assert mock_record.call_count == 3
    # Inspect first call shape
    args, kwargs = mock_record.call_args_list[0]
    assert kwargs["niche_id"] == "gaming"
    assert kwargs["source"] == "youtube_trending"
    assert 0.0 <= kwargs["reward"] <= 1.0
    assert summary["total"] == 3
    assert summary["by_niche"]["gaming"] == 2
    assert summary["by_niche"]["movies"] == 1


def test_process_rows_counts_record_source_outcome_failures_as_errors():
    """If record_source_outcome returns False (DB failure), the row
    counts as an error so the summary surfaces it."""
    from genlab_core.scripts.backfill_bandit_from_history import _process_rows

    rows = [_fake_row(pa_id="r1"), _fake_row(pa_id="r2")]
    with patch(
        "genlab_core.learning.source_performance.record_source_outcome",
        return_value=False,
    ):
        summary = _process_rows(rows, apply=True)
    assert summary["errors"] == 2


# ── main() — wires up the SQL + safety gates ────────────────────────


def _patch_psycopg(rows: list[dict]) -> MagicMock:
    """Return a MagicMock for psycopg.connect that yields ``rows`` on
    fetchall(). Models the cursor.fetchall path used by main()."""
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    cursor.rowcount = len(rows)
    cursor.__enter__ = lambda self: self
    cursor.__exit__ = lambda *a: False
    conn = MagicMock()
    conn.cursor.return_value = cursor
    conn.__enter__ = lambda self: self
    conn.__exit__ = lambda *a: False
    return conn


def test_main_dry_run_default_does_not_apply(monkeypatch):
    """Calling main() with no args defaults to dry-run; no
    record_source_outcome call, no UPDATE issued."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    conn = _patch_psycopg([_fake_row()])
    with (
        patch("psycopg.connect", return_value=conn),
        patch("genlab_core.learning.source_performance.record_source_outcome") as mock_record,
    ):
        rc = mod.main([])

    assert rc == 0
    mock_record.assert_not_called()


def test_main_apply_writes_and_stamps(monkeypatch):
    """--apply mode: record_source_outcome is called AND the
    UPDATE ... extra->>'bandit_backfilled_at' SQL runs."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    rows = [_fake_row(pa_id="11111111-1111-1111-1111-111111111111")]
    conn = _patch_psycopg(rows)

    with (
        patch("psycopg.connect", return_value=conn),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        rc = mod.main(["--apply"])

    assert rc == 0
    assert mock_record.call_count == 1
    conn.commit.assert_called_once()  # UPDATE committed
    # Verify the UPDATE SQL ran — the conn.cursor was called multiple
    # times (once for SELECT, once for UPDATE)
    assert conn.cursor.call_count >= 2


def test_main_idempotency_second_run_finds_no_rows(monkeypatch):
    """The WHERE clause's `(extra->>'bandit_backfilled_at') IS NULL`
    filter means a second run after --apply returns zero candidate
    rows. We simulate this by returning [] from the SELECT."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    conn = _patch_psycopg([])  # SELECT returns nothing on 2nd run
    with (
        patch("psycopg.connect", return_value=conn),
        patch("genlab_core.learning.source_performance.record_source_outcome") as mock_record,
    ):
        rc = mod.main(["--apply"])

    assert rc == 0
    mock_record.assert_not_called()
    conn.commit.assert_not_called()  # nothing to commit


def test_main_refuses_large_batch_without_force(monkeypatch):
    """--apply with >MAX_APPLY_WITHOUT_FORCE rows returns rc=2 +
    no record_source_outcome calls — safety gate fires before
    any state mutation."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    big = [
        _fake_row(pa_id=f"{i:08d}-0000-0000-0000-000000000000")
        for i in range(mod.MAX_APPLY_WITHOUT_FORCE + 1)
    ]
    conn = _patch_psycopg(big)
    with (
        patch("psycopg.connect", return_value=conn),
        patch("genlab_core.learning.source_performance.record_source_outcome") as mock_record,
    ):
        rc = mod.main(["--apply"])

    assert rc == 2
    mock_record.assert_not_called()


def test_main_large_batch_with_explicit_flag_proceeds(monkeypatch):
    """--apply --yes-large-batch overrides the safety gate."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    big = [
        _fake_row(pa_id=f"{i:08d}-0000-0000-0000-000000000000")
        for i in range(mod.MAX_APPLY_WITHOUT_FORCE + 5)
    ]
    conn = _patch_psycopg(big)
    with (
        patch("psycopg.connect", return_value=conn),
        patch(
            "genlab_core.learning.source_performance.record_source_outcome",
            return_value=True,
        ) as mock_record,
    ):
        rc = mod.main(["--apply", "--yes-large-batch"])

    assert rc == 0
    assert mock_record.call_count == len(big)


def test_main_no_database_url_returns_error(monkeypatch):
    """Missing DSN: main() returns rc=1 without crashing."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from genlab_core.scripts import backfill_bandit_from_history as mod

    rc = mod.main([])
    assert rc == 1


def test_main_niche_filter_threads_through(monkeypatch):
    """--niche-id <id> adds the niche_id parameter to the SQL.
    We assert it via the cursor.execute call args."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    conn = _patch_psycopg([])
    with patch("psycopg.connect", return_value=conn):
        mod.main(["--niche-id", "movies"])

    # First cursor was the SELECT; inspect its execute call
    cur = conn.cursor.return_value
    cur.execute.assert_called_once()
    sql, params = cur.execute.call_args.args
    assert "pa.niche_id = %s" in sql
    assert "movies" in params


def test_main_since_flag_threads_through(monkeypatch):
    """--since YYYY-MM-DD becomes the first SQL parameter."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://test/test")
    from genlab_core.scripts import backfill_bandit_from_history as mod

    conn = _patch_psycopg([])
    with patch("psycopg.connect", return_value=conn):
        mod.main(["--since", "2026-06-25"])

    cur = conn.cursor.return_value
    sql, params = cur.execute.call_args.args
    assert params[0] == "2026-06-25"
