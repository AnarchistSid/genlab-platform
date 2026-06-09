"""Tests for :mod:`genlab_core.monetization.proxy_revenue_aggregator`.

Audit ref: R-32.

These tests mock psycopg.connect end-to-end — the aggregator's whole
contract is SQL semantics (DELETE prior proxy rows then INSERT one per
niche+network bucket) so a real Postgres isn't needed to verify it.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.monetization import affiliate_economics, proxy_revenue_aggregator


@pytest.fixture(autouse=True)
def _stable_economics(monkeypatch):
    """Pin economics to a known shape so the math is predictable."""
    affiliate_economics.reset_cache()
    fixed = affiliate_economics.AffiliateEconomics(
        avg_order_inr={"gaming": 10000.0, "sports": 5000.0},
        default_avg_order_inr=10000.0,
        commission_pct={"amazon": 0.05, "earnkaro": 0.10},
        default_commission_pct=0.05,
        conversion_rate=0.02,
        default_currency="INR",
    )
    monkeypatch.setattr(affiliate_economics, "get_affiliate_economics", lambda: fixed)
    monkeypatch.setattr(
        proxy_revenue_aggregator,
        "get_affiliate_economics",
        lambda: fixed,
    )
    yield fixed
    affiliate_economics.reset_cache()


def _make_mock_conn(click_rows: list[dict]) -> tuple[MagicMock, MagicMock]:
    """Build a psycopg connection mock that returns ``click_rows`` from
    the first SELECT and records every execute call on the cursor.

    Returns (mock_conn_ctxmgr, mock_cursor) — the context-manager wrapper
    is what ``psycopg.connect`` returns; the cursor is where assertions
    happen.
    """
    cursor = MagicMock()
    cursor.fetchall.return_value = click_rows
    cursor.rowcount = 0  # DELETE rowcount; overridden per-test if needed

    conn = MagicMock()
    conn.cursor.return_value = cursor
    # transaction() context manager → no-op.
    conn.transaction.return_value.__enter__.return_value = None
    conn.transaction.return_value.__exit__.return_value = False
    # psycopg.connect(...) returns a context-manager.
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn, cursor


# ---------------------------------------------------------------------------
# aggregate_proxy_revenue — happy path
# ---------------------------------------------------------------------------


class TestAggregateProxyRevenue:
    def test_returns_counts_and_total(self) -> None:
        click_rows = [
            {"niche_id": "gaming", "network": "amazon", "clicks": 100},
            {"niche_id": "sports", "network": "earnkaro", "clicks": 50},
        ]
        conn, cursor = _make_mock_conn(click_rows)
        cursor.rowcount = 3  # prior proxy rows for the date

        with patch("psycopg.connect", return_value=conn):
            result = proxy_revenue_aggregator.aggregate_proxy_revenue(date(2026, 6, 8))

        assert result["report_date"] == "2026-06-08"
        assert result["rows_deleted"] == 3
        assert result["rows_inserted"] == 2
        # gaming: 100 × 0.02 × 10000 × 0.05 = 1000.0
        # sports: 50 × 0.02 × 5000 × 0.10 = 500.0
        assert result["total_revenue_inr"] == 1500.0

    def test_deletes_prior_proxy_then_inserts_one_per_bucket(self) -> None:
        click_rows = [
            {"niche_id": "gaming", "network": "amazon", "clicks": 100},
            {"niche_id": "sports", "network": "earnkaro", "clicks": 50},
        ]
        conn, cursor = _make_mock_conn(click_rows)

        with patch("psycopg.connect", return_value=conn):
            proxy_revenue_aggregator.aggregate_proxy_revenue(date(2026, 6, 8))

        sql_calls = [c[0][0] for c in cursor.execute.call_args_list]
        # Expected order: SELECT clicks, DELETE prior, INSERT × 2.
        assert "SELECT" in sql_calls[0]
        assert "affiliate_clicks" in sql_calls[0]
        assert "DELETE FROM affiliate_revenue" in sql_calls[1]
        assert sql_calls[1].count("source") >= 1  # filtered on extra->>'source'
        for sql in sql_calls[2:]:
            assert sql.strip().startswith("INSERT INTO affiliate_revenue")
        assert len(sql_calls) == 4  # SELECT + DELETE + 2 × INSERT

    def test_handles_zero_clicks_day_gracefully(self) -> None:
        conn, cursor = _make_mock_conn([])
        cursor.rowcount = 0

        with patch("psycopg.connect", return_value=conn):
            result = proxy_revenue_aggregator.aggregate_proxy_revenue(date(2026, 6, 8))

        assert result["rows_deleted"] == 0
        assert result["rows_inserted"] == 0
        assert result["total_revenue_inr"] == 0.0
        # DELETE still runs (so a previously-populated day gets cleared
        # if zero clicks come in).
        sql_calls = [c[0][0] for c in cursor.execute.call_args_list]
        assert any("DELETE FROM affiliate_revenue" in s for s in sql_calls)

    def test_null_niche_or_network_replaced_with_unknown(self) -> None:
        # affiliate_clicks rows occasionally have NULL niche_id or
        # network (the column is nullable in the schema). The
        # aggregator must not crash + must persist the row under
        # 'unknown'.
        click_rows = [
            {"niche_id": None, "network": None, "clicks": 10},
        ]
        conn, cursor = _make_mock_conn(click_rows)

        with patch("psycopg.connect", return_value=conn):
            result = proxy_revenue_aggregator.aggregate_proxy_revenue(date(2026, 6, 8))

        # Inserted under defaults: 10 × 0.02 × 10000 × 0.05 = 100.0
        assert result["rows_inserted"] == 1
        # Find the INSERT call and check the niche / network params.
        insert_calls = [
            c for c in cursor.execute.call_args_list if "INSERT INTO affiliate_revenue" in c[0][0]
        ]
        assert insert_calls
        params = insert_calls[0][0][1]
        assert params[0] == "unknown"  # niche
        assert params[1] == "unknown"  # network

    def test_extra_payload_marks_source_as_proxy(self) -> None:
        click_rows = [{"niche_id": "gaming", "network": "amazon", "clicks": 10}]
        conn, cursor = _make_mock_conn(click_rows)

        with patch("psycopg.connect", return_value=conn):
            proxy_revenue_aggregator.aggregate_proxy_revenue(date(2026, 6, 8))

        insert_call = next(
            c for c in cursor.execute.call_args_list if "INSERT INTO affiliate_revenue" in c[0][0]
        )
        params = insert_call[0][1]
        extra = json.loads(params[7])  # 8th param = extra jsonb string
        assert extra["source"] == proxy_revenue_aggregator.PROXY_SOURCE
        assert "computed_at" in extra

    def test_uses_default_currency_from_economics(self) -> None:
        click_rows = [{"niche_id": "gaming", "network": "amazon", "clicks": 1}]
        conn, cursor = _make_mock_conn(click_rows)

        with patch("psycopg.connect", return_value=conn):
            proxy_revenue_aggregator.aggregate_proxy_revenue(date(2026, 6, 8))

        insert_call = next(
            c for c in cursor.execute.call_args_list if "INSERT INTO affiliate_revenue" in c[0][0]
        )
        params = insert_call[0][1]
        assert params[3] == "INR"  # currency column


# ---------------------------------------------------------------------------
# aggregate_window — backfill loop
# ---------------------------------------------------------------------------


class TestAggregateWindow:
    def test_runs_one_pass_per_day_inclusive(self) -> None:
        conn, cursor = _make_mock_conn([])

        with patch("psycopg.connect", return_value=conn):
            results = proxy_revenue_aggregator.aggregate_window(
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 3),
            )

        assert len(results) == 3
        assert [r["report_date"] for r in results] == [
            "2026-06-01",
            "2026-06-02",
            "2026-06-03",
        ]

    def test_single_day_window(self) -> None:
        conn, cursor = _make_mock_conn([])
        with patch("psycopg.connect", return_value=conn):
            results = proxy_revenue_aggregator.aggregate_window(
                start_date=date(2026, 6, 1),
                end_date=date(2026, 6, 1),
            )
        assert len(results) == 1
        assert results[0]["report_date"] == "2026-06-01"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_default_date_is_yesterday(self) -> None:
        conn, _ = _make_mock_conn([])
        with (
            patch("psycopg.connect", return_value=conn),
            patch(
                "genlab_core.monetization.proxy_revenue_aggregator.aggregate_proxy_revenue"
            ) as mock_agg,
        ):
            mock_agg.return_value = {
                "report_date": "2026-06-08",
                "rows_deleted": 0,
                "rows_inserted": 0,
                "total_revenue_inr": 0.0,
            }
            exit_code = proxy_revenue_aggregator.main([])

        assert exit_code == 0
        # The single positional arg should be a date (yesterday).
        called_date = mock_agg.call_args[0][0]
        assert isinstance(called_date, date)

    def test_explicit_date_parsed_correctly(self) -> None:
        with patch(
            "genlab_core.monetization.proxy_revenue_aggregator.aggregate_proxy_revenue"
        ) as mock_agg:
            mock_agg.return_value = {
                "report_date": "2026-05-15",
                "rows_deleted": 0,
                "rows_inserted": 5,
                "total_revenue_inr": 100.0,
            }
            exit_code = proxy_revenue_aggregator.main(["--date", "2026-05-15"])

        assert exit_code == 0
        assert mock_agg.call_args[0][0] == date(2026, 5, 15)

    def test_days_triggers_window(self) -> None:
        with patch(
            "genlab_core.monetization.proxy_revenue_aggregator.aggregate_window"
        ) as mock_window:
            mock_window.return_value = []
            exit_code = proxy_revenue_aggregator.main(["--days", "7"])

        assert exit_code == 0
        mock_window.assert_called_once()
        kw = mock_window.call_args.kwargs
        # 7-day window ending yesterday → start 6 days before yesterday.
        assert (kw["end_date"] - kw["start_date"]).days == 6

    def test_date_and_days_mutually_exclusive(self) -> None:
        # argparse raises SystemExit on mutex violation.
        with pytest.raises(SystemExit):
            proxy_revenue_aggregator.main(["--date", "2026-05-15", "--days", "7"])
