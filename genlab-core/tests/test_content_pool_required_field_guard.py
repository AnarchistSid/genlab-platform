"""Pin: content_pool row skip-on-NULL-required-fields guard (2026-06-22 part 2).

The 2026-06-22 audit chain:
- PR #446 fixed published_at datetime → str coercion (one Pydantic
  strict-type failure)
- PR #447 added tracebacks to stage_runner failures (diagnostic surface)
- THIS PR hardens against the SAME bug class on title + source_url —
  the content_pool schema allows NULL for both, but StoryCandidate
  requires non-None strings.

Without this guard, one corrupt content_pool row (NULL title) would
crash the WHOLE FetchTrendingVideos stage for that niche on every
run until the row was manually deleted. With the guard, a single
WARN log per bad row + the rest of the batch proceeds normally.

These pins lock in:

  1. Row with NULL title → skipped (not included in returned list)
  2. Row with NULL source_url → skipped
  3. Row with empty-string title → skipped (treats "" same as None)
  4. WARN log emitted per skipped row with row id
  5. Aggregate WARN log emitted with skipped count
  6. Valid rows still flow through normally (no regression)
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from genlab_core.media.trending_video_fetcher import FetchTrendingVideos


def _make_row(*, row_id="row_1", title="Test", source_url="https://e.com", **overrides):
    """Minimal valid content_pool row dict."""
    base = {
        "id": row_id,
        "title": title,
        "source_url": source_url,
        "summary": "",
        "video_url": "https://youtube.com/v/x",
        "video_id": "x",
        "thumbnail_url": "",
        "published_at": None,
        "duration_seconds": 30,
        "view_count": 100,
        "view_velocity": 1.0,
        "source_platform": "youtube",
    }
    base.update(overrides)
    return base


def _patched_pg_context(rows):
    """Build the patched chain of (pg_connect → cursor → fetchall → rows)."""
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.execute = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value.__enter__ = lambda self: cur
    conn.cursor.return_value.__exit__ = lambda *a: None
    conn.commit = MagicMock()
    pg_ctx = MagicMock()
    pg_ctx.__enter__ = lambda self: conn
    pg_ctx.__exit__ = lambda *a: None
    return pg_ctx


class TestSkipsRowsWithMissingRequiredFields:
    def test_null_title_row_skipped(self, monkeypatch, caplog):
        """Pin: NULL title → row skipped (would crash StoryCandidate)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        rows = [_make_row(row_id="bad_title", title=None)]

        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_patched_pg_context(rows),
        ):
            with caplog.at_level(logging.WARNING):
                result = FetchTrendingVideos._read_from_content_pool("gaming")

        # Bad row dropped entirely
        assert result == []
        # WARN emitted
        warn_logs = [r for r in caplog.records if "missing required" in r.message]
        assert len(warn_logs) >= 1
        assert "bad_title" in warn_logs[0].message

    def test_null_source_url_row_skipped(self, monkeypatch, caplog):
        """Pin: NULL source_url → row skipped (would crash StoryCandidate)."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        rows = [_make_row(row_id="bad_url", source_url=None)]

        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_patched_pg_context(rows),
        ):
            with caplog.at_level(logging.WARNING):
                result = FetchTrendingVideos._read_from_content_pool("gaming")

        assert result == []
        warn_logs = [r for r in caplog.records if "missing required" in r.message]
        assert any("bad_url" in r.message for r in warn_logs)

    def test_empty_string_title_skipped(self, monkeypatch, caplog):
        """Pin: empty string treated same as None (Python truthy check).
        StoryCandidate.title is non-empty-required in practice — an
        empty-title story is useless content."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        rows = [_make_row(row_id="empty_title", title="")]

        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_patched_pg_context(rows),
        ):
            with caplog.at_level(logging.WARNING):
                result = FetchTrendingVideos._read_from_content_pool("gaming")

        assert result == []


class TestValidRowsStillFlow:
    def test_valid_row_returned(self, monkeypatch):
        """Pin: regression-guard — the guard MUST NOT reject valid rows.
        This is the canary for "did we accidentally over-tighten?" """
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        rows = [_make_row(row_id="good_1", title="Real title", source_url="https://e.com/a")]

        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_patched_pg_context(rows),
        ):
            result = FetchTrendingVideos._read_from_content_pool("gaming")

        assert len(result) == 1
        assert result[0]["title"] == "Real title"
        assert result[0]["source_url"] == "https://e.com/a"

    def test_mixed_valid_and_invalid(self, monkeypatch, caplog):
        """Pin: one bad row doesn't poison the batch — partial result
        returned with WARN for each skipped row + aggregate count."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
        rows = [
            _make_row(row_id="good_1", title="A", source_url="https://e.com/a"),
            _make_row(row_id="bad_1", title=None),
            _make_row(row_id="good_2", title="B", source_url="https://e.com/b"),
            _make_row(row_id="bad_2", source_url=None),
        ]

        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            return_value=_patched_pg_context(rows),
        ):
            with caplog.at_level(logging.WARNING):
                result = FetchTrendingVideos._read_from_content_pool("gaming")

        # 2 valid stories returned
        assert len(result) == 2
        ids_returned = {r["title"] for r in result}
        assert ids_returned == {"A", "B"}
        # Aggregate skipped log emitted
        agg_logs = [r for r in caplog.records if "skipped 2 content_pool rows" in r.message]
        assert len(agg_logs) >= 1
