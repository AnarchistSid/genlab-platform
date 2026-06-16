"""Tests for scripts/archive_stale_visual_paths.py — pure logic only.

The DB-touching paths (find_stale_blueprints, archive_blueprints) are not
covered here; they get exercised end-to-end via the nightly systemd
service against the live DB. The covered surface is the pure helpers
that decide what's stale + the safety rules (skip scheduled, dry-run
default).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load the script as a module — it lives outside the package tree
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "archive_stale_visual_paths.py"
spec = importlib.util.spec_from_file_location("archive_stale_visual_paths", SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["archive_stale_visual_paths"] = mod
spec.loader.exec_module(mod)


class TestDecodeVisualPaths:
    def test_none_returns_empty(self):
        assert mod._decode_visual_paths(None) == []

    def test_empty_string_returns_empty(self):
        assert mod._decode_visual_paths("") == []

    def test_plain_list_passthrough(self):
        assert mod._decode_visual_paths(["/a.mp4", "/b.mp4"]) == ["/a.mp4", "/b.mp4"]

    def test_json_string_list_decoded(self):
        assert mod._decode_visual_paths('["/a.mp4"]') == ["/a.mp4"]

    def test_malformed_json_returns_empty(self):
        assert mod._decode_visual_paths("not json") == []

    def test_filters_non_strings(self):
        assert mod._decode_visual_paths(["/ok.mp4", 42, None, "/also.mp4"]) == [
            "/ok.mp4",
            "/also.mp4",
        ]

    def test_filters_empty_strings(self):
        assert mod._decode_visual_paths(["/keep.mp4", ""]) == ["/keep.mp4"]


class TestWriteBackup:
    def test_empty_input_returns_none(self, tmp_path):
        assert mod.write_backup([], tmp_path) is None

    def test_csv_contents(self, tmp_path):
        records = [
            {
                "id": "abc-123",
                "niche_id": "movies",
                "status": "VISUAL_READY",
                "scheduled_for": None,
                "stale_paths": ["/gone.mp4"],
                "present_paths": [],
                "all_missing": True,
            }
        ]
        path = mod.write_backup(records, tmp_path)
        assert path is not None
        assert path.exists()
        content = path.read_text()
        assert "abc-123" in content
        assert "movies" in content
        assert "/gone.mp4" in content
        assert "True" in content  # all_missing


class TestMainCLI:
    def test_dry_run_default(self, tmp_path, monkeypatch):
        """No --apply → should NOT touch DB. We patch the lookups so the
        function path runs without a real connection."""
        # Sub-out connect to avoid real DB
        monkeypatch.setattr(mod, "_connect", lambda: _FakeConn(records=[]))
        rc = mod.main(["--backup-dir", str(tmp_path)])
        assert rc == 0

    def test_apply_flag_recognized(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mod, "_connect", lambda: _FakeConn(records=[]))
        rc = mod.main(["--apply", "--backup-dir", str(tmp_path)])
        assert rc == 0  # no records → nothing to archive

    def test_db_error_returns_1(self, tmp_path, monkeypatch):
        def boom():
            raise RuntimeError("DATABASE_URL not set")

        monkeypatch.setattr(mod, "_connect", boom)
        rc = mod.main(["--backup-dir", str(tmp_path)])
        assert rc == 1


class _FakeConn:
    """Minimal stand-in for psycopg connection.

    Each fixture in the test layer that needs a fake connection can
    pass a list of records that find_stale_blueprints should return.
    We patch find_stale_blueprints directly when we care about its
    behaviour; this fake just supports the close() that the main()
    finally-block calls.
    """

    def __init__(self, records=None):
        self._records = records or []
        self._closed = False

    def close(self):
        self._closed = True

    def cursor(self):
        return _FakeCursor(self._records)

    def commit(self):
        pass


class _FakeCursor:
    def __init__(self, records):
        self._records = records

    def execute(self, *args, **kwargs):
        pass

    def fetchall(self):
        return []

    @property
    def rowcount(self):
        return 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


@pytest.fixture(autouse=True)
def _stub_find_when_fake_conn(monkeypatch):
    """Make find_stale_blueprints return [] when given a fake conn —
    keeps the CLI tests deterministic without a real DB."""
    real = mod.find_stale_blueprints

    def patched(conn, **kwargs):
        if isinstance(conn, _FakeConn):
            return conn._records
        return real(conn, **kwargs)

    monkeypatch.setattr(mod, "find_stale_blueprints", patched)
    yield


class TestPastScheduledLogic:
    """Pin the nuance added 2026-06-16: past-scheduled rows are archivable
    by default (publisher already had its chance), future-scheduled remain
    sacred per cleanup_safety.md."""

    def _stub_blueprint_rows(self):
        """Build a list of (id, niche, status, scheduled_for, vp) tuples
        as find_stale_blueprints would receive from psql."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        return [
            # 1) past-scheduled stale — should be archived by default
            ("aaa-aaa", "movies", "VISUAL_READY", now - timedelta(hours=5), ["/gone1.mp4"]),
            # 2) future-scheduled stale — should be sacred
            ("bbb-bbb", "anime", "VISUAL_READY", now + timedelta(days=2), ["/gone2.mp4"]),
            # 3) no schedule, stale — should be archived
            ("ccc-ccc", "gaming", "DRAFTED", None, ["/gone3.mp4"]),
        ]

    def test_past_scheduled_archived_by_default(self, monkeypatch):
        """Default settings: past-scheduled rows surface for archival;
        future-scheduled appear too but flagged as future_stale (caller
        decides — alert vs archive)."""
        rows = self._stub_blueprint_rows()
        conn = self._fake_conn_with_rows(rows)
        records = mod.find_stale_blueprints(conn)
        archivable = {r["id"] for r in records if not r.get("future_stale")}
        future_alerts = {r["id"] for r in records if r.get("future_stale")}
        # Past-scheduled + no-schedule are archivable
        assert "aaa-aaa" in archivable
        assert "ccc-ccc" in archivable
        # Future-scheduled is NOT archivable (sacred) but IS surfaced as
        # future_stale for the caller to alert on
        assert "bbb-bbb" in future_alerts
        assert "bbb-bbb" not in archivable
        # past_scheduled flag tracked correctly
        past_record = next(r for r in records if r["id"] == "aaa-aaa")
        assert past_record["past_scheduled"] is True
        assert past_record["future_stale"] is False

    def test_no_past_scheduled_flag_restores_old_behavior(self, monkeypatch):
        """--no-past-scheduled flag: past-scheduled rows become sacred
        again BUT future-stale alerts still fire (the alert layer is
        independent of the archive layer)."""
        rows = self._stub_blueprint_rows()
        conn = self._fake_conn_with_rows(rows)
        records = mod.find_stale_blueprints(conn, archive_past_scheduled=False)
        archivable = {r["id"] for r in records if not r.get("future_stale")}
        future_alerts = {r["id"] for r in records if r.get("future_stale")}
        # Past + future both protected from archival
        assert "aaa-aaa" not in archivable
        assert "bbb-bbb" not in archivable
        # No-schedule still archivable
        assert "ccc-ccc" in archivable
        # Future-stale alert still fires regardless
        assert "bbb-bbb" in future_alerts

    def test_include_scheduled_wins(self, monkeypatch):
        """--include-scheduled archives both past + future schedules
        and skips the future-stale-alert path entirely."""
        rows = self._stub_blueprint_rows()
        conn = self._fake_conn_with_rows(rows)
        records = mod.find_stale_blueprints(conn, include_scheduled=True)
        archivable = {r["id"] for r in records if not r.get("future_stale")}
        assert archivable == {"aaa-aaa", "bbb-bbb", "ccc-ccc"}
        # No future-stale alerts when --include-scheduled archives them
        assert not any(r.get("future_stale") for r in records)

    def test_grace_seconds_respected(self, monkeypatch):
        """A row scheduled 30 min ago should NOT count as past when
        grace=3600 (1h) — but IS future-stale (the publisher window
        hasn't passed yet, so the blueprint will still be attempted)."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        rows = [
            ("zzz-zzz", "movies", "VISUAL_READY", now - timedelta(minutes=30), ["/gone.mp4"]),
        ]
        conn = self._fake_conn_with_rows(rows)
        records = mod.find_stale_blueprints(conn, past_grace_seconds=3600)
        # 30 min < 1h grace → still in-flight; the row is "future" by
        # the grace rule and surfaces as future-stale (NOT archivable).
        archivable = [r for r in records if not r.get("future_stale")]
        future = [r for r in records if r.get("future_stale")]
        assert archivable == []
        assert len(future) == 1
        # With a 5-min grace: 30 min > 5 min → past → archivable
        conn2 = self._fake_conn_with_rows(rows)
        records = mod.find_stale_blueprints(conn2, past_grace_seconds=300)
        archivable = [r for r in records if not r.get("future_stale")]
        assert len(archivable) == 1
        assert archivable[0]["id"] == "zzz-zzz"

    def _fake_conn_with_rows(self, rows):
        """Build a fake connection whose cursor.fetchall() returns the
        given list (in the same shape psql would return)."""

        class _C:
            def __init__(self, rows):
                self._rows = rows
                self._fetched = False

            def execute(self, *args, **kwargs):
                pass

            def fetchall(self):
                if self._fetched:
                    return []
                self._fetched = True
                return self._rows

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        class _Conn:
            def __init__(self, rows):
                self._rows = rows

            def cursor(self):
                return _C(self._rows)

            def commit(self):
                pass

            def close(self):
                pass

        return _Conn(rows)


class TestFutureStaleAlerts:
    """Pin the future-stale-alert behaviour (2026-06-16 follow-up).

    Future-scheduled blueprints with deleted media won't fail until
    the publisher attempts them days later. Surfacing a CRITICAL
    alert NOW gives the operator time to re-render or reschedule
    before the failure happens.
    """

    def test_future_stale_in_records_with_flag(self, monkeypatch):
        """find_stale_blueprints returns future-stale rows with future_stale=True."""
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        rows = [
            (
                "future-stale-1",
                "movies",
                "VISUAL_READY",
                now + timedelta(days=3),
                ["/gone.mp4"],
            ),
        ]
        conn = self._fake_conn_with_rows(rows)
        records = mod.find_stale_blueprints(conn)
        # Should appear in records (so caller can alert) even though
        # it won't be archived.
        assert len(records) == 1
        assert records[0]["future_stale"] is True
        assert records[0]["past_scheduled"] is False

    def test_past_stale_not_marked_future_stale(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        rows = [
            (
                "past-stale-1",
                "movies",
                "VISUAL_READY",
                now - timedelta(hours=5),
                ["/gone.mp4"],
            ),
        ]
        conn = self._fake_conn_with_rows(rows)
        records = mod.find_stale_blueprints(conn)
        assert len(records) == 1
        assert records[0]["future_stale"] is False
        assert records[0]["past_scheduled"] is True

    def test_write_future_stale_alerts_skips_when_no_future_stale(self):
        """No future-stale records → 0 writes, no DB touch."""
        conn = _FakeConn(records=[])
        count = mod.write_future_stale_alerts(conn, [])
        assert count == 0

    def test_write_future_stale_alerts_writes_one_per_row(self, monkeypatch):
        """Each future-stale record writes one CRITICAL alert."""
        writes = []
        commits = []

        class _Cur:
            def __init__(self):
                self._returned_dedup = False

            def execute(self, sql, params=None):
                if "SELECT 1" in sql:
                    # Dedupe check returns nothing (fresh)
                    self._returned_dedup = True
                elif "INSERT INTO pipeline_alerts" in sql:
                    writes.append((sql, params))

            def fetchone(self):
                return None  # no dupe

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        class _Conn:
            def cursor(self):
                return _Cur()

            def commit(self):
                commits.append(True)

            def close(self):
                pass

        records = [
            {
                "id": "abc-123-def",
                "niche_id": "movies",
                "stale_paths": ["/gone.mp4"],
                "scheduled_for": "2026-06-19T07:00:00+00:00",
                "future_stale": True,
            },
            # non-future row should NOT trigger an alert
            {
                "id": "skip-this",
                "niche_id": "anime",
                "future_stale": False,
                "stale_paths": ["/also.mp4"],
                "scheduled_for": None,
            },
        ]
        count = mod.write_future_stale_alerts(_Conn(), records)
        assert count == 1
        assert len(writes) == 1
        assert "movies" in str(writes[0][1])

    def _fake_conn_with_rows(self, rows):
        class _C:
            def __init__(self, rows):
                self._rows = rows
                self._fetched = False

            def execute(self, *a, **kw):
                pass

            def fetchall(self):
                if self._fetched:
                    return []
                self._fetched = True
                return self._rows

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        class _Conn:
            def __init__(self, rows):
                self._rows = rows

            def cursor(self):
                return _C(self._rows)

            def commit(self):
                pass

            def close(self):
                pass

        return _Conn(rows)
