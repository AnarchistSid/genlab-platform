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
    ) -> None:
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

    def create(self, table: str, record: Dict[str, Any]) -> str:
        """Create a record. Returns the new UUID record ID."""
        record_id = str(uuid.uuid4())
        cols, extra = self._split_fields(table, record)
        cols["extra"] = json.dumps(extra) if extra else "{}"

        col_names = list(cols.keys())
        # $1 is reserved for the id
        placeholders = [f"${i + 2}" for i in range(len(col_names))]
        values = [cols[c] for c in col_names]

        sql = (
            f"INSERT INTO {table} (id, {', '.join(col_names)}) "
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
    ) -> List[Dict[str, Any]]:
        """Find records matching a formula filter with RLS niche isolation."""
        from genlab_core.storage.formula_sql import formula_to_sql

        where_clause, params = formula_to_sql(formula)

        async def _do():
            pool = self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    # Set niche_id for RLS policy evaluation.
                    # SET LOCAL doesn't support parameterized queries,
                    # so we use set_config() which is SQL-injection safe.
                    await conn.execute(
                        "SELECT set_config('app.niche_id', $1, true)",
                        niche_id or "",
                    )
                    sql = f"SELECT * FROM {table}"
                    if where_clause:
                        sql += f" WHERE {where_clause}"
                    sql += " ORDER BY created_at DESC"
                    rows = await conn.fetch(sql, *params)
                    return [self._row_to_record(dict(r)) for r in rows]

        return self._run(_do())

    # ── UPDATE ──────────────────────────────────────────────────────

    def update(
        self,
        table: str,
        record_id: str,
        fields: Dict[str, Any],
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
                        sets.append(f"{k} = ${idx}")
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
