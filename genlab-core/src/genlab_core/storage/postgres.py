"""PostgresBackend — psycopg2-based storage with Row Level Security.

Fully synchronous — no asyncio, no event loop, no monkey-patch conflicts.
Works under gunicorn with any worker class (eventlet, gthread, sync).

RLS niche isolation is implemented via SET LOCAL app.niche_id scoped
to each transaction.

Records are returned in the standard {id, fields} format that
BacklogClient expects, matching the SharePoint record shape.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# PostgreSQL reserved words that must be quoted when used as column names.
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
PROMOTED_COLUMNS: dict[str, set[str]] = {
    "blueprints": {
        "niche_id", "candidate_id", "title", "status", "hook",
        "scheduled_for", "platform_publish_status", "video_id",
        "video_url", "source_url", "priority_score", "action_taken",
        "reviewed_at",
    },
    "stories": {
        "niche_id", "story_id", "title", "url", "source_name",
        "source_type", "status", "published_at", "score", "video_url",
        "video_id",
    },
    "assets": {
        "niche_id", "asset_id", "story_id", "url", "asset_type",
        "status", "source_type", "file_path",
    },
    "publishing_analytics": {
        "niche_id", "post_id", "platform", "published_at", "status",
        "views", "likes", "comments", "shares", "saves", "metrics_fetched",
    },
    "analytics": {
        "niche_id", "post_id", "platform", "metric_type", "value",
        "collected_at", "window",
    },
    "content_memory": {
        "niche_id", "content_hash", "title", "url", "first_seen",
        "last_seen",
    },
    "bandit_arms": {
        "niche_id", "arm_id", "alpha", "beta", "n_plays", "linucb_state",
    },
    "pending_engagement": {
        "niche_id", "post_id", "platform", "scheduled_at", "status",
        "attempts",
    },
    "pending_feedback": {
        "niche_id", "task_id", "post_id", "platform", "arm_id",
        "bandit_context", "collection_status", "reward_48h",
    },
    "templates": {
        "niche_id", "template_id", "name", "format", "max_slides",
        "max_duration_seconds", "status",
    },
    "sources": {
        "niche_id", "source_id", "name", "url", "source_type",
        "tier", "weight", "status", "last_fetched",
    },
    "monetisationprogress": {
        "niche_id", "platform", "metric_name", "current_value",
        "target_value", "pct_complete", "delta_7d",
        "days_to_threshold_est", "is_threshold_met", "data_source",
        "as_of_date", "error_log",
    },
}


class PostgresBackend:
    """Storage backend backed by local PostgreSQL with psycopg2.

    Fully synchronous. Thread-safe via connection pool.
    No asyncio, no event loop — compatible with eventlet, gthread, and sync workers.
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
        self._dsn = dsn or f"postgresql://{user}:{password}@{host}:{port}/{database}"
        self._min_size = min_size
        self._max_size = max_size
        self._pool = None
        self._pool_lock = threading.Lock()

    def _get_conn(self):
        """Get a connection from the pool (lazy-initialized)."""
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    import psycopg2
                    import psycopg2.pool
                    import psycopg2.extras
                    self._pool = psycopg2.pool.ThreadedConnectionPool(
                        self._min_size, self._max_size, self._dsn,
                    )
        return self._pool.getconn()

    def _put_conn(self, conn):
        """Return a connection to the pool."""
        if self._pool is not None:
            self._pool.putconn(conn)

    @staticmethod
    def _coerce_value(value: Any) -> Any:
        """Coerce ISO datetime strings to Python datetime objects."""
        if isinstance(value, str) and len(value) >= 19:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                pass
        return value

    def _split_fields(
        self, table: str, record: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Split record into promoted columns + extra JSONB overflow."""
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

    @staticmethod
    def _is_uuid(record_id: str) -> bool:
        record_id = record_id.strip()
        return len(record_id) >= 32 and "-" in record_id

    # ── CREATE ──────────────────────────────────────────────────────

    def create(self, table: str, record: Dict[str, Any], *, typecast: bool = False) -> str:
        """Create a record. Returns the new UUID record ID."""
        record_id = str(uuid.uuid4())
        cols, extra = self._split_fields(table, record)
        cols["extra"] = json.dumps(extra) if extra else "{}"

        col_names = list(cols.keys())
        quoted_names = [_quote_col(c) for c in col_names]
        placeholders = ["%s"] * (len(col_names) + 1)  # +1 for id
        values = [record_id] + [cols[c] for c in col_names]

        sql = (
            f"INSERT INTO {table} (id, {', '.join(quoted_names)}) "
            f"VALUES ({', '.join(placeholders)}) RETURNING id"
        )

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, values)
                row = cur.fetchone()
                conn.commit()
                return str(row[0]) if row else record_id
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

    # ── GET ─────────────────────────────────────────────────────────

    def get(self, table: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Get a single record by ID (UUID or legacy SharePoint integer ID)."""
        record_id = str(record_id).strip()

        conn = self._get_conn()
        try:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", ("",))
                if self._is_uuid(record_id):
                    cur.execute(f"SELECT * FROM {table} WHERE id = %s::uuid", (record_id,))
                else:
                    cur.execute(
                        f"SELECT * FROM {table} WHERE extra->>'sp_id' = %s",
                        (record_id,),
                    )
                row = cur.fetchone()
                conn.commit()
                if not row:
                    return None
                return self._row_to_record(dict(row))
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

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

        conn = self._get_conn()
        try:
            import psycopg2.extras
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT set_config('app.niche_id', %s, true)",
                    (niche_id or "",),
                )
                sql = f"SELECT * FROM {table}"
                if where_clause:
                    # Convert $N positional params to %s for psycopg2
                    import re
                    pg_where = re.sub(r'\$\d+', '%s', where_clause)
                    sql += f" WHERE {pg_where}"
                sql += " ORDER BY created_at DESC"
                if max_records:
                    sql += f" LIMIT {int(max_records)}"
                cur.execute(sql, params)
                rows = cur.fetchall()
                conn.commit()
                return [self._row_to_record(dict(r)) for r in rows]
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

    def all(
        self,
        table: str | None = None,
        *,
        formula: str = "",
        niche_id: str = "",
        max_records: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Alias for find()."""
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
        record_id = str(record_id).strip()

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", ("",))

                sets = []
                values: list[Any] = []
                for k, v in cols.items():
                    sets.append(f"{_quote_col(k)} = %s")
                    values.append(v)

                if extra:
                    sets.append("extra = extra || %s::jsonb")
                    values.append(json.dumps(extra))

                sets.append("updated_at = now()")

                if self._is_uuid(record_id):
                    where = "WHERE id = %s::uuid"
                else:
                    where = "WHERE extra->>'sp_id' = %s"
                values.append(record_id)

                sql = f"UPDATE {table} SET {', '.join(sets)} {where}"
                cur.execute(sql, values)
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

    # ── DELETE ──────────────────────────────────────────────────────

    def delete(self, table: str, record_id: str) -> None:
        """Delete a record by ID."""
        record_id = str(record_id).strip()

        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('app.niche_id', %s, true)", ("",))
                if self._is_uuid(record_id):
                    cur.execute(f"DELETE FROM {table} WHERE id = %s::uuid", (record_id,))
                else:
                    cur.execute(
                        f"DELETE FROM {table} WHERE extra->>'sp_id' = %s",
                        (record_id,),
                    )
                conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put_conn(conn)

    # ── BATCH CREATE ────────────────────────────────────────────────

    def batch_create(self, table: str, records: List[Dict[str, Any]]) -> List[str]:
        """Create multiple records. Returns list of new record IDs."""
        return [self.create(table, r) for r in records]

    # ── INTERNAL ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
        """Convert a PostgreSQL row to the {id, fields} format."""
        record_id = str(row.pop("id", ""))
        extra = row.pop("extra", None) or {}
        if isinstance(extra, str):
            extra = json.loads(extra)

        fields: dict[str, Any] = {}
        for k, v in row.items():
            if k in ("created_at", "updated_at"):
                continue
            if isinstance(v, (datetime, date)):
                fields[k] = v.isoformat()
            else:
                fields[k] = v
        fields.update(extra)

        return {"id": record_id, "fields": fields}


class PostgresTableProxy:
    """Binds a PostgresBackend to a specific table name.

    Drop-in replacement for GraphTableProxy / SyncListProxy in
    BacklogClient and SyncBacklogClient attribute slots.
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
            return self._backend.get(record_id_or_table, record_id)
        return self._backend.get(self._table, record_id_or_table)

    def create(self, table: str | None = None, fields: dict | None = None,
               *, typecast: bool = False, **kwargs):
        if fields is None and isinstance(table, dict):
            fields = table
            table = None
        return self._backend.create(table or self._table, fields or {}, typecast=typecast)

    def update(self, record_id_or_table: str, record_id_or_fields=None,
               fields: dict | None = None, *, typecast: bool = False):
        if isinstance(record_id_or_fields, dict):
            return self._backend.update(
                self._table, record_id_or_table, record_id_or_fields, typecast=typecast,
            )
        if fields is not None:
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
