"""PostgresBackend — psycopg3-based storage with Row Level Security.

Fully synchronous — no asyncio, no event loop, no monkey-patch conflicts.
Works under gunicorn with any worker class (eventlet, gthread, sync).

RLS niche isolation is implemented via SET LOCAL app.niche_id scoped
to each transaction.

Records are returned in the standard {id, fields} format that
BacklogClient expects, matching the SharePoint record shape.

Uses psycopg3 (the `psycopg` package) with:
- ConnectionPool for thread-safe pooling
- dict_row factory for direct dict results (no RealDictCursor)
- Native type adaptation (datetime, UUID handled automatically)
- Pipeline mode for batch operations
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading
import uuid
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)


# Cached at import time so every checked-out connection pays the cost once.
_HOST_ID = os.environ.get("GENLAB_HOST_ID", "").strip() or socket.gethostname() or "unknown"


def _configure_connection(conn) -> None:
    """Stamp every pooled connection with `app.host_id`.

    The DB-side trigger `tag_host_id` reads this GUC when populating
    `extra->>'host_id'` on blueprint and publishing_analytics writes.
    Without it, the trigger falls back to `inet_client_addr()`, which
    misattributes Hetzner-internal connections (NULL or docker bridge IP).

    Postgres `SET` does not accept parameters, so we use `set_config()`
    which does. The third arg `false` makes the value persist for the
    whole session (until the connection is checked back into the pool).
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.host_id', %s, false)", (_HOST_ID,))
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to set app.host_id on new connection: %s", exc)


# PostgreSQL reserved words that must be quoted when used as column names.
_RESERVED_WORDS: frozenset[str] = frozenset(
    {
        "window",
        "value",
        "user",
        "table",
        "column",
        "order",
        "group",
        "select",
        "where",
        "from",
        "to",
        "index",
        "check",
        "primary",
        "references",
        "constraint",
        "default",
        "null",
        "not",
        "and",
        "or",
        "all",
        "any",
        "as",
        "between",
        "case",
        "when",
        "then",
        "else",
        "end",
        "in",
        "like",
        "limit",
        "offset",
        "on",
        "set",
        "update",
        "delete",
        "insert",
        "into",
        "values",
        "create",
        "drop",
        "alter",
        "grant",
        "revoke",
        "name",
        "comment",
        "key",
        "type",
        "role",
    }
)


def _quote_col(col: str) -> str:
    """Double-quote a column name if it is a PostgreSQL reserved word."""
    if col.lower() in _RESERVED_WORDS:
        return f'"{col}"'
    return col


# Valid table names — prevents SQL injection via table name interpolation.
_VALID_TABLES: frozenset[str] = frozenset(
    {
        "blueprints",
        "stories",
        "assets",
        "publishing_analytics",
        "analytics",
        "content_memory",
        "bandit_arms",
        "pending_engagement",
        "pending_feedback",
        "templates",
        "sources",
        "monetisationprogress",
        "ab_tests",
        "audience_snapshots",
        "affiliate_clicks",
        "email_subscribers",
    }
)


def _validate_table(table: str) -> str:
    """Validate table name against allowlist. Raises ValueError on invalid."""
    t = table.lower()
    if t not in _VALID_TABLES:
        raise ValueError(f"Invalid table name: {table!r}")
    return t


# Columns that are promoted to proper SQL columns (not in extra JSONB).
PROMOTED_COLUMNS: dict[str, set[str]] = {
    "blueprints": {
        "niche_id",
        "candidate_id",
        "title",
        "status",
        "hook",
        "hook_text",
        "caption",
        "format",
        "story_id",
        "topic",
        "arm_id",
        "scheduled_for",
        "platform_publish_status",
        "video_id",
        "video_url",
        "source_url",
        "priority_score",
        "action_taken",
        "reviewed_at",
        "source",
        "summary",
        "error_message",
        "blueprint_id",
        "affiliate_product",
        "affiliate_url",
        "affiliate_network",
        "affiliate_commission_pct",
        "affiliate_cta",
        "affiliate_cta_variant",
        # PR after #585 (2026-06-25): source-discovery proposer foundation.
        # YouTube channel_id (UC...) of the SOURCE clip's uploader.
        # Captured at trending-fetch time for blueprints sourced from a
        # YouTube clip; NULL for legacy blueprints + non-YouTube sources.
        # Migration g3h4i5j6k7l8 adds the column + partial index.
        "source_channel_id",
    },
    "stories": {
        "niche_id",
        "story_id",
        "title",
        "url",
        "status",
        "published_at",
        "score",
        "source",
        "summary",
        # R-63: these are REAL columns on the stories table (migration
        # a1b2c3d4e5f6) but were absent from this map — so writes landed in the
        # ``extra`` JSONB while reads preferred the (NULL) real column, silently
        # dropping the value. The test-storage CI job caught this on video_id.
        "source_name",
        "source_type",
        "video_id",
        "video_url",
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
        "blueprint_id",
        "error_message",
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
    "pending_engagement": {
        "niche_id",
        "post_id",
        "platform",
        "status",
        "attempts",
        "scheduled_at",
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
    "monetisationprogress": {
        "niche_id",
        "platform",
        "metric_name",
        "current_value",
        "target_value",
        "pct_complete",
        "delta_7d",
        "days_to_threshold_est",
        "is_threshold_met",
        "data_source",
        "as_of_date",
        "error_log",
    },
    # R-63: ab_tests is backend-accessed (BacklogClient.create_ab_test/get_ab_tests
    # via self.ab_tests → PostgresBackend.create/find) but was absent from this
    # map, so its real columns (test_name/variant_a/variant_b/status) would write
    # to `extra` and read back NULL — the same silent data-loss class as the
    # video_id bug. These match the live table.
    "ab_tests": {
        "niche_id",
        "test_name",
        "variant_a",
        "variant_b",
        "status",
    },
    "affiliate_clicks": {
        "niche_id",
        "product_id",
        "network",
        "affiliate_url",
        "referrer",
        "country",
        "platform_source",
        "blueprint_id",
        "channel_id",
    },
}


class PostgresBackend:
    """Storage backend backed by local PostgreSQL with psycopg3.

    Fully synchronous. Thread-safe via psycopg ConnectionPool.
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

    def _get_pool(self):
        """Lazy-initialize the connection pool."""
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    from psycopg_pool import ConnectionPool

                    self._pool = ConnectionPool(
                        self._dsn,
                        min_size=self._min_size,
                        max_size=self._max_size,
                        open=True,
                        configure=_configure_connection,
                    )
        return self._pool

    def close(self) -> None:
        """Close the connection pool cleanly.

        Call this during application teardown to prevent
        ConnectionPool.__del__ errors on garbage collection.
        """
        with self._pool_lock:
            if self._pool is not None:
                try:
                    self._pool.close()
                except Exception as exc:
                    logger.debug("Pool close error (non-critical): %s", exc)
                self._pool = None

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
        import re

        return bool(
            re.match(
                r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                record_id.strip(),
                re.I,
            )
        )

    # ── CREATE ──────────────────────────────────────────────────────

    def create(
        self,
        table: str,
        record: dict[str, Any],
        *,
        typecast: bool = False,
        niche_id: str | None = None,
    ) -> str:
        """Create a record. Returns the new UUID record ID.

        PR #517 (2026-06-24, SR-C tenant-isolation hardening):
        ``niche_id`` parameter accepted to ``SET LOCAL app.niche_id``
        before INSERT, so the row's tenant context matches the
        ``WITH CHECK`` clause on each RLS policy (added in migrations
        ``t0o1p2q3r4s5`` + ``u1p2q3r4s5t6``).

        When ``niche_id`` is provided:
          * ``SET LOCAL app.niche_id = niche_id`` runs in the same
            transaction as the INSERT (transaction-scoped, doesn't
            leak across pool connections).
          * If ``record`` carries a ``niche_id`` field, it MUST match
            the ``niche_id`` kwarg — otherwise ValueError (prevents
            silent tenant-spoofing via record-side forgery).
          * If ``record`` lacks ``niche_id``, it's injected from the
            kwarg before the split, so the row carries the right tenant
            tag.

        When ``niche_id`` is None (backward compat for the ~200 existing
        callers): falls through to admin-mode INSERT (RLS WITH CHECK
        clause permits ``app.niche_id IS NULL``). The audit (SR-C) flagged
        this as a correctness-bug risk for tenant #2 onboarding —
        callers that forget the kwarg silently create rows with no
        tenant binding. Migrate callers incrementally; once all sites
        pass ``niche_id``, this admin-mode fallback can flip to
        require-strict.
        """
        table = _validate_table(table)
        record_id = str(uuid.uuid4())

        # Validate + inject niche_id BEFORE splitting fields, so the
        # tenant tag lands in the correct SQL column (niche_id is
        # promoted across every multi-tenant table per PROMOTED_COLUMNS).
        if niche_id is not None:
            record_niche = record.get("niche_id")
            if record_niche is not None and record_niche != niche_id:
                raise ValueError(
                    f"niche_id kwarg '{niche_id}' does not match record's "
                    f"niche_id field '{record_niche}' — tenant-spoofing "
                    "attempt prevented (SR-C guard, PR #517). Either drop "
                    "the kwarg, drop the field, or align the two."
                )
            if record_niche is None:
                # Defensive: inject so the row's column matches the GUC.
                # Caller may have omitted niche_id from fields trusting
                # the kwarg to do the right thing — honour that.
                record = {**record, "niche_id": niche_id}

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

        pool = self._get_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                # SR-C: set tenant context BEFORE the INSERT so RLS
                # WITH CHECK evaluates correctly. SET LOCAL is
                # transaction-scoped, so it doesn't leak to other
                # pool consumers.
                if niche_id is not None:
                    cur.execute(
                        "SELECT set_config('app.niche_id', %s, true)",
                        (niche_id,),
                    )
                cur.execute(sql, values)
                row = cur.fetchone()
                conn.commit()
                return str(row[0]) if row else record_id

    # ── GET ─────────────────────────────────────────────────────────

    def get(
        self,
        table: str,
        record_id: str,
        *,
        niche_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Get a single record by ID (UUID or legacy SharePoint integer ID).

        PR #532 (2026-06-24, SR-A tenant binding): ``niche_id`` parameter
        accepted to ``SET LOCAL app.niche_id`` before SELECT, so RLS
        ``USING`` clauses filter to the correct tenant. Pre-PR-#532 the
        method ran with ``app.niche_id=''`` (admin mode) — any caller
        with a record_id could read across tenants by ID alone.

        When ``niche_id`` is None (backward compat for all existing
        callers): admin-mode SELECT (RLS treats empty GUC as "all
        tenants" per the existing policies). Migrate callers
        incrementally; once all sites pass ``niche_id``, the admin
        fallback can flip to require-strict.
        """
        table = _validate_table(table)
        from psycopg.rows import dict_row

        record_id = str(record_id).strip()

        pool = self._get_pool()
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT set_config('app.niche_id', %s, true)",
                    (niche_id or "",),
                )
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

    # ── FIND ────────────────────────────────────────────────────────

    def find(
        self,
        table: str,
        *,
        formula: str = "",
        niche_id: str = "",
        max_records: int | None = None,
        columns: list[str] | None = None,
        _skip_validation: bool = False,
    ) -> list[dict[str, Any]]:
        """Find records matching a formula filter with RLS niche isolation.

        Args:
            columns: Optional list of column names to select. When provided,
                uses ``SELECT col1, col2, ...`` instead of ``SELECT *``.
                The ``id``, ``extra``, ``created_at``, and ``updated_at``
                columns are always included automatically.
        """
        table = _validate_table(table)
        from psycopg.rows import dict_row

        from genlab_core.storage.formula_sql import formula_to_sql

        # R-49: pass the table's promoted columns so non-promoted filter fields
        # (e.g. analytics_id, candidate_id on publishing_analytics) are queried
        # via extra->> instead of a non-existent bare column — otherwise the
        # query errors, callers swallow it, and dedup silently writes duplicates.
        where_clause, params = formula_to_sql(formula, PROMOTED_COLUMNS.get(table))

        # Build column projection
        if columns is not None:
            # Always include structural columns needed by _row_to_record
            required = {"id", "extra", "created_at", "updated_at"}
            col_set = required | {c for c in columns}
            projection = ", ".join(_quote_col(c) for c in sorted(col_set))
        else:
            projection = "*"

        pool = self._get_pool()
        with pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT set_config('app.niche_id', %s, true)",
                    (niche_id or "",),
                )
                sql = f"SELECT {projection} FROM {table}"
                if where_clause:
                    # Convert $N positional params to %s for psycopg
                    import re

                    pg_where = re.sub(r"\$\d+", "%s", where_clause)
                    sql += f" WHERE {pg_where}"
                sql += " ORDER BY created_at DESC"
                if max_records:
                    sql += f" LIMIT {int(max_records)}"
                cur.execute(sql, params)
                rows = cur.fetchall()
                conn.commit()
                return [self._row_to_record(dict(r)) for r in rows]

    def all(
        self,
        table: str | None = None,
        *,
        formula: str = "",
        niche_id: str = "",
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Alias for find()."""
        if table is None:
            raise ValueError("table is required for PostgresBackend.all()")
        return self.find(table, formula=formula, niche_id=niche_id, max_records=max_records)

    # ── UPDATE ──────────────────────────────────────────────────────

    def update(
        self,
        table: str,
        record_id: str,
        fields: dict[str, Any],
        *,
        typecast: bool = False,
        niche_id: str | None = None,
    ) -> None:
        """Update fields on an existing record.

        PR #532 (2026-06-24, SR-A): ``niche_id`` parameter accepted to
        ``SET LOCAL app.niche_id`` before UPDATE — without this an
        attacker (or a buggy caller) with a record_id could mutate
        rows across tenants. RLS ``USING`` clauses filter rows to the
        tenant scope; admin-mode (``niche_id=None``) preserves
        backward compat.

        When ``fields`` carries an explicit ``niche_id`` AND the
        ``niche_id`` kwarg is provided, the two MUST match — otherwise
        ValueError to prevent silent tenant-spoofing on updates.
        Mirrors the ``create()`` guard (PR #517).
        """
        table = _validate_table(table)

        # SR-A guard: reject tenant-spoofing attempts on update —
        # a caller passing niche_id='gaming' but trying to update a
        # 'sports' row would silently land in admin-mode without this.
        if niche_id is not None and "niche_id" in fields:
            field_niche = fields["niche_id"]
            if field_niche is not None and field_niche != niche_id:
                raise ValueError(
                    f"niche_id kwarg '{niche_id}' does not match fields' "
                    f"niche_id '{field_niche}' — tenant-spoofing attempt "
                    "prevented (SR-A guard, PR #532)."
                )

        cols, extra = self._split_fields(table, fields)
        record_id = str(record_id).strip()

        pool = self._get_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.niche_id', %s, true)",
                    (niche_id or "",),
                )

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

    # ── CONDITIONAL CLAIM ───────────────────────────────────────────

    def claim_status(
        self,
        table: str,
        record_id: str,
        *,
        expected_status: str,
        new_status: str,
        niche_id: str | None = None,
    ) -> bool:
        """Atomically flip ``status`` only if it currently equals
        ``expected_status``.

        Returns ``True`` if this call claimed the row, ``False`` if another
        writer already moved it (lost the race). This is the double-publish
        guard (R-24): two concurrent publishers cannot both claim the same
        ``VISUAL_READY`` blueprint, because the conditional ``UPDATE … WHERE
        status = expected RETURNING id`` only matches for the first one.

        PR #532 (2026-06-24, SR-A): ``niche_id`` parameter accepted to
        ``SET LOCAL app.niche_id`` before the conditional UPDATE.
        Without this, a concurrent claim from a different tenant
        could potentially win the race — admin-mode flip is the
        worst-case behaviour. ``niche_id=None`` preserves backward
        compat (existing publishers don't break).
        """
        table = _validate_table(table)
        record_id = str(record_id).strip()
        id_pred = "id = %s::uuid" if self._is_uuid(record_id) else "extra->>'sp_id' = %s"

        pool = self._get_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.niche_id', %s, true)",
                    (niche_id or "",),
                )
                cur.execute(
                    f"UPDATE {table} SET status = %s, updated_at = now() "
                    f"WHERE {id_pred} AND status = %s RETURNING id",
                    (new_status, record_id, expected_status),
                )
                row = cur.fetchone()
                conn.commit()
                return row is not None

    # ── DELETE ──────────────────────────────────────────────────────

    def delete(
        self,
        table: str,
        record_id: str,
        *,
        niche_id: str | None = None,
    ) -> None:
        """Delete a record by ID.

        PR #532 (2026-06-24, SR-A): ``niche_id`` parameter accepted to
        ``SET LOCAL app.niche_id`` before DELETE — without this an
        attacker (or a buggy caller) with a record_id could erase
        rows across tenants. RLS ``USING`` clauses scope the DELETE to
        the tenant; admin-mode (``niche_id=None``) preserves backward
        compat for the ~5 existing delete call sites.

        Delete is the most-destructive of the three SR-A methods —
        cross-tenant deletion can't be undone, unlike get (read-only)
        or update (audit-recoverable). Once the migration is complete
        this admin fallback should be the first to flip to require-strict.
        """
        table = _validate_table(table)
        record_id = str(record_id).strip()

        pool = self._get_pool()
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.niche_id', %s, true)",
                    (niche_id or "",),
                )
                if self._is_uuid(record_id):
                    cur.execute(f"DELETE FROM {table} WHERE id = %s::uuid", (record_id,))
                else:
                    cur.execute(
                        f"DELETE FROM {table} WHERE extra->>'sp_id' = %s",
                        (record_id,),
                    )
                conn.commit()

    # ── BATCH CREATE ────────────────────────────────────────────────

    def batch_create(
        self,
        table: str,
        records: list[dict[str, Any]],
        *,
        niche_id: str | None = None,
    ) -> list[str]:
        """Create multiple records using pipeline mode for performance.

        PR #517 (2026-06-24, SR-C): ``niche_id`` parameter behaves
        identically to ``create()`` — see that method's docstring for
        the full SR-C tenant-isolation rationale.

        IMPORTANT: when ``niche_id`` is provided, EVERY record in the
        batch must share that tenant. Heterogeneous-tenant batches
        raise ValueError before any INSERT runs — atomic-validation
        prevents partial cross-tenant inserts that would leave the
        DB in a mixed-up state if the pipeline fails mid-batch.
        """
        table = _validate_table(table)
        if not records:
            return []

        # SR-C: pre-validate every record against the niche_id kwarg
        # BEFORE entering the pipeline — partial inserts on tenant
        # mismatch would be worse than failing fast.
        if niche_id is not None:
            for i, record in enumerate(records):
                record_niche = record.get("niche_id")
                if record_niche is not None and record_niche != niche_id:
                    raise ValueError(
                        f"niche_id kwarg '{niche_id}' does not match record[{i}]'s "
                        f"niche_id '{record_niche}' — heterogeneous-tenant batch "
                        "rejected before INSERT (SR-C guard, PR #517)."
                    )
            # Inject niche_id into any record missing it (mirrors
            # ``create()``'s defensive behaviour).
            records = [
                {**r, "niche_id": niche_id} if r.get("niche_id") is None else r for r in records
            ]

        ids = []
        pool = self._get_pool()
        with pool.connection() as conn:
            with conn.pipeline():
                with conn.cursor() as cur:
                    # SR-C: set tenant context once for the whole pipeline.
                    if niche_id is not None:
                        cur.execute(
                            "SELECT set_config('app.niche_id', %s, true)",
                            (niche_id,),
                        )
                    for record in records:
                        record_id = str(uuid.uuid4())
                        cols, extra = self._split_fields(table, record)
                        cols["extra"] = json.dumps(extra) if extra else "{}"

                        col_names = list(cols.keys())
                        quoted_names = [_quote_col(c) for c in col_names]
                        placeholders = ["%s"] * (len(col_names) + 1)
                        values = [record_id] + [cols[c] for c in col_names]

                        sql = (
                            f"INSERT INTO {table} (id, {', '.join(quoted_names)}) "
                            f"VALUES ({', '.join(placeholders)}) RETURNING id"
                        )
                        cur.execute(sql, values)
                        ids.append(record_id)
                conn.commit()
        return ids

    # ── INTERNAL ────────────────────────────────────────────────────

    @staticmethod
    def _row_to_record(row: dict[str, Any]) -> dict[str, Any]:
        """Convert a PostgreSQL row to the {id, fields, created_at, updated_at} format.

        ``created_at`` and ``updated_at`` are auto-managed SQL columns that we
        deliberately keep OUT of ``fields`` — putting them in ``fields`` would
        mean every write-back round-trip pushes them through ``_split_fields``
        into the ``extra`` JSONB (they aren't promoted columns), growing the
        JSONB indefinitely and shadowing the authoritative SQL values.

        Surfacing them as top-level keys instead (alongside ``id``) lets
        consumers that genuinely need lifecycle timestamps reach them
        without forcing the field-merge path. PushToBacklog + PreDownloadDedup
        both need ``created_at`` for the URL-dedup TTL filter (added 2026-06-
        23 — see those stages' ``_is_within_url_ttl`` helpers); before this
        change the helpers always read None and silently kept every blueprint
        in the dedup set (TTL no-op).
        """
        record_id = str(row.pop("id", ""))
        extra = row.pop("extra", None) or {}
        if isinstance(extra, str):
            extra = json.loads(extra)

        # Pop the auto-managed lifecycle columns BEFORE we iterate row.items()
        # so they don't slip into ``fields``. Surface them as ISO strings at
        # top level, matching the existing datetime → isoformat convention
        # below for promoted columns.
        def _iso(v: object) -> str | None:
            if v is None:
                return None
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            return str(v)

        created_at = _iso(row.pop("created_at", None))
        updated_at = _iso(row.pop("updated_at", None))

        fields: dict[str, Any] = {}
        for k, v in row.items():
            if isinstance(v, (datetime, date)):
                fields[k] = v.isoformat()
            else:
                fields[k] = v
        # Merge extra JSONB but give promoted SQL columns absolute priority.
        # Promoted columns are authoritative; JSONB only fills in keys that
        # aren't already present as promoted columns (handles stale JSONB values
        # and nulls left over from before a column was promoted).
        for k, v in extra.items():
            if k not in fields:  # Only use extra for keys NOT in promoted columns
                fields[k] = v

        return {
            "id": record_id,
            "fields": fields,
            "created_at": created_at,
            "updated_at": updated_at,
        }


class PostgresTableProxy:
    """Binds a PostgresBackend to a specific table name.

    Drop-in replacement for GraphTableProxy / SyncListProxy in
    BacklogClient and SyncBacklogClient attribute slots.
    """

    def __init__(self, backend: PostgresBackend, table: str) -> None:
        self._backend = backend
        self._table = table.lower()

    def find(
        self,
        table: str | None = None,
        *,
        formula: str = "",
        niche_id: str = "",
        max_records: int | None = None,
        columns: list[str] | None = None,
    ) -> list:
        return self._backend.find(
            table or self._table,
            formula=formula,
            niche_id=niche_id,
            max_records=max_records,
            columns=columns,
        )

    def all(
        self,
        table: str | None = None,
        *,
        formula: str = "",
        niche_id: str = "",
        max_records: int | None = None,
        columns: list[str] | None = None,
    ) -> list:
        return self._backend.find(
            table or self._table,
            formula=formula,
            niche_id=niche_id,
            max_records=max_records,
            columns=columns,
        )

    def get(
        self,
        record_id_or_table: str,
        record_id: str | None = None,
        *,
        niche_id: str | None = None,
    ):
        """Wrapper forward for PR #532 (SR-A): niche_id reaches
        backend.get's SET LOCAL step. Mirrors the create() forward
        from PR #525."""
        if record_id is not None:
            return self._backend.get(record_id_or_table, record_id, niche_id=niche_id)
        return self._backend.get(self._table, record_id_or_table, niche_id=niche_id)

    def create(
        self,
        table: str | None = None,
        fields: dict | None = None,
        *,
        typecast: bool = False,
        niche_id: str | None = None,
        **kwargs,
    ):
        """BacklogClient.create — thin wrapper over PostgresBackend.create.

        PR #525 (2026-06-24): forwards ``niche_id`` to the backend so
        SR-C tenant binding actually reaches the INSERT. Before this
        PR the wrapper accepted ``**kwargs`` (which silently swallowed
        ``niche_id=``) but never forwarded it — making PR #517's
        SR-C mechanism *dormant for every store-level caller*. The
        wrapper-forward unblocks per-store migration: store classes
        can now pass ``niche_id=`` and trust it lands at the
        backend's ``SET LOCAL app.niche_id`` step.
        """
        if fields is None and isinstance(table, dict):
            fields = table
            table = None
        return self._backend.create(
            table or self._table,
            fields or {},
            typecast=typecast,
            niche_id=niche_id,
        )

    def update(
        self,
        record_id_or_table: str,
        record_id_or_fields=None,
        fields: dict | None = None,
        *,
        typecast: bool = False,
        niche_id: str | None = None,
    ):
        """Wrapper forward for PR #532 (SR-A): niche_id reaches
        backend.update's SET LOCAL + tenant-spoofing guard.
        Identical pattern to create() forward from PR #525."""
        if isinstance(record_id_or_fields, dict):
            return self._backend.update(
                self._table,
                record_id_or_table,
                record_id_or_fields,
                typecast=typecast,
                niche_id=niche_id,
            )
        if fields is not None:
            return self._backend.update(
                record_id_or_table,
                record_id_or_fields,
                fields,
                typecast=typecast,
                niche_id=niche_id,
            )
        raise ValueError("update() requires fields dict")

    def delete(
        self,
        record_id_or_table: str,
        record_id: str | None = None,
        *,
        niche_id: str | None = None,
    ):
        """Wrapper forward for PR #532 (SR-A): niche_id reaches
        backend.delete's SET LOCAL step. Delete is the most-
        destructive of the SR-A methods — cross-tenant deletion is
        irreversible — so the forwarding here matters more than for
        get() or update()."""
        if record_id is not None:
            return self._backend.delete(record_id_or_table, record_id, niche_id=niche_id)
        return self._backend.delete(self._table, record_id_or_table, niche_id=niche_id)

    def batch_create(
        self,
        table: str | None = None,
        records: list | None = None,
        *,
        niche_id: str | None = None,
    ):
        """BacklogClient.batch_create — thin wrapper over PostgresBackend.batch_create.

        PR #525 (2026-06-24): forwards ``niche_id`` to the backend.
        See :meth:`create` for the SR-C wrapper-forward rationale —
        identical pattern. ``niche_id=None`` preserves the existing
        admin-mode fallback so unmigrated callers keep working
        unchanged.
        """
        if records is None and isinstance(table, list):
            records = table
            table = None
        return self._backend.batch_create(
            table or self._table,
            records or [],
            niche_id=niche_id,
        )

    def claim_status(
        self,
        record_id: str,
        *,
        expected_status: str,
        new_status: str,
        niche_id: str | None = None,
    ) -> bool:
        """Atomic conditional status flip — see PostgresBackend.claim_status (R-24).

        PR #532 (SR-A): forwards ``niche_id`` so the SET LOCAL fires
        inside the backend. Without this, a publisher claiming a
        VISUAL_READY row runs in admin mode and the RLS USING clause
        doesn't scope the SELECT FOR UPDATE to the right tenant.
        """
        return self._backend.claim_status(
            self._table,
            record_id,
            expected_status=expected_status,
            new_status=new_status,
            niche_id=niche_id,
        )
