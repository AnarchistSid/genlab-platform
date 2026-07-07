"""Pin the Amazon Associates CSV importer (L3 PR 10, 2026-07-07).

The parser is the most fragile piece — Amazon has renamed columns
multiple times, ships CSVs with BOM markers, and uses different
locale conventions for currency + dates across regions. This suite
locks in the resilience contract:

  1. Column aliases resolve correctly against the current + prior
     Amazon export formats
  2. Malformed rows (bad date, empty ASIN, unparseable currency)
     are SKIPPED, not crash the whole file
  3. BOM + Unicode + European locale (comma decimal) all parse
  4. Same file content → same hash (idempotency)
  5. Different filename + same content → same hash (still idempotent)
  6. Line numbers survive skipped rows (importer needs stable
     ``csv_line_no`` for the migration's unique constraint)
"""

from __future__ import annotations

from datetime import date

import pytest
from genlab_core.monetization.amazon_report_importer import (
    ConversionRow,
    _parse_currency,
    _parse_date,
    _parse_int,
    _resolve_column_map,
    compute_csv_hash,
    parse_csv,
)


class TestColumnResolution:
    """Amazon has renamed these at least twice. Pin every known
    variant so a future rename doesn't silently break imports."""

    def test_current_headers(self):
        headers = [
            "Date",
            "ASIN",
            "Title",
            "Items Shipped",
            "Items Returned",
            "Ordered Item Revenue",
            "Ordered Item Earnings",
            "Tracking ID",
        ]
        col_map = _resolve_column_map(headers)
        assert col_map["report_date"] == "Date"
        assert col_map["asin"] == "ASIN"
        assert col_map["items_shipped"] == "Items Shipped"
        assert col_map["revenue"] == "Ordered Item Revenue"
        assert col_map["earnings"] == "Ordered Item Earnings"
        assert col_map["tracking_id"] == "Tracking ID"

    def test_legacy_headers_still_recognized(self):
        """Pre-2023 CSVs used 'Item Revenue' / 'Item Earnings'."""
        headers = [
            "Transaction Date",
            "Item ASIN",
            "Item Title",
            "Shipped Items",
            "Item Revenue",
            "Item Earnings",
        ]
        col_map = _resolve_column_map(headers)
        assert col_map["report_date"] == "Transaction Date"
        assert col_map["asin"] == "Item ASIN"
        assert col_map["revenue"] == "Item Revenue"
        assert col_map["earnings"] == "Item Earnings"

    def test_case_insensitive_matching(self):
        """Amazon has been inconsistent about capitalization
        (``TRACKING ID`` in one export, ``Tracking Id`` in another)."""
        headers = ["date", "ASIN", "items shipped", "TRACKING ID"]
        col_map = _resolve_column_map(headers)
        assert "report_date" in col_map
        assert "asin" in col_map
        assert "items_shipped" in col_map
        assert "tracking_id" in col_map

    def test_missing_columns_returns_partial_map(self):
        """A CSV missing all revenue columns still yields a map with
        the columns it DID find. Caller decides whether to abort."""
        headers = ["Date", "ASIN"]
        col_map = _resolve_column_map(headers)
        assert "report_date" in col_map
        assert "revenue" not in col_map

    def test_bom_stripped(self):
        """Amazon's dashboard export sometimes ships a UTF-8 BOM on
        the FIRST column name. Match anyway."""
        headers = ["﻿Date", "ASIN"]
        col_map = _resolve_column_map(headers)
        assert "report_date" in col_map


class TestDateParsing:
    def test_iso_format(self):
        assert _parse_date("2026-07-07") == date(2026, 7, 7)

    def test_us_format(self):
        assert _parse_date("07/07/2026") == date(2026, 7, 7)

    def test_iso_with_time(self):
        assert _parse_date("2026-07-07T14:23:00") == date(2026, 7, 7)

    def test_unparseable_returns_none(self):
        assert _parse_date("not a date") is None
        assert _parse_date("") is None

    def test_whitespace_stripped(self):
        assert _parse_date("  2026-07-07  ") == date(2026, 7, 7)


class TestCurrencyParsing:
    def test_plain_number(self):
        assert _parse_currency("1234.56") == pytest.approx(1234.56)

    def test_us_thousands(self):
        assert _parse_currency("1,234.56") == pytest.approx(1234.56)

    def test_european_decimal(self):
        """1.234,56 EUR → 1234.56 (Amazon EU exports use this)."""
        assert _parse_currency("1.234,56") == pytest.approx(1234.56)

    def test_indian_rupee_symbol(self):
        assert _parse_currency("₹1499.99") == pytest.approx(1499.99)

    def test_us_dollar_symbol(self):
        assert _parse_currency("$29.99") == pytest.approx(29.99)

    def test_empty_returns_none(self):
        assert _parse_currency("") is None
        assert _parse_currency("-") is None
        assert _parse_currency("—") is None  # Amazon uses em-dash

    def test_negative(self):
        """Returns get logged as negative revenue (refund)."""
        assert _parse_currency("-100.00") == pytest.approx(-100.00)


class TestIntParsing:
    def test_valid_int(self):
        assert _parse_int("5") == 5

    def test_empty_returns_zero(self):
        assert _parse_int("") == 0
        assert _parse_int("-") == 0

    def test_whitespace(self):
        assert _parse_int("  3  ") == 3

    def test_invalid_returns_default(self):
        assert _parse_int("not-an-int", default=99) == 99


class TestParseCsv:
    """End-to-end CSV parsing on realistic Amazon-format fixtures."""

    _SAMPLE_CSV = (
        "Date,ASIN,Title,Items Shipped,Items Returned,"
        "Ordered Item Revenue,Ordered Item Earnings,Tracking ID\n"
        "2026-07-06,B0DJHG2VVS,PlayStation 5,2,0,99980.00,2999.40,aspirehub06-20\n"
        "2026-07-06,B0BHD9TS9Q,NVIDIA RTX 4090,1,0,159900.00,4797.00,aspirehub06-20\n"
        "2026-07-06,B0CP7SV7XV,Gaming Monitor,3,1,45000.00,1350.00,aspirehub06-20\n"
    )

    def test_parses_3_rows(self):
        rows = list(parse_csv(self._SAMPLE_CSV))
        assert len(rows) == 3

    def test_first_row_correct_values(self):
        rows = list(parse_csv(self._SAMPLE_CSV))
        r = rows[0]
        assert r.asin == "B0DJHG2VVS"
        assert r.title == "PlayStation 5"
        assert r.report_date == date(2026, 7, 6)
        assert r.items_shipped == 2
        assert r.items_returned == 0
        assert r.revenue_inr == pytest.approx(99980.00)
        assert r.earnings_inr == pytest.approx(2999.40)
        assert r.tracking_id == "aspirehub06-20"
        assert r.csv_line_no == 2  # line 1 is header

    def test_line_numbers_correct(self):
        rows = list(parse_csv(self._SAMPLE_CSV))
        assert [r.csv_line_no for r in rows] == [2, 3, 4]

    def test_malformed_date_skipped(self):
        """Row with unparseable date is skipped; others survive."""
        csv = "Date,ASIN,Items Shipped\nnot-a-date,B0DJHG2VVS,2\n2026-07-06,B0BHD9TS9Q,1\n"
        rows = list(parse_csv(csv))
        assert len(rows) == 1
        assert rows[0].asin == "B0BHD9TS9Q"
        # Line number of the SURVIVING row must reflect its actual
        # position (line 3) so the runner's uniqueness constraint
        # never collides
        assert rows[0].csv_line_no == 3

    def test_empty_asin_skipped(self):
        csv = "Date,ASIN,Items Shipped\n2026-07-06,,2\n2026-07-06,B0BHD9TS9Q,1\n"
        rows = list(parse_csv(csv))
        assert len(rows) == 1
        assert rows[0].asin == "B0BHD9TS9Q"

    def test_asin_case_normalized_to_uppercase(self):
        """Amazon ASINs are canonically uppercase. Normalize on
        parse so downstream JOINs don't case-mismatch."""
        csv = "Date,ASIN,Items Shipped\n2026-07-06,b0djhg2vvs,2\n"
        rows = list(parse_csv(csv))
        assert rows[0].asin == "B0DJHG2VVS"

    def test_missing_required_columns_yields_empty(self):
        """A CSV missing 'ASIN' can't be processed at all."""
        csv = "Date,Items Shipped\n2026-07-06,2\n"
        rows = list(parse_csv(csv))
        assert rows == []

    def test_missing_optional_columns_still_parses(self):
        """A CSV with only required columns still yields rows,
        just with None for the missing optional fields."""
        csv = "Date,ASIN,Items Shipped\n2026-07-06,B0DJHG2VVS,2\n"
        rows = list(parse_csv(csv))
        assert len(rows) == 1
        assert rows[0].revenue_inr is None
        assert rows[0].earnings_inr is None
        assert rows[0].tracking_id is None
        assert rows[0].title == ""


class TestCsvHash:
    """Hash-based idempotence key."""

    def test_same_content_same_hash(self, tmp_path):
        p1 = tmp_path / "a.csv"
        p2 = tmp_path / "b.csv"  # different filename
        p1.write_text("Date,ASIN,Items Shipped\n2026-07-06,B0DJHG2VVS,2\n")
        p2.write_text("Date,ASIN,Items Shipped\n2026-07-06,B0DJHG2VVS,2\n")
        assert compute_csv_hash(p1) == compute_csv_hash(p2)

    def test_different_content_different_hash(self, tmp_path):
        p1 = tmp_path / "a.csv"
        p2 = tmp_path / "b.csv"
        p1.write_text("Date,ASIN,Items Shipped\n2026-07-06,B0DJHG2VVS,2\n")
        p2.write_text("Date,ASIN,Items Shipped\n2026-07-07,B0DJHG2VVS,2\n")
        assert compute_csv_hash(p1) != compute_csv_hash(p2)

    def test_hash_is_hex_sha256(self, tmp_path):
        p = tmp_path / "a.csv"
        p.write_text("hello")
        h = compute_csv_hash(p)
        assert len(h) == 64  # sha256 hex digest
        assert all(c in "0123456789abcdef" for c in h)


class TestFrozenDataclass:
    """ConversionRow is frozen because instances get held in memory
    during batch inserts; accidental mutation would cause hard-to-
    debug INSERT drift. Pin the immutability."""

    def test_cannot_mutate(self):
        r = ConversionRow(
            report_date=date(2026, 7, 6),
            asin="B0DJHG2VVS",
            title="PS5",
            items_shipped=2,
            items_returned=0,
            revenue_inr=99980.00,
            earnings_inr=2999.40,
            tracking_id="aspirehub06-20",
            csv_line_no=2,
        )
        with pytest.raises((AttributeError, TypeError)):
            r.asin = "OTHER"  # type: ignore[misc]
