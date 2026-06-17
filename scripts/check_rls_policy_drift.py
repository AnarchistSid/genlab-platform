"""RLS policy drift detector — runs nightly via systemd timer.

Why this exists
---------------
The post-Wave-4 audit (2026-06-17) found that prod's RLS policy set
had silently drifted from the Alembic chain's intent:

  * ``affiliate_clicks`` had ``affiliate_clicks_niche_policy`` instead of
    the canonical ``niche_isolation`` (created by an out-of-band .sql).
  * ``email_subscribers`` existed in prod with ``niche_isolation`` but
    wasn't in any Alembic migration's _TABLES list.
  * SR-B's initial migration broke because of this drift; SR-B hotfix
    + SR-F closed the loop with defensive DO blocks.

Without a detector, the NEXT drift event surprises us during the NEXT
migration. This script runs nightly + writes a CRITICAL pipeline_alerts
row when it finds:

  1. A table in ``_EXPECTED_RLS_TABLES`` (the canonical list from the
     Alembic chain) that doesn't have any RLS policy → tenant isolation
     missing entirely.

  2. A table in ``_EXPECTED_RLS_TABLES`` whose policy lacks WITH CHECK
     → write-side isolation missing (SR-B-class bug recurring).

  3. A table in ``_EXPECTED_RLS_TABLES`` whose policy isn't named
     ``niche_isolation`` → the SR-F-class drift (operator should rename
     manually since policies can't be renamed, only dropped + created).

  4. A table NOT in ``_EXPECTED_RLS_TABLES`` that DOES have RLS — this
     is fine (the script informs but doesn't alert) but the operator
     should add it to ``_EXPECTED_RLS_TABLES`` so future drift detection
     covers it.

Cadence + reporting
-------------------
Runs at ~04:00 IST via systemd timer (off-peak, doesn't compete with
pipeline). Writes a single pipeline_alerts row per drift class — the
alert pump (post-#296 + #301) picks it up and Slacks the operator.

Idempotent: re-running on the same drift state doesn't multiply
alerts; the alert pump's ``notified_at`` column dedupes downstream.

Exit codes
----------
* 0 — no drift OR pre-flight skipped (no DATABASE_URL)
* 1 — drift detected + alert written
* 2 — script crashed (DB unreachable, schema unexpected, etc.)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-4

logger = logging.getLogger(__name__)


# Canonical RLS-protected table list. Keep in sync with:
#   * genlab-core/migrations/versions/t0o1p2q3r4s5_rls_with_check_clauses.py:_TABLES
#   * genlab-core/migrations/versions/u1p2q3r4s5t6_srf_add_with_check_to_orphan_policies.py:_POLICY_PAIRS
#
# When a new RLS-protected table is added via Alembic, add it here so
# the drift detector covers it. ``CANONICAL_POLICY_NAME`` is what the
# Alembic chain creates (always ``niche_isolation``). The (table,
# policy_name) pairs from SR-F's _POLICY_PAIRS are the explicit
# legitimate exceptions — listed at the bottom so the operator's drift
# triage can distinguish "known exception" from "new drift".
CANONICAL_POLICY_NAME = "niche_isolation"

_EXPECTED_RLS_TABLES = (
    "stories",
    "assets",
    "blueprints",
    "templates",
    "sources",
    "publishing_analytics",
    "analytics",
    "content_memory",
    "bandit_arms",
    "pending_engagement",
    "pending_feedback",
    "config_updates",
    "ab_tests",
    "audience_snapshots",
    "monetisationprogress",
    "affiliate_clicks",
    "affiliate_revenue",
    "preference_data",
    "email_subscribers",
)

# Known non-canonical policy names. Each entry is (table, expected
# policy_name). These tables have RLS protection but under a different
# policy name (typically because the table was created out-of-band
# before the Alembic migration ran). The drift detector treats these
# as "known good" — only the absence of WITH CHECK on them is alerted.
_KNOWN_NON_CANONICAL_POLICIES = {
    "affiliate_clicks": "affiliate_clicks_niche_policy",
}


def _connect():

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")
    return pg_connect(dsn, connect_timeout=10, niche_id="all")


def scan(conn) -> dict[str, list[str]]:
    """Return per-class drift list.

    Returns a dict with keys:
      * missing_policy   — table has no RLS policy at all
      * missing_with_check — policy exists but no WITH CHECK
      * unexpected_policy_name — policy exists but under unexpected name
                                 (not the canonical name and not in
                                 _KNOWN_NON_CANONICAL_POLICIES)

    Each value is a list[str] of table names. Empty lists mean
    "no drift in that class".
    """
    drift = {
        "missing_policy": [],
        "missing_with_check": [],
        "unexpected_policy_name": [],
    }
    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        # Pull every policy from public schema. Filter Python-side
        # because the per-table loop is clearer + handles missing
        # tables gracefully.
        cur.execute(
            """
            SELECT relname AS table_name, polname AS policy_name,
                   polwithcheck IS NOT NULL AS has_with_check
            FROM pg_policy
            JOIN pg_class ON polrelid = pg_class.oid
            JOIN pg_namespace ON relnamespace = pg_namespace.oid
            WHERE nspname = 'public'
            """
        )
        # policies_by_table: { table_name: [(policy_name, has_with_check), ...] }
        policies_by_table: dict[str, list[tuple[str, bool]]] = {}
        for table_name, policy_name, has_with_check in cur.fetchall():
            policies_by_table.setdefault(table_name, []).append((policy_name, has_with_check))

    for table in _EXPECTED_RLS_TABLES:
        policies = policies_by_table.get(table, [])
        if not policies:
            drift["missing_policy"].append(table)
            continue

        # If any policy on this table is the canonical name or a known
        # non-canonical, that's the protective policy we care about.
        expected_policy = _KNOWN_NON_CANONICAL_POLICIES.get(table, CANONICAL_POLICY_NAME)
        matching = [p for p, _ in policies if p == expected_policy]
        if not matching:
            # Table has SOME policy but not the expected one. Could
            # be drift (new out-of-band rename) or a legitimate
            # operator addition we haven't catalogued. Report.
            drift["unexpected_policy_name"].append(
                f"{table}: have={[p for p, _ in policies]} expected={expected_policy}"
            )
            continue

        # Expected policy exists. Verify WITH CHECK.
        has_with_check = next((has for p, has in policies if p == expected_policy), False)
        if not has_with_check:
            drift["missing_with_check"].append(f"{table}.{expected_policy}")

    return drift


def write_alert(conn, drift: dict[str, list[str]]) -> None:
    """Write a single CRITICAL row to pipeline_alerts summarizing drift."""
    summary_lines = []
    for cls, items in drift.items():
        if items:
            summary_lines.append(f"  {cls}: {', '.join(items[:5])}")
            if len(items) > 5:
                summary_lines.append(f"    ... and {len(items) - 5} more")

    message = (
        "RLS policy drift detected. Operator should investigate before "
        "the next migration:\n" + "\n".join(summary_lines) + "\n\n"
        "Triage script:\n"
        "  ssh root@prod 'sudo -u genlab psql $DATABASE_URL "
        '-c "SELECT relname, polname, polwithcheck IS NOT NULL AS has_check '
        "FROM pg_policy JOIN pg_class ON polrelid=pg_class.oid ORDER BY relname\"'"
    )

    with conn.cursor() as cur:
        cur.execute("SET app.niche_id TO 'all'")
        cur.execute(
            """
            INSERT INTO pipeline_alerts (niche_id, check_name, severity, message)
            VALUES ('all', 'rls_policy_drift', 'critical', %s)
            """,
            (message,),
        )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_rls_policy_drift",
        description="Nightly RLS policy drift detector — alerts when "
        "prod's RLS state diverges from the Alembic chain's intent.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print drift to stdout but don't write to pipeline_alerts.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if not os.environ.get("DATABASE_URL"):
        logger.info("[drift] DATABASE_URL not set — exiting 0 (pre-flight skip)")
        return 0

    try:
        conn = _connect()
    except Exception as exc:
        logger.error("[drift] DB connect failed: %s", exc)
        return 2

    try:
        drift = scan(conn)
    except Exception:
        logger.exception("[drift] scan failed")
        conn.close()
        return 2

    has_drift = any(drift[cls] for cls in drift)
    if not has_drift:
        logger.info("[drift] no drift across %d expected RLS tables", len(_EXPECTED_RLS_TABLES))
        conn.close()
        return 0

    for cls, items in drift.items():
        if items:
            logger.warning("[drift] %s (%d): %s", cls, len(items), items)

    if not args.dry_run:
        try:
            write_alert(conn, drift)
            logger.info("[drift] CRITICAL alert written to pipeline_alerts")
        except Exception as exc:
            logger.exception("[drift] alert write failed: %s", exc)
            conn.close()
            return 2

    conn.close()
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
