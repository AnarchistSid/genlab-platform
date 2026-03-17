"""PostgresBackend — asyncpg-based storage with Row Level Security.

Uses asyncpg for all database operations. RLS niche isolation is
implemented via SET LOCAL app.niche_id scoped to each transaction.

Records are returned in the standard {id, fields} format that
BacklogClient expects, matching the SharePoint record shape.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# PostgreSQL reserved words that must be quoted when used as column names.
# See https://www.postgresql.org/docs/current/sql-keywords-appendix.html
_RESERVED_WORDS: frozenset[str] = frozenset({
    "window", "value", "user", "table", "column", "order", "group",
    "select", "where", "from", "to", "index", "check", "primary",
    "references", "constraint", "default", "null", "not", "and", "or",
    "all", "any", "as", "between", "case", "when", "then", "else",
    "end", "in", "like", "limit", "offset", "on", "set", "update",
    "delete", "insert", "into", "values", "create", "drop", "alter",
    "grant", "revoke", "name", "comment", "key", "type", "role",
})


def _quote_col(col: str) -> str:
    """Double-quote a column name if it is a PostgreSQL reserved word."""
    if col.lower() in _RESERVED_WORDS:
        return f'"{col}"'
    return col


# Columns that are promoted to proper SQL columns (not in extra JSONB).
# Any field NOT in this set for a given table goes into the `extra` JSONB column.
PROMOTED_COLUMNS: dict[str, set[str]] = {
    "blueprints": {
        "niche_id",
        "candidate_id",
        "title",
        "status",
        "hook",
        "scheduled_for",
        "platform_publish_status",
        "video_id",
        "video_url",
        "source_url",
        "priority_score",
        "action_taken",
        "reviewed_at",
    },
    # Phase 2
    "stories": {
        "niche_id",
        "story_id",
        "title",
        "url",
        "source_name",
        "source_type",
        "status",
        "published_at",
        "score",
        "video_url",
        "video_id",
    },
    "assets": {
        "niche_id",
        "asset_id",
        "story_id",
        "url",
        "asset_type",
        "status",
        "source_type",
        "file_path",
    },
    # Phase 3
    "publishing_analytics": {
        "niche_id",
        "post_id",
        "platform",
        "published_at",
        "status",
        "views",
        "likes",
        "comments",
        "shares",
        "saves",
        "metrics_fetched",
    },
    "analytics": {
        "niche_id",
        "post_id",
        "platform",
        "metric_type",
        "value",
        "collected_at",
        "window",
    },
    # Phase 4
    "content_memory": {
        "niche_id",
        "content_hash",
        "title",
        "url",
        "first_seen",
        "last_seen",
    },
    "bandit_arms": {
        "niche_id",
        "arm_id",
        "alpha",
        "beta",
        "n_plays",
        "linucb_state",
    },
    # Phase 5
    "pending_engagement": {
        "niche_id",
        "post_id",
        "platform",
        "scheduled_at",
        "status",
        "attempts",
    },
    "pending_feedback": {
        "niche_id",
        "task_id",
        "post_id",
        "platform",
        "arm_id",
        "bandit_context",
        "collection_status",
        "reward_48h",
        "publish_time",
    },
    # Phase 6
    "templates": {
        "niche_id",
        "template_id",
        "name",
        "category",
        "max_duration",
        "status",
    },
    "sources": {
        "niche_id",
        "source_id",
        "name",
        "url",
        "source_type",
        "tier",
        "weight",
        "status",
        "last_fetched",
    },
}


class PostgresBackend:
    """Storage backend backed by local PostgreSQL with RLS.

    All public methods are synchronous. Async operations run via a
    dedicated event loop. The connection pool is created lazily on
    the first operation.

    RLS is enforced by SET LOCAL app.niche_id within each transaction.
    When niche_id is empty or 'all', the RLS policy allows access to
    all records (admin/superuser mode).
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "genlab",
        user: str = "genlab",
        password: str = "",
        min_size: int = 2,
        max_size: int = 10,
        *,
        dsn: str | None = None,
    ) -> None:
        if dsn:
            self._dsn = dsn
        else:
            self._dsn = f"postgresql://{user}:{password}@{host}:{port}/{database}"
        self._min_size = min_size
        self._max_size = max_size
        self._pool = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _ensure_pool(self) -> None:
        """Create the event loop and connection pool if they don't exist.

        This must be called BEFORE entering run_until_complete() so we
        don't try to nest event loop operations.
        """
        if self._pool is not None:
            return

        import asyncpg

        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()

        # asyncpg.create_pool() calls asyncio.get_event_loop() in __init__,
        # so we must set our loop as the current loop first.
        asyncio.set_event_loop(self._loop)

        self._pool = self._loop.run_until_complete(
            asyncpg.create_pool(
                self._dsn,
                min_size=self._min_size,
                max_size=self._max_size,
            )
        )

    def _get_pool(self):
        """Return the connection pool (must be initialized via _ensure_pool)."""
        return self._pool

    def _run(self, coro):
        """Run an async coroutine synchronously.

        Ensures the pool exists BEFORE entering the event loop, avoiding
        the "event loop already running" error.
        """
        self._ensure_pool()
        assert self._loop is not None
        return self._loop.run_until_complete(coro)

    def _split_fields(
        self, table: str, record: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Split record into promoted columns + extra JSONB overflow.

        Fields in PROMOTED_COLUMNS go to their own SQL columns.
        Everything else gets serialized into the `extra` JSONB column.
        """
        promoted = PROMOTED_COLUMNS.get(table, set())
        cols: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for k, v in record.items():
            if k in promoted:
                # JSONB columns need to be serialized
                if isinstance(v, dict):
                    cols[k] = json.dumps(v)
                else:
                    cols[k] = v
            else:
                extra[k] = v
        return cols, extra

    # ── CREATE ──────────────────────────────────────────────────────

    def create(self, table: str, record: Dict[str, Any], *, typecast: bool = False) -> str:
        """Create a record. Returns the new UUID record ID."""
        record_id = str(uuid.uuid4())
        cols, extra = self._split_fields(table, record)
        cols["extra"] = json.dumps(extra) if extra else "{}"

        col_names = list(cols.keys())
        quoted_names = [_quote_col(c) for c in col_names]
        # $1 is reserved for the id
        placeholders = [f"${i + 2}" for i in range(len(col_names))]
        values = [cols[c] for c in col_names]

        sql = (
            f"INSERT INTO {table} (id, {', '.join(quoted_names)}) "
            f"VALUES ($1, {', '.join(placeholders)}) RETURNING id"
        )

        async def _do():
            pool = self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(sql, record_id, *values)
                return str(row["id"]) if row else record_id

        return self._run(_do())

    # ── GET ─────────────────────────────────────────────────────────

    def get(self, table: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Get a single record by ID. Returns None if not found.

        Uses empty niche_id (admin mode) to bypass RLS for direct ID lookups.
        """
        async def _do():
            pool = self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Admin mode: bypass RLS for direct ID lookups
                    await conn.execute("SELECT set_config('app.niche_id', '', true)")
                    row = await conn.fetchrow(
                        f"SELECT * FROM {table} WHERE id = $1::uuid",
                        record_id,
                    )
                    return dict(row) if row else None

        row = self._run(_do())
        if not row:
            return None
        return self._row_to_record(row)

    # ── FIND ────────────────────────────────────────────────────────

    def find(
        self,
        table: str,
        *,
        formula: str = "",
        niche_id: str = "",
        max_records: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Find records matching a formula filter with RLS niche isolation."""
        from genlab_core.storage.formula_sql import formula_to_sql

        where_clause, params = formula_to_sql(formula)

        async def _do():
            pool = self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT set_config('app.niche_id', $1, true)",
                        niche_id or "",
                    )
                    sql = f"SELECT * FROM {table}"
                    if where_clause:
                        sql += f" WHERE {where_clause}"
                    sql += " ORDER BY created_at DESC"
                    if max_records:
                        sql += f" LIMIT {int(max_records)}"
                    rows = await conn.fetch(sql, *params)
                    return [self._row_to_record(dict(r)) for r in rows]

        return self._run(_do())

    def all(
        self,
        table: str | None = None,
        *,
        formula: str = "",
        niche_id: str = "",
        max_records: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Alias for find() — matches GraphTableProxy.all() interface."""
        if table is None:
            raise ValueError("table is required for PostgresBackend.all()")
        return self.find(table, formula=formula, niche_id=niche_id, max_records=max_records)

    # ── UPDATE ──────────────────────────────────────────────────────

    def update(
        self,
        table: str,
        record_id: str,
        fields: Dict[str, Any],
        *,
        typecast: bool = False,
    ) -> None:
        """Update fields on an existing record."""
        cols, extra = self._split_fields(table, fields)

        async def _do():
            pool = self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Admin mode for updates (direct ID access)
                    await conn.execute("SELECT set_config('app.niche_id', '', true)")

                    sets = []
                    values: list[Any] = []
                    idx = 0
                    for k, v in cols.items():
                        idx += 1
                        sets.append(f"{_quote_col(k)} = ${idx}")
                        values.append(v)

                    if extra:
                        idx += 1
                        sets.append(f"extra = extra || ${idx}::jsonb")
                        values.append(json.dumps(extra))

                    sets.append("updated_at = now()")

                    idx += 1
                    values.append(record_id)

                    sql = (
                        f"UPDATE {table} SET {', '.join(sets)} "
                        f"WHERE id = ${idx}::uuid"
                    )
                    await conn.execute(sql, *values)

        self._run(_do())

    # ── DELETE ──────────────────────────────────────────────────────

    def delete(self, table: str, record_id: str) -> None:
        """Delete a record by ID."""
        async def _do():
            pool = self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.niche_id', '', true)")
                    await conn.execute(
                        f"DELETE FROM {table} WHERE id = $1::uuid",
                        record_id,
                    )

        self._run(_do())

    # ── BATCH CREATE ────────────────────────────────────────────────

    def batch_create(
        self,
        table: str,
        records: List[Dict[str, Any]],
    ) -> List[str]:
        """Create multiple records. Returns list of new record IDs."""
        return [self.create(table, r) for r in records]

    # ── INTERNAL ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
        """Convert a PostgreSQL row to the {id, fields} format.

        This matches the record shape returned by GraphTableProxy /
        SharePointBackend, so BacklogClient callers don't need to change.
        """
        record_id = str(row.pop("id", ""))
        extra = row.pop("extra", None) or {}
        if isinstance(extra, str):
            extra = json.loads(extra)

        # Merge promoted columns + extra into fields
        # Exclude internal timestamp columns
        fields: dict[str, Any] = {}
        for k, v in row.items():
            if k in ("created_at", "updated_at"):
                continue
            # Convert datetime objects to ISO strings for consistency
            if isinstance(v, (datetime, date)):
                fields[k] = v.isoformat()
            else:
                fields[k] = v
        fields.update(extra)

        return {"id": record_id, "fields": fields}
