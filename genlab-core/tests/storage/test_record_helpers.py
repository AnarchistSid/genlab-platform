"""Tests for ``record_helpers.record_created_at`` + friends.

Pins the top-level → fields fallback semantics that prevent the
``_row_to_record`` lifecycle-column-stripping footgun from biting
new callers. Tracked instances of the bug pre-helper:

  * PR #500 → PR #501: URL-dedup TTL silently broken
  * PR #510 → PR #511: oldest-age computation silently None

If these pins regress, new code that uses the helper functions starts
returning None on Postgres-backend records again — silently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from genlab_core.storage.record_helpers import (
    record_created_at,
    record_created_at_dt,
    record_updated_at,
    record_updated_at_dt,
)

# ── raw value lookup ────────────────────────────────────────────────


def test_top_level_created_at_wins_over_fields_value():
    """When BOTH paths have created_at, top-level wins. This matches
    the Postgres backend's authoritative source — fields-level value
    can be a stale write-side artefact."""
    record = {
        "created_at": "2026-06-24T08:00:00+00:00",
        "fields": {"created_at": "2026-01-01T00:00:00+00:00"},
    }
    assert record_created_at(record) == "2026-06-24T08:00:00+00:00"


def test_top_level_created_at_returns_when_only_top_level_set():
    """Postgres-backend shape: top-level set, fields lacks the field."""
    record = {"created_at": "2026-06-24T08:00:00+00:00", "fields": {"niche_id": "x"}}
    assert record_created_at(record) == "2026-06-24T08:00:00+00:00"


def test_fields_created_at_fallback_when_top_level_absent():
    """Legacy / JSON-backend / test-fixture shape: top-level absent,
    fields has it."""
    record = {"fields": {"created_at": "2026-06-24T08:00:00+00:00"}}
    assert record_created_at(record) == "2026-06-24T08:00:00+00:00"


def test_returns_none_when_neither_path_has_the_field():
    assert record_created_at({"fields": {"niche_id": "x"}}) is None
    assert record_created_at({}) is None


def test_handles_non_dict_input_gracefully():
    """Never raise — defensive for callers iterating heterogeneous lists."""
    assert record_created_at(None) is None
    assert record_created_at("not a dict") is None
    assert record_created_at(42) is None


def test_handles_fields_not_being_a_dict():
    """If ``fields`` somehow isn't a dict (corrupt record), fall through
    to None rather than crashing."""
    record = {"fields": "corrupted-string"}
    assert record_created_at(record) is None


def test_updated_at_mirrors_created_at_semantics():
    """``record_updated_at`` uses the same lookup ladder — pin once."""
    record = {
        "updated_at": "2026-06-24T09:00:00+00:00",
        "fields": {"updated_at": "2026-01-01T00:00:00+00:00"},
    }
    assert record_updated_at(record) == "2026-06-24T09:00:00+00:00"
    # And the legacy-only path
    legacy = {"fields": {"updated_at": "2026-06-24T09:00:00+00:00"}}
    assert record_updated_at(legacy) == "2026-06-24T09:00:00+00:00"


# ── parsed datetime ─────────────────────────────────────────────────


def test_created_at_dt_parses_iso_string():
    record = {"created_at": "2026-06-24T08:00:00+00:00"}
    dt = record_created_at_dt(record)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 6 and dt.day == 24
    assert dt.tzinfo is not None  # timezone-aware


def test_created_at_dt_tolerates_trailing_Z():
    """Some backends emit ``"...Z"`` instead of ``"...+00:00"`` — handle both.
    Without this tolerance the PR #501 storage layer's `.isoformat()` output
    would never parse via the helper."""
    record = {"created_at": "2026-06-24T08:00:00Z"}
    dt = record_created_at_dt(record)
    assert dt is not None
    assert dt.tzinfo is not None


def test_created_at_dt_passes_through_existing_datetime():
    """When the backend (or a test fixture) hands a raw ``datetime``
    instead of an ISO string, the helper accepts it unchanged. Pin
    so a refactor to "string only" doesn't break in-memory test
    fixtures."""
    now = datetime.now(UTC)
    record = {"created_at": now}
    dt = record_created_at_dt(record)
    assert dt == now


def test_created_at_dt_returns_none_on_unparseable_string():
    """Malformed value must not crash the caller — return None."""
    record = {"created_at": "not-a-real-date"}
    assert record_created_at_dt(record) is None


def test_created_at_dt_returns_none_on_missing_field():
    assert record_created_at_dt({}) is None
    assert record_created_at_dt({"fields": {"niche_id": "x"}}) is None


# ── end-to-end age computation pattern (the original callers' use case) ─


def test_age_computation_works_when_only_top_level_created_at_set():
    """The exact pattern from ``overview.py`` and the URL-dedup TTL —
    pin that the most common downstream use case works through the
    helper without the caller having to know about the lookup trap."""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    # Postgres-backend shape — created_at at top level only
    bp = {
        "fields": {"video_url": "x", "status": "PUBLISHED"},
        "created_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
    }
    dt = record_created_at_dt(bp)
    assert dt is not None
    assert dt < cutoff, "30-day-old record must parse as older than 7-day cutoff"


def test_updated_at_dt_mirrors_full_semantics():
    """``record_updated_at_dt`` shares the same parsing + fallback logic
    as ``record_created_at_dt``. One pin to confirm the mirror is
    intact (catches a refactor that diverges them)."""
    pg_shape = {"updated_at": "2026-06-24T09:00:00Z"}
    legacy = {"fields": {"updated_at": "2026-06-24T09:00:00Z"}}

    pg_dt = record_updated_at_dt(pg_shape)
    legacy_dt = record_updated_at_dt(legacy)

    assert pg_dt is not None
    assert legacy_dt is not None
    assert pg_dt == legacy_dt


# ── parametric variants for coverage of the corner cases ──────────────


@pytest.mark.parametrize(
    "record,expected",
    [
        ({}, None),
        ({"created_at": None, "fields": {}}, None),
        ({"created_at": "", "fields": {}}, None),  # empty string is falsy → falls through to None
        ({"created_at": "", "fields": {"created_at": "2026-01-01"}}, "2026-01-01"),
        # The above pin matters: an explicit "" at top level must NOT
        # shadow a real value in fields. The bug shape would be the
        # helper returning "" because it "found" something at top
        # level, then the caller's downstream parse-or-date-compare
        # silently fails.
    ],
)
def test_falsy_top_level_falls_through_to_fields(record, expected):
    assert record_created_at(record) == expected
