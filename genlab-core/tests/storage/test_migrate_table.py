"""Tests for genlab_core.storage.migrate_table.

All external dependencies (SharePoint, PostgreSQL) are mocked.
Focuses on record transformation, SQL generation, and migration flow logic.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from genlab_core.storage.migrate_table import (
    ALL_TABLES,
    PG_TABLE_TO_SP_LIST,
    SP_LIST_TO_PG_TABLE,
    TABLE_UNIQUE_KEY,
    _build_insert_sql,
    _get_sp_proxy,
    _record_exists,
    flatten_sp_record,
    migrate_table,
)
from genlab_core.storage.postgres import PostgresBackend


# ── flatten_sp_record ────────────────────────────────────────────────


class TestFlattenSpRecord:
    """Tests for SharePoint -> flat dict transformation."""

    def test_basic_record(self):
        sp_record = {
            "id": "42",
            "fields": {
                "story_id": "st_abc123",
                "title": "Test Story",
                "status": "INTAKE",
                "niche_id": "gaming",
            },
            "createdTime": "2026-03-17T10:00:00Z",
        }
        flat = flatten_sp_record(sp_record)
        assert flat["story_id"] == "st_abc123"
        assert flat["title"] == "Test Story"
        assert flat["status"] == "INTAKE"
        assert flat["niche_id"] == "gaming"
        assert flat["sp_id"] == "42"

    def test_preserves_sharepoint_id(self):
        sp_record = {"id": "999", "fields": {"title": "A"}}
        flat = flatten_sp_record(sp_record)
        assert flat["sp_id"] == "999"

    def test_skips_none_values(self):
        sp_record = {
            "id": "1",
            "fields": {
                "title": "OK",
                "url": None,
                "score": None,
            },
        }
        flat = flatten_sp_record(sp_record)
        assert "title" in flat
        assert "url" not in flat
        assert "score" not in flat

    def test_empty_fields(self):
        sp_record = {"id": "5", "fields": {}}
        flat = flatten_sp_record(sp_record)
        assert flat == {"sp_id": "5"}

    def test_missing_id(self):
        sp_record = {"fields": {"title": "No ID"}}
        flat = flatten_sp_record(sp_record)
        assert flat["sp_id"] == ""
        assert flat["title"] == "No ID"

    def test_nested_dict_preserved(self):
        sp_record = {
            "id": "10",
            "fields": {
                "platform_publish_status": {"instagram": "OK", "youtube": "PENDING"},
            },
        }
        flat = flatten_sp_record(sp_record)
        assert flat["platform_publish_status"] == {
            "instagram": "OK",
            "youtube": "PENDING",
        }

    def test_created_time_not_in_flat(self):
        """createdTime is top-level metadata, not a field — should not appear."""
        sp_record = {
            "id": "7",
            "fields": {"title": "A"},
            "createdTime": "2026-03-17T10:00:00Z",
        }
        flat = flatten_sp_record(sp_record)
        assert "createdTime" not in flat


# ── _build_insert_sql ────────────────────────────────────────────────


class TestBuildInsertSql:
    """Tests for SQL INSERT generation with ON CONFLICT."""

    def test_blueprints_has_conflict_on_candidate_id(self):
        record = {
            "candidate_id": "cand_001",
            "niche_id": "gaming",
            "title": "Test",
            "status": "DRAFTED",
        }
        sql, values = _build_insert_sql("blueprints", record)
        assert "ON CONFLICT (candidate_id) DO NOTHING" in sql
        assert "INSERT INTO blueprints" in sql
        # First value is the generated UUID
        assert len(values[0]) == 36  # UUID length

    def test_stories_has_conflict_on_story_id(self):
        record = {
            "story_id": "st_xyz",
            "niche_id": "gaming",
            "title": "Test Story",
        }
        sql, values = _build_insert_sql("stories", record)
        assert "ON CONFLICT (story_id) DO NOTHING" in sql

    def test_assets_has_conflict_on_asset_id(self):
        record = {
            "asset_id": "ast_001",
            "niche_id": "gaming",
        }
        sql, values = _build_insert_sql("assets", record)
        assert "ON CONFLICT (asset_id) DO NOTHING" in sql

    def test_content_memory_has_conflict_on_content_hash(self):
        record = {
            "content_hash": "abc123def456",
            "niche_id": "gaming",
        }
        sql, values = _build_insert_sql("content_memory", record)
        assert "ON CONFLICT (content_hash) DO NOTHING" in sql

    def test_bandit_arms_has_conflict_on_arm_id(self):
        record = {
            "arm_id": "arm_001",
            "niche_id": "gaming",
        }
        sql, values = _build_insert_sql("bandit_arms", record)
        assert "ON CONFLICT (arm_id) DO NOTHING" in sql

    def test_pending_feedback_has_conflict_on_task_id(self):
        record = {
            "task_id": "task_001",
            "niche_id": "gaming",
        }
        sql, values = _build_insert_sql("pending_feedback", record)
        assert "ON CONFLICT (task_id) DO NOTHING" in sql

    def test_templates_has_conflict_on_template_id(self):
        record = {
            "template_id": "tpl_001",
            "niche_id": "gaming",
        }
        sql, values = _build_insert_sql("templates", record)
        assert "ON CONFLICT (template_id) DO NOTHING" in sql

    def test_publishing_analytics_falls_back_to_id_conflict(self):
        record = {
            "niche_id": "gaming",
            "platform": "instagram",
            "post_id": "p_123",
        }
        sql, values = _build_insert_sql("publishing_analytics", record)
        assert "ON CONFLICT (id) DO NOTHING" in sql

    def test_analytics_falls_back_to_id_conflict(self):
        record = {
            "niche_id": "gaming",
            "platform": "youtube",
            "post_id": "p_456",
        }
        sql, values = _build_insert_sql("analytics", record)
        assert "ON CONFLICT (id) DO NOTHING" in sql

    def test_extra_fields_go_to_jsonb(self):
        record = {
            "candidate_id": "cand_002",
            "niche_id": "gaming",
            "title": "Test",
            "status": "DRAFTED",
            "sp_id": "42",
            "custom_field": "value",
        }
        sql, values = _build_insert_sql("blueprints", record)
        # extra should be one of the values containing sp_id and custom_field
        extra_values = [v for v in values if isinstance(v, str) and "sp_id" in v]
        assert len(extra_values) == 1
        extra_data = json.loads(extra_values[0])
        assert extra_data["sp_id"] == "42"
        assert extra_data["custom_field"] == "value"

    def test_promoted_dict_field_serialized_to_json(self):
        record = {
            "candidate_id": "cand_003",
            "niche_id": "gaming",
            "platform_publish_status": {"ig": "OK"},
        }
        sql, values = _build_insert_sql("blueprints", record)
        # platform_publish_status is promoted and should be JSON-serialized
        json_values = [v for v in values if isinstance(v, str) and "ig" in v]
        assert len(json_values) >= 1

    def test_all_tables_have_unique_key_mapping(self):
        """Every PG table must have an entry in TABLE_UNIQUE_KEY."""
        for table in ALL_TABLES:
            assert table in TABLE_UNIQUE_KEY, f"{table} missing from TABLE_UNIQUE_KEY"

    def test_sql_has_returning_clause_is_absent(self):
        """Migration uses ON CONFLICT DO NOTHING, not RETURNING."""
        record = {"story_id": "st_1", "niche_id": "gaming"}
        sql, _ = _build_insert_sql("stories", record)
        # ON CONFLICT DO NOTHING makes RETURNING unreliable, so we don't use it
        assert "RETURNING" not in sql

    def test_reserved_word_column_quoted(self):
        """The 'value' column in analytics should be quoted."""
        record = {
            "niche_id": "gaming",
            "value": 42.0,
            "post_id": "p_1",
        }
        sql, _ = _build_insert_sql("analytics", record)
        assert '"value"' in sql


# ── _record_exists ───────────────────────────────────────────────────


class TestRecordExists:
    """Tests for duplicate detection logic."""

    def test_exists_by_unique_key(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = {"1": 1}

        record = {"story_id": "st_abc", "niche_id": "gaming"}
        result = asyncio.run(_record_exists(conn, "stories", record))
        assert result is True
        conn.fetchrow.assert_called_once()
        call_args = conn.fetchrow.call_args
        assert "story_id" in call_args[0][0]
        assert call_args[0][1] == "st_abc"

    def test_not_exists_by_unique_key(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = None

        record = {"story_id": "st_new", "niche_id": "gaming"}
        result = asyncio.run(_record_exists(conn, "stories", record))
        assert result is False

    def test_falls_back_to_sp_id_for_tables_without_unique_key(self):
        conn = AsyncMock()
        conn.fetchrow.return_value = {"1": 1}

        record = {"niche_id": "gaming", "post_id": "p_1", "sp_id": "77"}
        result = asyncio.run(
            _record_exists(conn, "publishing_analytics", record)
        )
        assert result is True
        call_args = conn.fetchrow.call_args
        assert "sp_id" in call_args[0][0]
        assert call_args[0][1] == "77"

    def test_no_key_no_sp_id_returns_false(self):
        conn = AsyncMock()
        record = {"niche_id": "gaming", "post_id": "p_1"}
        result = asyncio.run(_record_exists(conn, "analytics", record))
        assert result is False


# ── _get_sp_proxy ────────────────────────────────────────────────────


class TestGetSpProxy:
    """Tests for SharePoint proxy attribute lookup."""

    def test_known_list(self):
        client = MagicMock()
        client.stories = MagicMock()
        proxy = _get_sp_proxy(client, "Stories")
        assert proxy is client.stories

    def test_blueprints_returns_guarded_proxy(self):
        client = MagicMock()
        client.blueprints = MagicMock()
        proxy = _get_sp_proxy(client, "Blueprints")
        assert proxy is client.blueprints

    def test_content_memory(self):
        client = MagicMock()
        client.content_memory = MagicMock()
        proxy = _get_sp_proxy(client, "Content_Memory")
        assert proxy is client.content_memory

    def test_bandit_arms(self):
        client = MagicMock()
        client.bandit_arms = MagicMock()
        proxy = _get_sp_proxy(client, "BanditArms")
        assert proxy is client.bandit_arms

    def test_unknown_list_raises(self):
        client = MagicMock()
        with pytest.raises(ValueError, match="Unknown SharePoint list"):
            _get_sp_proxy(client, "NonExistent")

    def test_none_proxy_raises(self):
        client = MagicMock()
        client.content_memory = None
        with pytest.raises(ValueError, match="is None"):
            _get_sp_proxy(client, "Content_Memory")


# ── Table mappings consistency ───────────────────────────────────────


class TestTableMappings:
    """Tests for mapping consistency between SP and PG."""

    def test_sp_to_pg_covers_all_11_tables(self):
        assert len(SP_LIST_TO_PG_TABLE) == 11

    def test_pg_to_sp_is_inverse(self):
        for sp_name, pg_name in SP_LIST_TO_PG_TABLE.items():
            assert PG_TABLE_TO_SP_LIST[pg_name] == sp_name

    def test_all_tables_matches_pg_values(self):
        assert set(ALL_TABLES) == set(SP_LIST_TO_PG_TABLE.values())

    def test_all_pg_tables_in_promoted_columns_or_known(self):
        """Every migrated table should either have promoted columns
        defined in postgres.py or be a known table."""
        from genlab_core.storage.postgres import PROMOTED_COLUMNS

        for table in ALL_TABLES:
            assert table in PROMOTED_COLUMNS, (
                f"{table} missing from PROMOTED_COLUMNS in postgres.py"
            )


# ── migrate_table ────────────────────────────────────────────────────


class TestMigrateTable:
    """Tests for the core migration function."""

    def _make_pg_backend(self):
        """Create a mocked PostgresBackend with async pool."""
        pg = MagicMock(spec=PostgresBackend)
        pg._loop = asyncio.new_event_loop()
        pg._pool = MagicMock()

        # Connection mock
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)  # no existing records

        # Pool.acquire() context manager
        pool_ctx = AsyncMock()
        pool_ctx.__aenter__ = AsyncMock(return_value=conn)
        pool_ctx.__aexit__ = AsyncMock(return_value=False)
        pg._pool.acquire.return_value = pool_ctx
        pg._get_pool.return_value = pg._pool
        pg._ensure_pool = MagicMock()

        return pg, conn

    def test_empty_records(self):
        pg, _ = self._make_pg_backend()
        stats = migrate_table(pg, [], "stories")
        assert stats == {"total": 0, "migrated": 0, "skipped": 0, "errors": 0}
        pg._loop.close()

    def test_migrates_new_records(self):
        pg, conn = self._make_pg_backend()

        sp_records = [
            {"id": "1", "fields": {"story_id": "st_1", "niche_id": "gaming", "title": "Story 1"}},
            {"id": "2", "fields": {"story_id": "st_2", "niche_id": "gaming", "title": "Story 2"}},
        ]

        stats = migrate_table(pg, sp_records, "stories")
        assert stats["total"] == 2
        assert stats["migrated"] == 2
        assert stats["skipped"] == 0
        assert stats["errors"] == 0

        # Verify INSERT was called for each record
        insert_calls = [
            c for c in conn.execute.call_args_list
            if c[0] and isinstance(c[0][0], str) and "INSERT" in c[0][0]
        ]
        assert len(insert_calls) == 2
        pg._loop.close()

    def test_skips_existing_records(self):
        pg, conn = self._make_pg_backend()

        # First record exists, second doesn't
        conn.fetchrow.side_effect = [
            {"1": 1},  # first _record_exists: found
            None,       # second _record_exists: not found
        ]

        sp_records = [
            {"id": "1", "fields": {"story_id": "st_existing", "niche_id": "gaming", "title": "Old"}},
            {"id": "2", "fields": {"story_id": "st_new", "niche_id": "gaming", "title": "New"}},
        ]

        stats = migrate_table(pg, sp_records, "stories")
        assert stats["total"] == 2
        assert stats["skipped"] == 1
        assert stats["migrated"] == 1
        pg._loop.close()

    def test_handles_insert_error_gracefully(self):
        pg, conn = self._make_pg_backend()

        # Record doesn't exist, but INSERT fails
        conn.fetchrow.return_value = None
        original_execute = conn.execute

        call_count = 0

        async def _side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: set_config (admin mode) — succeeds
                return None
            # Second call: INSERT — fails
            raise RuntimeError("Database error")

        conn.execute = AsyncMock(side_effect=_side_effect)

        sp_records = [
            {"id": "1", "fields": {"story_id": "st_1", "niche_id": "gaming", "title": "Test"}},
        ]

        stats = migrate_table(pg, sp_records, "stories")
        assert stats["total"] == 1
        assert stats["errors"] == 1
        assert stats["migrated"] == 0
        pg._loop.close()

    def test_sets_admin_mode_for_rls_bypass(self):
        pg, conn = self._make_pg_backend()

        sp_records = [
            {"id": "1", "fields": {"story_id": "st_1", "niche_id": "gaming", "title": "Test"}},
        ]

        migrate_table(pg, sp_records, "stories")

        # First execute call should be set_config for admin mode
        first_call = conn.execute.call_args_list[0]
        assert "set_config" in first_call[0][0]
        assert "app.niche_id" in first_call[0][0]
        pg._loop.close()

    def test_calls_ensure_pool(self):
        pg, conn = self._make_pg_backend()
        sp_records = [
            {"id": "1", "fields": {"story_id": "st_1", "niche_id": "gaming", "title": "Test"}},
        ]
        migrate_table(pg, sp_records, "stories")
        pg._ensure_pool.assert_called_once()
        pg._loop.close()

    def test_blueprints_migration(self):
        """Test migrating blueprints with promoted + extra fields."""
        pg, conn = self._make_pg_backend()

        sp_records = [
            {
                "id": "bp1",
                "fields": {
                    "candidate_id": "cand_001",
                    "niche_id": "gaming",
                    "title": "Blueprint 1",
                    "status": "DRAFTED",
                    "hook": "Something cool happened",
                    "priority_score": 0.85,
                    "topic": "FPS games",
                    "angle": "competitive",
                },
            },
        ]

        stats = migrate_table(pg, sp_records, "blueprints")
        assert stats["migrated"] == 1

        # Verify the INSERT SQL references candidate_id conflict
        insert_calls = [
            c for c in conn.execute.call_args_list
            if c[0] and isinstance(c[0][0], str) and "INSERT" in c[0][0]
        ]
        assert len(insert_calls) == 1
        sql = insert_calls[0][0][0]
        assert "ON CONFLICT (candidate_id) DO NOTHING" in sql
        pg._loop.close()
