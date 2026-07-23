"""Pin tests for hybrid batched upsert in ``shared_ingestion._write_to_pool`` (#589).

The pre-#589 implementation issued a SAVEPOINT + RELEASE pair per row
(2 round-trips) + 1 UPSERT (1 round-trip) = **3 round-trips per row**.
At prod's 40-200 rows/day that's 120-600 round-trips daily.

The #589 refactor batches into groups of ``BATCH_SIZE=50`` and uses
``executemany`` for the bulk path — savepoint per batch (not per row).

  * Happy path (no bad rows): ~40× fewer savepoint round-trips
  * Bad row in a batch: rollback batch + reprocess row-by-row for THAT
    batch only — preserves the original error-isolation contract

These tests use a mock cursor to count SAVEPOINT / RELEASE / ROLLBACK
executions across both paths. They don't need a real DB — the wire
here is about SQL statement ordering + count, not query semantics.

Real-DB integration for the actual UPSERT SQL is covered by
``test_shared_ingestion_dedup.py`` (already runs in the ``test-storage``
CI job). This file is deliberately in-process fast unit tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_entry(content_hash: str, routed: list[str] | None = None):
    """Fake PoolEntry — only the fields ``_entry_to_params`` reads."""
    e = MagicMock()
    e.content_hash = content_hash
    e.title = f"title-{content_hash}"
    e.summary = f"summary-{content_hash}"
    e.source_url = f"https://example.com/{content_hash}"
    e.source_name = "test_source"
    e.source_platform = "youtube"
    e.video_url = f"https://youtu.be/{content_hash}"
    e.video_id = content_hash
    e.thumbnail_url = ""
    e.published_at = "2026-07-08T12:00:00Z"
    e.duration_seconds = 30
    e.view_count = 100
    e.view_velocity = 1.5
    e.source_affinity = ["ai_creators"]
    e.youtube_category_id = 28
    e.niche_scores = {"ai_creators": 0.9}
    e.routed_niches = routed if routed is not None else ["ai_creators"]
    e.routing_reason = "test"
    e.extra = {}
    return e


def _install_stub_ingestion(entries):
    """Build a SharedIngestion-shaped stub carrying the given entries.

    Uses a real class import so ``_write_to_pool`` is the actual method,
    but stubs ``_db_url`` + ``_stats`` so we don't need a real config.
    """
    from genlab_core.pipeline.shared_ingestion import SharedIngestionPipeline

    ingestion = SharedIngestionPipeline.__new__(SharedIngestionPipeline)
    ingestion._db_url = "postgresql://fake"
    ingestion._entries = entries
    ingestion._stats = {"inserted": 0, "errors": 0}
    return ingestion


def _count_calls(cur, statement_prefix: str) -> int:
    """Count ``cur.execute()`` calls whose 1st arg starts with the prefix.

    Case-insensitive; strips leading whitespace so ``"  SAVEPOINT sp_batch"``
    matches ``"SAVEPOINT"``.
    """
    return sum(
        1
        for call in cur.execute.call_args_list
        if isinstance(call.args[0], str)
        and call.args[0].lstrip().upper().startswith(statement_prefix.upper())
    )


class TestHappyPathBatching:
    """When all rows succeed the batch path runs — 1 savepoint per
    50-row batch, not per row. This is the round-trip savings that
    justified the refactor."""

    def test_50_rows_produce_one_batch_savepoint(self):
        entries = [_make_entry(f"h{i:03d}") for i in range(50)]
        ingestion = _install_stub_ingestion(entries)

        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        with patch(
            "genlab_core.pipeline.shared_ingestion.pg_connect",
            return_value=MagicMock(
                __enter__=MagicMock(return_value=mock_conn), __exit__=MagicMock()
            ),
        ):
            ingestion._write_to_pool()

        # Exactly ONE batch savepoint pair for 50 rows (not 50 pairs).
        assert _count_calls(mock_cur, "SAVEPOINT sp_batch") == 1, (
            "50 rows should use 1 batch savepoint, not 50. "
            f"Got {_count_calls(mock_cur, 'SAVEPOINT sp_batch')}."
        )
        assert _count_calls(mock_cur, "RELEASE SAVEPOINT sp_batch") == 1
        # No per-row savepoints on happy path.
        assert _count_calls(mock_cur, "SAVEPOINT sp_row") == 0
        # executemany called once with all 50 params.
        assert mock_cur.executemany.call_count == 1
        params_sent = mock_cur.executemany.call_args.args[1]
        assert len(params_sent) == 50
        assert ingestion._stats["inserted"] == 50
        assert ingestion._stats["errors"] == 0

    def test_120_rows_produce_three_batches(self):
        """120 rows / 50-per-batch = 3 batches (50, 50, 20). Verifies
        the loop's slicing math + that batch savepoints are per-batch."""
        entries = [_make_entry(f"h{i:03d}") for i in range(120)]
        ingestion = _install_stub_ingestion(entries)

        mock_cur = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        with patch(
            "genlab_core.pipeline.shared_ingestion.pg_connect",
            return_value=MagicMock(
                __enter__=MagicMock(return_value=mock_conn), __exit__=MagicMock()
            ),
        ):
            ingestion._write_to_pool()

        assert _count_calls(mock_cur, "SAVEPOINT sp_batch") == 3
        assert _count_calls(mock_cur, "RELEASE SAVEPOINT sp_batch") == 3
        assert mock_cur.executemany.call_count == 3
        # Check batch sizes: 50, 50, 20.
        sizes = [len(call.args[1]) for call in mock_cur.executemany.call_args_list]
        assert sizes == [50, 50, 20]
        assert ingestion._stats["inserted"] == 120


class TestFallbackToPerRow:
    """When executemany raises inside a batch, the code rolls back the
    batch savepoint and reprocesses that batch's rows individually with
    per-row savepoints. This preserves the pre-#589 error-isolation
    contract — a single bad row cannot block the whole run."""

    def test_batch_failure_falls_back_to_per_row(self):
        """3 rows, executemany fails, all 3 process individually and
        succeed under per-row savepoints."""
        entries = [_make_entry(f"h{i:03d}") for i in range(3)]
        ingestion = _install_stub_ingestion(entries)

        mock_cur = MagicMock()

        # First executemany call raises; every subsequent execute succeeds.
        # This models "batch failed at some row, but the individual rows
        # succeed when retried alone" — the common shape for a constraint
        # violation on ONE row that we now isolate.
        call_count = {"executemany": 0}

        def executemany_side_effect(*_args, **_kwargs):
            call_count["executemany"] += 1
            if call_count["executemany"] == 1:
                raise RuntimeError("simulated batch failure")

        mock_cur.executemany.side_effect = executemany_side_effect

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        with patch(
            "genlab_core.pipeline.shared_ingestion.pg_connect",
            return_value=MagicMock(
                __enter__=MagicMock(return_value=mock_conn), __exit__=MagicMock()
            ),
        ):
            ingestion._write_to_pool()

        # Fallback path fired: 1 batch savepoint + 1 rollback + 3 row
        # savepoints + 3 releases.
        assert _count_calls(mock_cur, "SAVEPOINT sp_batch") == 1
        assert _count_calls(mock_cur, "ROLLBACK TO SAVEPOINT sp_batch") == 1
        assert _count_calls(mock_cur, "SAVEPOINT sp_row") == 3
        assert _count_calls(mock_cur, "RELEASE SAVEPOINT sp_row") == 3
        # All 3 rows counted as inserted via the fallback per-row path.
        assert ingestion._stats["inserted"] == 3
        assert ingestion._stats["errors"] == 0

    def test_bad_row_in_batch_isolated_by_per_row_savepoint(self):
        """3 rows, executemany fails, then the 2nd row-level execute
        also fails. First + third row succeed; middle row is isolated
        as an error. This is the CRITICAL isolation contract test."""
        entries = [_make_entry(f"h{i:03d}") for i in range(3)]
        ingestion = _install_stub_ingestion(entries)

        mock_cur = MagicMock()

        # State machine: executemany raises once, then execute raises on
        # the 2nd row-level attempt only.
        state = {"em_called": 0, "row_execute_num": 0}

        def executemany_side(*_a, **_k):
            state["em_called"] += 1
            if state["em_called"] == 1:
                raise RuntimeError("batch failed on 2nd row")

        def execute_side(*a, **_k):
            sql = a[0] if a else ""
            # Track only the row-level UPSERT executes (not SAVEPOINT).
            if isinstance(sql, str) and "INSERT INTO content_pool" in sql:
                state["row_execute_num"] += 1
                if state["row_execute_num"] == 2:
                    raise RuntimeError("row-level failure on row 2")

        mock_cur.executemany.side_effect = executemany_side
        mock_cur.execute.side_effect = execute_side

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        with patch(
            "genlab_core.pipeline.shared_ingestion.pg_connect",
            return_value=MagicMock(
                __enter__=MagicMock(return_value=mock_conn), __exit__=MagicMock()
            ),
        ):
            ingestion._write_to_pool()

        # 2/3 succeeded, 1/3 errored — the row-level savepoint isolated
        # the bad row so the surrounding good rows landed cleanly.
        assert ingestion._stats["inserted"] == 2
        assert ingestion._stats["errors"] == 1
        # Verify per-row savepoints fired for each attempt:
        # 3 savepoints (start of each row) + 2 releases (rows 1 & 3) +
        # 1 rollback (row 2).
        assert _count_calls(mock_cur, "SAVEPOINT sp_row") == 3
        assert _count_calls(mock_cur, "RELEASE SAVEPOINT sp_row") == 2
        assert _count_calls(mock_cur, "ROLLBACK TO SAVEPOINT sp_row") == 1


class TestEdgeCases:
    def test_no_routed_entries_no_db_call(self):
        """When there's nothing to write, no DB connection is opened.
        Same behavior as pre-#589 — the batch loop simply doesn't fire."""
        ingestion = _install_stub_ingestion([])

        with patch("genlab_core.pipeline.shared_ingestion.pg_connect") as mock_pg_connect:
            ingestion._write_to_pool()
        mock_pg_connect.assert_not_called()

    def test_no_db_url_early_return(self):
        """No DATABASE_URL configured → early return; matches pre-#589."""
        ingestion = _install_stub_ingestion([_make_entry("h001")])
        ingestion._db_url = None

        with patch("genlab_core.pipeline.shared_ingestion.pg_connect") as mock_pg_connect:
            ingestion._write_to_pool()
        mock_pg_connect.assert_not_called()


def test_shared_ingestion_exception_writes_durable_error_file():
    """2026-07-23: on unhandled exception, shared_ingestion writes
    traceback to /opt/genlab/.runtime/shared_ingestion_last_error.txt
    BEFORE exiting. Same pattern as publisher (publish_all_platforms.py)
    and nightly-scheduler (nightly_schedule_top_per_niche.py).

    Source-grep pin — same discipline as the publisher test.
    """
    import inspect

    from genlab_core.pipeline import shared_ingestion as mod

    src = inspect.getsource(mod.main)
    assert "shared_ingestion_last_error.txt" in src, (
        "Durable error file path must remain in shared_ingestion's "
        "main() so operators can diagnose exit=3 failures after "
        "journal rotation."
    )
    assert "_tb.print_exc" in src or "traceback.print_exc" in src, (
        "traceback.print_exc must write to the durable file so the "
        "stack is preserved."
    )
    assert "also failed to write error file" in src, (
        "Nested-except guard must be present so filesystem-full or "
        "permissions-drift conditions do NOT hide the actual error."
    )
