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
        import threading
        self._lock = threading.Lock()

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

        Thread-safe: a lock serializes all event-loop operations so
        gunicorn gthread workers don't race on the same loop.
        """
        with self._lock:
            self._ensure_pool()
            assert self._loop is not None
            return self._loop.run_until_complete(coro)

    @staticmethod
    def _coerce_value(value: Any) -> Any:
        """Coerce values to types asyncpg accepts.

        Handles ISO datetime strings → datetime objects, and
        list/dict values → JSON strings for non-JSONB columns.
        """
        if isinstance(value, str) and len(value) >= 19:
            # Try to parse ISO datetime strings (2026-03-17T06:30:00Z)
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S+00:00",
                        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S.%f+00:00",
                        "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    return datetime.strptime(value, fmt)
                except (ValueError, TypeError):
                    continue
            # Try fromisoformat (handles +05:30 etc.)
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        return value

    def _split_fields(
        self, table: str, record: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Split record into promoted columns + extra JSONB overflow.

        Fields in PROMOTED_COLUMNS go to their own SQL columns.
        Everything else gets serialized into the `extra` JSONB column.
        Datetime strings are coerced to datetime objects for timestamp columns.
        """
        promoted = PROMOTED_COLUMNS.get(table, set())
        cols: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for k, v in record.items():
            if k in promoted:
                if isinstance(v, dict):
                    cols[k] = json.dumps(v)
                else:
                    cols[k] = self._coerce_value(v)
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

        Handles both Postgres UUIDs and legacy SharePoint integer IDs.
        SharePoint IDs are stored in the extra JSONB column as sp_id.
        """
        record_id = str(record_id).strip()
        is_uuid = len(record_id) >= 32 and "-" in record_id

        async def _do():
            pool = self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.niche_id', '', true)")
                    if is_uuid:
                        row = await conn.fetchrow(
                            f"SELECT * FROM {table} WHERE id = $1::uuid",
                            record_id,
                        )
                    else:
                        # Legacy SharePoint integer ID — search in extra JSONB
                        row = await conn.fetchrow(
                            f"SELECT * FROM {table} WHERE extra->>'sp_id' = $1",
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

    def _where_by_id(self, record_id: str, param_idx: int) -> tuple[str, str]:
        """Build WHERE clause for UUID or legacy SharePoint integer ID."""
        record_id = str(record_id).strip()
        is_uuid = len(record_id) >= 32 and "-" in record_id
        if is_uuid:
            return f"WHERE id = ${param_idx}::uuid", record_id
        return f"WHERE extra->>'sp_id' = ${param_idx}", record_id

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
                    where_clause, rid = self._where_by_id(record_id, idx)
                    values.append(rid)

                    sql = f"UPDATE {table} SET {', '.join(sets)} {where_clause}"
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
                    where_clause, rid = self._where_by_id(record_id, 1)
                    await conn.execute(
                        f"DELETE FROM {table} {where_clause}", rid,
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


class PostgresTableProxy:
    """Binds a PostgresBackend to a specific table name.

    This allows using PostgresBackend as a drop-in for GraphTableProxy
    in BacklogClient attribute slots (client.stories, client.blueprints, etc.)
    where callers expect table-bound methods.

    Usage:
        proxy = PostgresTableProxy(backend, "blueprints")
        proxy.find("Blueprints", formula="{status}='PUBLISHED'")
        proxy.all(formula="{status}='PUBLISHED'")  # table auto-inferred
        proxy.get("record-uuid")  # table auto-inferred
    """

    def __init__(self, backend: PostgresBackend, table: str) -> None:
        self._backend = backend
        self._table = table.lower()

    def find(self, table: str | None = None, *, formula: str = "",
             niche_id: str = "", max_records: int | None = None) -> list:
        return self._backend.find(
            table or self._table, formula=formula,
            niche_id=niche_id, max_records=max_records,
        )

    def all(self, table: str | None = None, *, formula: str = "",
            niche_id: str = "", max_records: int | None = None) -> list:
        return self._backend.find(
            table or self._table, formula=formula,
            niche_id=niche_id, max_records=max_records,
        )

    def get(self, record_id_or_table: str, record_id: str | None = None):
        if record_id is not None:
            # Called as get("Blueprints", "uuid") — table explicit
            return self._backend.get(record_id_or_table, record_id)
        # Called as get("uuid") — table inferred
        return self._backend.get(self._table, record_id_or_table)

    def create(self, table: str | None = None, fields: dict | None = None,
               *, typecast: bool = False, **kwargs):
        if fields is None and isinstance(table, dict):
            # Called as create({"field": "value"})
            fields = table
            table = None
        return self._backend.create(table or self._table, fields or {}, typecast=typecast)

    def update(self, record_id_or_table: str, record_id_or_fields=None,
               fields: dict | None = None, *, typecast: bool = False):
        if isinstance(record_id_or_fields, dict):
            # Called as update("uuid", {"field": "value"})
            return self._backend.update(
                self._table, record_id_or_table, record_id_or_fields, typecast=typecast,
            )
        if fields is not None:
            # Called as update("Table", "uuid", {"field": "value"})
            return self._backend.update(
                record_id_or_table, record_id_or_fields, fields, typecast=typecast,
            )
        raise ValueError("update() requires fields dict")

    def delete(self, record_id_or_table: str, record_id: str | None = None):
        if record_id is not None:
            return self._backend.delete(record_id_or_table, record_id)
        return self._backend.delete(self._table, record_id_or_table)

    def batch_create(self, table: str | None = None, records: list | None = None):
        if records is None and isinstance(table, list):
            records = table
            table = None
        return self._backend.batch_create(table or self._table, records or [])
