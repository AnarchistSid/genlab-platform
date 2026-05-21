"""SharePoint → PostgreSQL data migration script.

Reads all records from SharePoint via BacklogClient's GraphTableProxy
instances and writes them to PostgreSQL via raw asyncpg INSERT ... ON
CONFLICT DO NOTHING to handle duplicates gracefully.

Usage:
    uv run python -m genlab_core.storage.migrate_table --all
    uv run python -m genlab_core.storage.migrate_table --table blueprints
    uv run python -m genlab_core.storage.migrate_table --table stories --table blueprints
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from typing import Any

from genlab_core.storage.postgres import (
    PROMOTED_COLUMNS,
    PostgresBackend,
    _quote_col,
    _validate_table,
)

logger = logging.getLogger(__name__)

# ── SharePoint list name → PostgreSQL table mapping ──────────────────

SP_LIST_TO_PG_TABLE: dict[str, str] = {
    "Stories": "stories",
    "Blueprints": "blueprints",
    "Templates": "templates",
    "Assets": "assets",
    "Sources": "sources",
    "Publishing_Analytics": "publishing_analytics",
    "Analytics": "analytics",
    "Content_Memory": "content_memory",
    "BanditArms": "bandit_arms",
    "PendingEngagement": "pending_engagement",
    "PendingFeedback": "pending_feedback",
}

# Reverse mapping: postgres table → SharePoint list name
PG_TABLE_TO_SP_LIST: dict[str, str] = {v: k for k, v in SP_LIST_TO_PG_TABLE.items()}

# The unique business key per table used for ON CONFLICT.
# Tables without a unique business key use the SharePoint record ID stored
# in extra->'sp_id' — we add a fallback dedup check for those.
TABLE_UNIQUE_KEY: dict[str, str | None] = {
    "blueprints": "candidate_id",
    "stories": "story_id",
    "assets": "asset_id",
    "content_memory": "content_hash",
    "bandit_arms": "arm_id",
    "pending_feedback": "task_id",
    "templates": "template_id",
    # These tables have no unique business key column:
    "sources": None,
    "publishing_analytics": None,
    "analytics": None,
    "pending_engagement": None,
}


def flatten_sp_record(record: dict[str, Any]) -> dict[str, Any]:
    """Convert a SharePoint record {id, fields, createdTime} to a flat dict.

    The SharePoint record ID is preserved as ``sp_id`` so we can track
    provenance and deduplicate tables that lack a unique business key.
    """
    sp_id = record.get("id", "")
    fields = record.get("fields", {})

    flat: dict[str, Any] = {}
    for key, value in fields.items():
        # Skip empty values to keep the record clean
        if value is None:
            continue
        flat[key] = value

    # Ensure niche_id is never null (required by PostgreSQL NOT NULL + RLS)
    if "niche_id" not in flat or not flat["niche_id"]:
        flat["niche_id"] = "unknown"

    # Preserve SharePoint record ID for provenance
    flat["sp_id"] = str(sp_id)

    return flat


def _build_insert_sql(
    table: str,
    record: dict[str, Any],
) -> tuple[str, list[Any]]:
    """Build an INSERT ... ON CONFLICT DO NOTHING statement.

    Returns (sql, values) tuple. Uses the promoted column split from
    PostgresBackend to match the table schema exactly.
    """
    table = _validate_table(table)
    promoted = PROMOTED_COLUMNS.get(table, set())
    cols: dict[str, Any] = {}
    extra: dict[str, Any] = {}

    from datetime import date, datetime

    def _serialize(val: Any) -> Any:
        """Make a value JSON-safe."""
        if isinstance(val, (datetime, date)):
            return val.isoformat()
        if isinstance(val, dict):
            return {k2: _serialize(v2) for k2, v2 in val.items()}
        if isinstance(val, list):
            return [_serialize(item) for item in val]
        return val

    # TIMESTAMPTZ columns that asyncpg expects as datetime objects
    _TS_COLS = {
        "scheduled_for",
        "published_at",
        "collected_at",
        "reviewed_at",
        "first_seen",
        "last_seen",
        "last_fetched",
        "scheduled_at",
        "publish_time",
        "metrics_fetched",
        "created_at",
        "updated_at",
    }

    def _parse_datetime(val: Any) -> Any:
        """Parse ISO datetime strings to datetime objects for TIMESTAMPTZ columns."""
        if isinstance(val, (datetime, date)):
            return val
        if isinstance(val, str) and val:
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return val
        return val

    for k, v in record.items():
        if k in promoted:
            if isinstance(v, dict):
                cols[k] = json.dumps(_serialize(v))
            elif k in _TS_COLS:
                cols[k] = _parse_datetime(v)
            elif isinstance(v, (datetime, date)):
                cols[k] = v
            else:
                cols[k] = v
        else:
            extra[k] = _serialize(v)

    cols["extra"] = json.dumps(extra) if extra else "{}"

    record_id = str(uuid.uuid4())
    col_names = list(cols.keys())
    quoted_names = [_quote_col(c) for c in col_names]
    placeholders = [f"${i + 2}" for i in range(len(col_names))]
    values = [cols[c] for c in col_names]

    unique_key = TABLE_UNIQUE_KEY.get(table)

    if unique_key and unique_key in cols:
        # Use ON CONFLICT on the unique business key
        conflict_clause = f"ON CONFLICT ({_quote_col(unique_key)}) DO NOTHING"
    else:
        # No unique key — use the primary key 'id' to avoid outright
        # duplicates, but since we generate a new UUID each time we need
        # a different approach. We'll check sp_id in extra JSONB before
        # inserting (handled by the caller via _record_exists).
        conflict_clause = "ON CONFLICT (id) DO NOTHING"

    sql = (
        f"INSERT INTO {table} (id, {', '.join(quoted_names)}) "
        f"VALUES ($1, {', '.join(placeholders)}) "
        f"{conflict_clause}"
    )

    return sql, [record_id] + values


async def _record_exists(conn, table: str, record: dict[str, Any]) -> bool:
    """Check if a record already exists by its unique business key.

    For tables without a unique key, checks the extra JSONB for sp_id match.
    """
    table = _validate_table(table)
    unique_key = TABLE_UNIQUE_KEY.get(table)

    if unique_key and unique_key in record:
        row = await conn.fetchrow(
            f"SELECT 1 FROM {table} WHERE {_quote_col(unique_key)} = $1",
            record[unique_key],
        )
        return row is not None

    # Fallback: check sp_id in the extra JSONB column
    sp_id = record.get("sp_id", "")
    if sp_id:
        row = await conn.fetchrow(
            f"SELECT 1 FROM {table} WHERE extra->>'sp_id' = $1",
            sp_id,
        )
        return row is not None

    return False


def _init_backlog_client():
    """Initialize BacklogClient connected to SharePoint.

    Requires AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET,
    and SHAREPOINT_SITE_ID environment variables.
    """
    from genlab_core.http.backlog_client import BacklogClient

    return BacklogClient()


def _get_sp_proxy(client, sp_list_name: str):
    """Get the GraphTableProxy for a SharePoint list from BacklogClient.

    Maps list names to BacklogClient proxy attributes.
    """
    attr_map: dict[str, str] = {
        "Stories": "stories",
        "Blueprints": "blueprints",
        "Templates": "templates",
        "Assets": "assets",
        "Sources": "sources",
        "Publishing_Analytics": "publishing_analytics",
        "Analytics": "analytics",
        "Content_Memory": "content_memory",
        "BanditArms": "bandit_arms",
        "PendingEngagement": "pending_engagement",
        "PendingFeedback": "pending_feedback",
    }
    attr = attr_map.get(sp_list_name)
    if not attr:
        raise ValueError(f"Unknown SharePoint list: {sp_list_name}")

    proxy = getattr(client, attr, None)
    if proxy is None:
        raise ValueError(
            f"BacklogClient has no proxy for '{sp_list_name}' (attribute '{attr}' is None)"
        )
    return proxy


def migrate_table(
    pg: PostgresBackend,
    sp_records: list[dict[str, Any]],
    pg_table: str,
) -> dict[str, int]:
    """Migrate records from SharePoint format to PostgreSQL.

    Args:
        pg: PostgresBackend instance (used for its pool/loop).
        sp_records: List of SharePoint records ({id, fields}).
        pg_table: Target PostgreSQL table name.

    Returns:
        Dict with 'total', 'migrated', 'skipped', 'errors' counts.
    """
    pg_table = _validate_table(pg_table)

    total = len(sp_records)
    migrated = 0
    skipped = 0
    errors = 0

    if total == 0:
        logger.info("No records to migrate for %s", pg_table)
        return {"total": 0, "migrated": 0, "skipped": 0, "errors": 0}

    pg._ensure_pool()

    async def _migrate_all():
        nonlocal migrated, skipped, errors
        pool = pg._get_pool()
        async with pool.acquire() as conn:
            # Admin mode: bypass RLS for migration
            await conn.execute("SELECT set_config('app.niche_id', '', true)")

            for i, sp_record in enumerate(sp_records, 1):
                flat = flatten_sp_record(sp_record)

                try:
                    # Check for existing record first
                    exists = await _record_exists(conn, pg_table, flat)
                    if exists:
                        skipped += 1
                        if i % 100 == 0 or i == total:
                            logger.info(
                                "Migrated %d/%d %s (skipped existing)...",
                                i,
                                total,
                                pg_table,
                            )
                        continue

                    sql, values = _build_insert_sql(pg_table, flat)
                    await conn.execute(sql, *values)
                    migrated += 1

                except Exception as exc:
                    errors += 1
                    sp_id = sp_record.get("id", "?")
                    logger.warning(
                        "Error migrating %s record sp_id=%s: %s",
                        pg_table,
                        sp_id,
                        exc,
                    )

                if i % 100 == 0 or i == total:
                    logger.info(
                        "Migrated %d/%d %s...",
                        i,
                        total,
                        pg_table,
                    )

    assert pg._loop is not None
    pg._loop.run_until_complete(_migrate_all())

    return {
        "total": total,
        "migrated": migrated,
        "skipped": skipped,
        "errors": errors,
    }


def run_migration(
    tables: list[str],
    *,
    dry_run: bool = False,
) -> dict[str, dict[str, int]]:
    """Run migration for specified tables.

    Args:
        tables: List of PostgreSQL table names to migrate.
        dry_run: If True, only read from SharePoint and report counts.

    Returns:
        Dict mapping table name to migration stats.
    """
    logger.info("Initializing BacklogClient (SharePoint connection)...")
    client = _init_backlog_client()

    logger.info("Initializing PostgresBackend...")
    password = os.environ.get("POSTGRES_PASSWORD", "")
    pg = PostgresBackend(password=password)

    results: dict[str, dict[str, int]] = {}

    for pg_table in tables:
        sp_list_name = PG_TABLE_TO_SP_LIST.get(pg_table)
        if not sp_list_name:
            logger.error("Unknown table: %s", pg_table)
            results[pg_table] = {"total": 0, "migrated": 0, "skipped": 0, "errors": 1}
            continue

        logger.info("=== Migrating %s (SharePoint: %s) ===", pg_table, sp_list_name)

        try:
            proxy = _get_sp_proxy(client, sp_list_name)
        except ValueError as exc:
            logger.error("Failed to get proxy: %s", exc)
            results[pg_table] = {"total": 0, "migrated": 0, "skipped": 0, "errors": 1}
            continue

        logger.info("Fetching all records from SharePoint %s...", sp_list_name)
        try:
            sp_records = proxy.all()
        except Exception as exc:
            logger.error("Failed to fetch from SharePoint %s: %s", sp_list_name, exc)
            results[pg_table] = {"total": 0, "migrated": 0, "skipped": 0, "errors": 1}
            continue

        logger.info("Fetched %d records from %s", len(sp_records), sp_list_name)

        if dry_run:
            logger.info("[DRY RUN] Would migrate %d records to %s", len(sp_records), pg_table)
            results[pg_table] = {
                "total": len(sp_records),
                "migrated": 0,
                "skipped": 0,
                "errors": 0,
            }
            continue

        stats = migrate_table(pg, sp_records, pg_table)
        results[pg_table] = stats

        logger.info(
            "%s complete: %d total, %d migrated, %d skipped, %d errors",
            pg_table,
            stats["total"],
            stats["migrated"],
            stats["skipped"],
            stats["errors"],
        )

    return results


ALL_TABLES: list[str] = list(PG_TABLE_TO_SP_LIST.keys())


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate data from SharePoint to PostgreSQL",
    )
    parser.add_argument(
        "--table",
        action="append",
        dest="tables",
        choices=ALL_TABLES,
        help="Table(s) to migrate. Can be specified multiple times.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="migrate_all",
        help="Migrate all tables.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read from SharePoint and report counts without writing to PostgreSQL.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging.",
    )

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.tables and not args.migrate_all:
        parser.error("Specify --table TABLE_NAME or --all")

    tables = ALL_TABLES if args.migrate_all else (args.tables or [])

    logger.info("Starting migration for tables: %s", ", ".join(tables))
    results = run_migration(tables, dry_run=args.dry_run)

    # Print summary
    print("\n" + "=" * 60)
    print("MIGRATION SUMMARY")
    print("=" * 60)
    total_migrated = 0
    total_skipped = 0
    total_errors = 0
    for table, stats in results.items():
        total_migrated += stats["migrated"]
        total_skipped += stats["skipped"]
        total_errors += stats["errors"]
        status = "OK" if stats["errors"] == 0 else "ERRORS"
        print(
            f"  {table:<25s} {stats['total']:>6d} total, "
            f"{stats['migrated']:>6d} migrated, "
            f"{stats['skipped']:>6d} skipped, "
            f"{stats['errors']:>6d} errors  [{status}]"
        )
    print("-" * 60)
    print(
        f"  {'TOTAL':<25s} "
        f"{total_migrated:>6d} migrated, "
        f"{total_skipped:>6d} skipped, "
        f"{total_errors:>6d} errors"
    )
    print("=" * 60)

    if total_errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
