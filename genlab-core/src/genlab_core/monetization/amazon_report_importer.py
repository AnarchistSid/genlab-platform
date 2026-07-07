"""Amazon Associates report CSV importer (L3 PR 10, 2026-07-07).

Reads the daily-earnings CSV that operators download from
associates.amazon.com/reports and returns parsed
:class:`ConversionRow` objects.  A companion runner
(``scripts/import_amazon_conversions.py``) drives this into the
``affiliate_conversions`` table for L3 PR 11's real-revenue reward
escalation.

## Why CSV, not API

Amazon does not expose a public affiliate-conversions API. There's
PA-API for product SEARCH (queries prices + availability) but no
endpoint that returns "what did MY affiliate tag actually sell today?"
The associates dashboard is the only source for that data, and it's
retrieved by manual CSV download (or by scraping the dashboard,
which we're not doing).

CSV import is the industry norm for this — Genius Link, Refersion,
and other affiliate management tools all offer the same pattern for
Amazon specifically. Once we hit a scale where PA-API's $5+ trailing
30-day threshold clears, that unlocks product-side data (prices,
titles, images) but STILL doesn't give us conversions — those stay
CSV-only until Amazon changes their API surface.

## Expected CSV shape (as of 2026-07)

The daily-earnings CSV from the operator's associates dashboard has
this header row (semicolon-separated in EU, comma in US/IN):

    Date, ASIN, Title, Category, Items Shipped, Items Returned,
    Ordered Item Revenue, Ordered Item Earnings, Tracking ID

Amazon has changed column ordering + names at least twice in the
last 3 years. To survive future format churn, we parse by column
NAME (via csv.DictReader) rather than index, and fail gracefully
when expected columns are absent — the runner logs the missing
columns + skips the file.

## Design non-goals

* **No API polling** — deferred until Amazon exposes one (probably
  never for individual affiliates)
* **No historical backfill** — CSV imports process forward in time;
  a re-imported CSV is idempotent via ``source_csv_hash`` +
  ``csv_line_no`` uniqueness (see the migration)
* **No cross-marketplace normalization** — a US CSV comes in USD,
  an IN CSV comes in INR. We DO NOT convert; operator picks a
  single-marketplace CSV per import run and downstream code reads
  the ``tracking_id`` column to know the currency implicitly.

## Consumer wire (L3 PR 11)

The reward escalation SQL will do:

    UPDATE bandit_arms
    SET alpha = 1.0 + n_purchases,
        beta = 1.0 + (n_clicks - n_purchases)
    ...

where ``n_purchases = SUM(items_shipped - items_returned)`` from
``affiliate_conversions`` per (product_slug, date-window), joined
back to ``affiliate_clicks`` for ``n_clicks``.

That's L3 PR 11's job; this module just gets the data INTO the
table.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


# Column names as they appear in Amazon's CSV headers (case-insensitive
# after normalization). Mapping to our internal field names.
#
# We accept multiple variants because Amazon renamed these at least
# twice: "Item Revenue" became "Ordered Item Revenue" in 2023;
# "Earnings" became "Ordered Item Earnings" the same year.
_COLUMN_ALIASES: dict[str, list[str]] = {
    "report_date": ["Date", "Transaction Date", "Report Date"],
    "asin": ["ASIN", "Item ASIN"],
    "title": ["Title", "Item Title", "Product Title"],
    "items_shipped": ["Items Shipped", "Shipped Items", "Items"],
    "items_returned": ["Items Returned", "Returned Items"],
    "revenue": [
        "Ordered Item Revenue",
        "Item Revenue",
        "Revenue",
        "Ordered Revenue",
    ],
    "earnings": [
        "Ordered Item Earnings",
        "Item Earnings",
        "Earnings",
        "Total Earnings",
    ],
    "tracking_id": ["Tracking ID", "Tracking Id", "Tag"],
}


@dataclass(frozen=True)
class ConversionRow:
    """One parsed row from an Amazon Associates CSV.

    Frozen because these get held in memory during batch inserts —
    accidental mutation would cause hard-to-debug INSERT drift.
    """

    report_date: date
    asin: str
    title: str
    items_shipped: int
    items_returned: int
    revenue_inr: float | None
    earnings_inr: float | None
    tracking_id: str | None
    csv_line_no: int


def compute_csv_hash(csv_path: Path) -> str:
    """SHA-256 the source CSV file. Used as the idempotency key.

    Reads in chunks so a huge CSV doesn't OOM the runner. The hash
    identifies "this exact file bytes" — re-uploading the same CSV
    with a different filename still hashes to the same value and
    won't create duplicates.
    """
    h = hashlib.sha256()
    with open(csv_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_col(name: str) -> str:
    """Strip + lowercase for alias matching. Amazon sometimes ships
    BOM + trailing whitespace in header row column names."""
    return name.strip().lstrip("﻿").lower()


def _resolve_column_map(fieldnames: list[str]) -> dict[str, str]:
    """Build ``{internal_field: actual_csv_column_name}`` from the
    header row.  Missing internal fields are absent from the result;
    caller decides whether to skip the file.

    Case-insensitive match against ``_COLUMN_ALIASES``.
    """
    normalized_actual = {_normalize_col(fn): fn for fn in fieldnames}
    resolved: dict[str, str] = {}
    for internal, variants in _COLUMN_ALIASES.items():
        for variant in variants:
            if _normalize_col(variant) in normalized_actual:
                resolved[internal] = normalized_actual[_normalize_col(variant)]
                break
    return resolved


def _parse_date(value: str) -> date | None:
    """Amazon uses YYYY-MM-DD in the dashboard export. Some legacy
    exports use MM/DD/YYYY. Try both.  Returns None on failure so
    the caller can skip the row."""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_currency(value: str) -> float | None:
    """Strip common currency formatting: ``₹``, ``$``, thousands
    separators, whitespace. Empty / dash returns None (row had no
    revenue this day)."""
    if not value:
        return None
    stripped = value.strip()
    if not stripped or stripped in ("-", "—"):
        return None
    # Drop any character that isn't a digit, dot, minus, or comma.
    numeric = re.sub(r"[^\d.,\-]", "", stripped)
    # Convert European decimal format (1.234,56 → 1234.56)
    if "," in numeric and "." in numeric:
        if numeric.rindex(",") > numeric.rindex("."):
            numeric = numeric.replace(".", "").replace(",", ".")
        else:
            numeric = numeric.replace(",", "")
    elif "," in numeric and "." not in numeric:
        # Ambiguous: could be thousands separator or decimal. If
        # exactly one comma with 3 digits after, treat as thousands.
        if re.match(r"^-?\d{1,3},\d{3}$", numeric):
            numeric = numeric.replace(",", "")
        else:
            numeric = numeric.replace(",", ".")
    try:
        return float(numeric)
    except ValueError:
        return None


def _parse_int(value: str, default: int = 0) -> int:
    """Amazon uses integer counts for items_shipped/returned.
    Empty / dash returns 0 (no items that day)."""
    if not value or value.strip() in ("", "-", "—"):
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def parse_csv(csv_text: str) -> Iterator[ConversionRow]:
    """Yield :class:`ConversionRow` for every parseable line.

    Malformed rows are skipped with a DEBUG log — parser continues
    with the next line rather than aborting the whole file. Rate of
    skipping is exposed via the runner's summary output.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        logger.warning("[amazon-importer] CSV has no header row")
        return

    col_map = _resolve_column_map(reader.fieldnames)
    required = ("report_date", "asin", "items_shipped")
    missing = [r for r in required if r not in col_map]
    if missing:
        logger.warning(
            "[amazon-importer] CSV missing required columns %s (fields: %s)",
            missing,
            reader.fieldnames,
        )
        return

    for line_no, row in enumerate(reader, start=2):  # 1 = header
        report_date = _parse_date(row.get(col_map["report_date"], ""))
        if report_date is None:
            logger.debug(
                "[amazon-importer] line %d: unparseable date %r; skipping",
                line_no,
                row.get(col_map["report_date"]),
            )
            continue
        asin = (row.get(col_map["asin"]) or "").strip().upper()
        if not asin:
            logger.debug("[amazon-importer] line %d: empty ASIN; skipping", line_no)
            continue
        yield ConversionRow(
            report_date=report_date,
            asin=asin,
            title=(row.get(col_map.get("title", ""), "") or "").strip(),
            items_shipped=_parse_int(row.get(col_map["items_shipped"], "")),
            items_returned=_parse_int(row.get(col_map.get("items_returned", ""), "")),
            revenue_inr=_parse_currency(row.get(col_map.get("revenue", ""), "")),
            earnings_inr=_parse_currency(row.get(col_map.get("earnings", ""), "")),
            tracking_id=(row.get(col_map.get("tracking_id", ""), "").strip() or None),
            csv_line_no=line_no,
        )


def parse_csv_file(csv_path: Path) -> tuple[str, list[ConversionRow]]:
    """Convenience: read the file + return (hash, rows).

    Reads the whole file into memory — Amazon's daily CSVs are
    typically < 100 KB even for high-volume affiliates, so no need to
    stream. If we ever handle multi-MB reports, refactor to a
    streaming pipeline.
    """
    csv_hash = compute_csv_hash(csv_path)
    text = csv_path.read_text(encoding="utf-8-sig")  # strip BOM
    rows = list(parse_csv(text))
    return csv_hash, rows


__all__ = [
    "ConversionRow",
    "compute_csv_hash",
    "parse_csv",
    "parse_csv_file",
]
