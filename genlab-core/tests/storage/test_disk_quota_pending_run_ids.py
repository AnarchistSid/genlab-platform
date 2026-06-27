"""Pin: 2026-06-28 — disk_quota._get_pending_publish_run_ids() must
protect blueprints in ALL publish-pending states, not just scheduled ones.

The original query at storage/disk_quota.py:_get_pending_publish_run_ids
required ``scheduled_for IS NOT NULL`` AND a status-not-in-terminal
filter. Investigation found 6 blueprints over 2 months (April-June
2026) that lost their media files between render-time and operator
approve-time:

  Day N:   blueprint rendered → DRAFTED with visual_paths in extra
  Day N+M: cleanup runs; ``scheduled_for IS NULL`` so this blueprint
           NOT in pending_run_ids set; ``protect_recent: 3`` evicts
           the run dir; media file deleted
  Day N+K: operator approves + schedules
  Day N+L: publisher runs, file gone → ``No valid media files for
           video publish`` → blueprint eventually auto-archived

Fix: broaden the WHERE clause to ``status NOT IN (PUBLISHED, ARCHIVED,
PUBLISH_FAILED, REJECTED)`` AND drop the ``scheduled_for`` requirement.
Any blueprint still in a publish-pending state needs its media kept,
regardless of whether the operator has scheduled it yet.

This pin guards against regression to the scheduled_for-required gate
which would silently re-create the cleanup race.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DISK_QUOTA = REPO_ROOT / "genlab-core" / "src" / "genlab_core" / "storage" / "disk_quota.py"


class TestPendingRunIdsQuery:
    """Pin the SQL shape of _get_pending_publish_run_ids."""

    def test_query_does_not_require_scheduled_for(self):
        """The fix removes the ``scheduled_for IS NOT NULL`` requirement.
        DRAFTED/VISUAL_READY blueprints (post-render, pre-approval) must
        also have their media protected — otherwise cleanup evicts the
        file while operator is reviewing."""
        content = DISK_QUOTA.read_text()
        # Locate the function body
        fn_start = content.find("def _get_pending_publish_run_ids")
        assert fn_start > 0, "_get_pending_publish_run_ids must exist"
        next_def = content.find("\ndef ", fn_start + 10)
        body = content[fn_start:next_def]

        # The SQL inside this function must NOT contain `scheduled_for IS NOT NULL`.
        assert "scheduled_for IS NOT NULL" not in body, (
            "_get_pending_publish_run_ids must NOT require scheduled_for IS NOT NULL. "
            "Investigation 2026-06-28 found 6 blueprints lost media because cleanup "
            "ran during DRAFTED/VISUAL_READY window before operator scheduled them. "
            "Drop the scheduled_for requirement; keep the terminal-status NOT IN filter."
        )

    def test_query_excludes_terminal_statuses(self):
        """Terminal states (PUBLISHED, ARCHIVED, PUBLISH_FAILED, REJECTED)
        must remain excluded — these don't need protection."""
        content = DISK_QUOTA.read_text()
        fn_start = content.find("def _get_pending_publish_run_ids")
        next_def = content.find("\ndef ", fn_start + 10)
        body = content[fn_start:next_def]

        # Required exclusions for all terminal/never-publishing states.
        for terminal in ("PUBLISHED", "ARCHIVED", "PUBLISH_FAILED", "REJECTED"):
            assert terminal in body, (
                f"_get_pending_publish_run_ids must exclude {terminal!r} from "
                f"the protected set. Terminal states by definition don't need "
                f"their media kept."
            )
        # The construct should be a `status NOT IN (...)` clause.
        assert "status NOT IN" in body, (
            "Must use `status NOT IN (...)` to express the protected set. "
            "Inverting to `status IN (...)` risks missing future status values "
            "(safer to enumerate the few terminal states)."
        )

    def test_query_requires_visual_paths_key(self):
        """Without visual_paths in extra, there's no file to protect.
        The ``extra ? 'visual_paths'`` JSONB-key-exists check must
        remain — otherwise the function returns rows we can't extract
        a run_id from."""
        content = DISK_QUOTA.read_text()
        fn_start = content.find("def _get_pending_publish_run_ids")
        next_def = content.find("\ndef ", fn_start + 10)
        body = content[fn_start:next_def]

        assert "extra ? 'visual_paths'" in body, (
            "Query must keep the `extra ? 'visual_paths'` JSONB-key-exists "
            "filter — otherwise blueprints with no rendered media show up "
            "and we can't extract a run_id from their (missing) paths."
        )


class TestPendingRunIdsBehavior:
    """Behavior-level pins via mocked psycopg connection."""

    def test_returns_empty_set_when_no_db_url(self, monkeypatch):
        """No DATABASE_URL → empty set (fail-OPEN; don't crash the
        cleanup loop just because the DB env var is missing)."""
        from genlab_core.storage.disk_quota import _get_pending_publish_run_ids

        monkeypatch.setenv("DATABASE_URL", "")
        result = _get_pending_publish_run_ids()
        assert result == set()

    def test_returns_empty_set_on_psycopg_failure(self, monkeypatch):
        """DB query failure → empty set, log WARNING. The cleanup loop
        must continue even when the protection lookup fails — better to
        risk evicting a pending blueprint than to crash and let disk fill."""
        from genlab_core.storage import disk_quota

        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@invalid-host:9999/db")
        # No actual mock needed — invalid host will raise inside psycopg.connect
        # and the function's try/except should swallow it.
        result = disk_quota._get_pending_publish_run_ids()
        assert result == set(), (
            "Connection failure must return empty set, not raise. The cleanup "
            "loop continues with NO pending-run protection that pass (the "
            "log line warns the operator)."
        )

    def test_extracts_run_id_from_visual_paths(self, monkeypatch):
        """Given a fake DB row whose visual_paths references a
        .tmp/runs/<run_id>/visuals/... path, the run_id must be added
        to the protected set."""
        import json
        from unittest.mock import MagicMock, patch

        # Fake row: visual_paths is a JSON-encoded list (matches prod schema)
        sample_path = "/opt/genlab/.tmp/runs/movies_20260618_082904/visuals/abc/reel.mp4"
        fake_row = (json.dumps([sample_path]),)
        fake_cursor = MagicMock()
        fake_cursor.fetchall.return_value = [fake_row]
        fake_cursor.__enter__ = lambda self: self
        fake_cursor.__exit__ = lambda *args: None
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        fake_conn.__enter__ = lambda self: self
        fake_conn.__exit__ = lambda *args: None

        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@host:5432/db")
        with patch("psycopg.connect", return_value=fake_conn):
            from genlab_core.storage.disk_quota import _get_pending_publish_run_ids

            result = _get_pending_publish_run_ids()

        assert "movies_20260618_082904" in result, (
            "run_id extracted from visual_paths must end up in the protected set. "
            "Extraction regex/marker must match both /runs/ and /rendered/ layouts."
        )

    def test_extracts_run_id_from_rendered_path_format(self, monkeypatch):
        """CriticalRush uses /<channel>/.tmp/rendered/<run_id>/... layout.
        The extractor must handle both `/runs/` and `/rendered/` markers."""
        import json
        from unittest.mock import MagicMock, patch

        sample_path = "/opt/genlab/CriticalRush/.tmp/rendered/gaming_20260528_040029/file.mp4"
        fake_row = (json.dumps([sample_path]),)
        fake_cursor = MagicMock()
        fake_cursor.fetchall.return_value = [fake_row]
        fake_cursor.__enter__ = lambda self: self
        fake_cursor.__exit__ = lambda *args: None
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        fake_conn.__enter__ = lambda self: self
        fake_conn.__exit__ = lambda *args: None

        monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@host:5432/db")
        with patch("psycopg.connect", return_value=fake_conn):
            from genlab_core.storage.disk_quota import _get_pending_publish_run_ids

            result = _get_pending_publish_run_ids()

        assert "gaming_20260528_040029" in result, (
            "Extractor must also handle /rendered/ marker, not just /runs/."
        )
