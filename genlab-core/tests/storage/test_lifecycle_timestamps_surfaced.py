"""Regression pins: PostgresBackend._row_to_record surfaces created_at/updated_at.

Live-diagnosed 2026-06-23: the URL-dedup TTL filter shipped in PR #500 was a
no-op because ``_row_to_record`` explicitly stripped ``created_at`` and
``updated_at`` from the returned record. The TTL helpers couldn't reach the
date, fell into the conservative "keep in dedup" path on every blueprint,
and the 78-of-89 active-blueprint hash count never narrowed.

This test pins:
  - top-level keys are present
  - they parse as ISO8601 (so consumers can ``datetime.fromisoformat``)
  - they are NOT inside ``fields`` (would silently round-trip through
    ``_split_fields`` into ``extra`` JSONB on the next write, growing it
    unboundedly and shadowing the authoritative SQL columns)

If this pin breaks, the URL-dedup TTL stops working — gaming + sports
revert to the 2026-06-23 outage shape (0 blueprints despite fresh trending
content).
"""

from __future__ import annotations

from datetime import UTC, datetime

from genlab_core.storage.postgres import PostgresBackend


def test_row_to_record_surfaces_created_at_at_top_level() -> None:
    now = datetime.now(UTC)
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "extra": {},
        "niche_id": "gaming",
        "title": "trending overwatch",
        "created_at": now,
        "updated_at": now,
    }
    rec = PostgresBackend._row_to_record(row)
    assert "created_at" in rec, "created_at must surface at top level"
    assert "updated_at" in rec, "updated_at must surface at top level"
    assert rec["created_at"] is not None
    assert rec["updated_at"] is not None


def test_row_to_record_lifecycle_keys_are_iso_strings() -> None:
    """Top-level lifecycle keys must be ISO-formatted so consumers can parse them."""
    now = datetime.now(UTC)
    row = {
        "id": "22222222-2222-2222-2222-222222222222",
        "extra": {},
        "niche_id": "sports",
        "created_at": now,
        "updated_at": now,
    }
    rec = PostgresBackend._row_to_record(row)
    # Round-trip — proves they're the ISO format consumers expect
    parsed = datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00"))
    assert abs((parsed - now).total_seconds()) < 1


def test_row_to_record_does_not_leak_lifecycle_into_fields() -> None:
    """``fields`` must stay clean of created_at/updated_at.

    If either leaks into ``fields``, the next write-back round trip would
    push them through ``_split_fields`` into ``extra`` JSONB (they aren't
    promoted columns), bloating the JSONB indefinitely.
    """
    row = {
        "id": "33333333-3333-3333-3333-333333333333",
        "extra": {"hook": "from extra"},
        "niche_id": "movies",
        "title": "movie title",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    rec = PostgresBackend._row_to_record(row)
    assert "created_at" not in rec["fields"], "created_at must not leak into fields"
    assert "updated_at" not in rec["fields"], "updated_at must not leak into fields"
    # But the legit fields must still be there
    assert rec["fields"]["niche_id"] == "movies"
    assert rec["fields"]["title"] == "movie title"
    assert rec["fields"]["hook"] == "from extra"


def test_row_to_record_handles_null_lifecycle_columns() -> None:
    """Defensive: rows missing created_at/updated_at must not crash."""
    row = {"id": "44444444-4444-4444-4444-444444444444", "extra": {}, "niche_id": "anime"}
    rec = PostgresBackend._row_to_record(row)
    assert rec["created_at"] is None
    assert rec["updated_at"] is None
    # And fields is still well-formed
    assert rec["fields"]["niche_id"] == "anime"


def test_url_ttl_helper_reads_top_level_first() -> None:
    """The pre_download TTL helper must prefer the top-level key over fields.

    This is the actual integration point — if the helper reads only
    ``fields.get(...)`` (the pre-PR-#501 shape), the TTL goes back to
    being a no-op because the Postgres backend doesn't put created_at
    in fields.
    """
    from datetime import timedelta

    from genlab_core.pipeline.stages.pre_download_dedup import _is_within_url_ttl

    cutoff = datetime.now(UTC) - timedelta(days=7)

    # Top-level key carries an OLD date — should EXCLUDE from dedup set
    bp_top_old = {
        "id": "x",
        "fields": {"video_url": "y"},
        "created_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
    }
    assert _is_within_url_ttl(bp_top_old, cutoff) is False, (
        "Top-level created_at must be the primary source"
    )

    # Top-level key carries a RECENT date — should KEEP in dedup set
    bp_top_recent = {
        "id": "x",
        "fields": {"video_url": "y"},
        "created_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
    }
    assert _is_within_url_ttl(bp_top_recent, cutoff) is True

    # Fallback path: no top-level, but fields has created_at (legacy / tests)
    bp_fields_only = {
        "fields": {
            "video_url": "y",
            "created_at": (datetime.now(UTC) - timedelta(days=30)).isoformat(),
        }
    }
    assert _is_within_url_ttl(bp_fields_only, cutoff) is False, (
        "fields fallback must still work for legacy callers"
    )
