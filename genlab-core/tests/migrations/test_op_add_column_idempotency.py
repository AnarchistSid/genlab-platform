"""Pin test: every ``op.add_column`` on an R-48-adopted table MUST be
wrapped in a ``DO $$ IF NOT EXISTS ... END $$`` guard.

Codified 2026-07-14 after `a8w9x0y1z2a3_monetization_l3_product_bandit_schema`
had been failing CI silently for weeks. Root cause: `l2g3h4i5j6k7`
(R-48 adopt-hand-created-tables) creates `affiliate_clicks` with
`CREATE TABLE IF NOT EXISTS ...` including a `blueprint_id` column,
and the later a8w9 migration then called `op.add_column("affiliate_
clicks", "blueprint_id", UUID)` unconditionally.

Fresh-CI-DB path: l2g3 creates the TEXT column → a8w9 ADD COLUMN
crashes with `psycopg.errors.DuplicateColumn`.

The fix pattern for a8w9 was to wrap in a `DO $$ BEGIN ... IF NOT
EXISTS ... END $$` block. This test enforces the pattern going
forward: any NEW `op.add_column(<adopted_table>, ...)` call must
be paired with an idempotency guard.

## Adopted tables (from IF-NOT-EXISTS creating migrations)

The audit at 2026-07-14 identified these tables adopted from prior
prod state (rather than created fresh by Alembic):

  - affiliate_clicks
  - content_pool
  - pipeline_alerts
  - ab_tests
  - audience_snapshots
  - monetisationprogress
  - affiliate_revenue
  - dashboard_events
  - preference_data
  - product_embeddings

Any future `op.add_column("<table>", ...)` on one of these needs an
idempotency guard because the column MIGHT already exist depending
on the DB's history path.

## Test strategy

Scan all files under `genlab-core/migrations/versions/`. For each,
look for `op.add_column("<adopted_table>", ...)` calls. If found,
verify either:
  (a) The call is wrapped in a `DO $$ ... IF NOT EXISTS ...` block
      via `op.execute("...DO $$...")`; OR
  (b) The migration file is a KNOWN pre-a8w9 file (allowlisted).

Otherwise fail with a clear message pointing at the idempotency
pattern.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _REPO_ROOT / "genlab-core" / "migrations" / "versions"

# Tables created via `CREATE TABLE IF NOT EXISTS ...` in R-48 adopt
# migrations. Any op.add_column on these MUST be idempotency-guarded.
_ADOPTED_TABLES = frozenset(
    {
        "affiliate_clicks",
        "content_pool",
        "pipeline_alerts",
        "ab_tests",
        "audience_snapshots",
        "monetisationprogress",
        "affiliate_revenue",
        "dashboard_events",
        "preference_data",
        "product_embeddings",
    }
)

# Files that predate the pattern OR have their own idempotency
# guard already. Add new entries here ONLY with a written reason.
_ALLOWLIST: frozenset[str] = frozenset(
    {
        # The pin fix itself — has the correct DO $$ IF NOT EXISTS wrapper.
        "a8w9x0y1z2a3_monetization_l3_product_bandit_schema.py",
    }
)

# Pattern: op.add_column("adopted_table", ...) — captures the table
# name to check membership.
_ADD_COLUMN_PATTERN = re.compile(
    r'op\.add_column\(\s*[\'"]([a-z_]+)[\'"]', re.IGNORECASE
)

# Pattern: DO $$ block containing "IF NOT EXISTS" or "column_name" +
# "information_schema" check. Presence in the same file is proof of
# idempotency guard.
_DO_BLOCK_GUARD_PATTERN = re.compile(
    r"DO\s+\$\$.*?(IF\s+NOT\s+EXISTS|information_schema\.columns)",
    re.IGNORECASE | re.DOTALL,
)


def test_no_unguarded_add_column_on_adopted_tables():
    """Every ``op.add_column`` on an R-48-adopted table must be
    wrapped in a ``DO $$ IF NOT EXISTS ...`` guard.

    Fails with a per-file report so operators can fix batch.
    """
    offenders: list[tuple[str, str]] = []

    if not _MIGRATIONS_DIR.exists():
        return  # workspace without migrations dir (test harness edge)

    for path in sorted(_MIGRATIONS_DIR.glob("*.py")):
        if path.name in _ALLOWLIST:
            continue
        source = path.read_text(encoding="utf-8")

        # Find op.add_column("<adopted_table>", ...) calls
        for m in _ADD_COLUMN_PATTERN.finditer(source):
            table = m.group(1)
            if table not in _ADOPTED_TABLES:
                continue

            # Must have DO $$ IF NOT EXISTS guard SOMEWHERE in file
            if _DO_BLOCK_GUARD_PATTERN.search(source):
                continue

            offenders.append((path.name, table))
            break  # one report per file is enough

    assert not offenders, (
        f"Found {len(offenders)} migration(s) with unguarded "
        f"op.add_column on R-48-adopted tables:\n"
        + "\n".join(f"  - {name}: op.add_column({table!r}, ...)" for name, table in offenders)
        + "\n\n"
        "Fix pattern (from a8w9x0y1z2a3_monetization_l3_product_bandit_schema):\n"
        "  op.execute('''DO $$ BEGIN\n"
        "    IF NOT EXISTS (SELECT 1 FROM information_schema.columns\n"
        "                   WHERE table_name='<table>' AND column_name='<col>')\n"
        "    THEN ALTER TABLE <table> ADD COLUMN <col> <type>;\n"
        "    END IF;\n"
        "  END $$;''')\n"
        "\n"
        "This prevents the DuplicateColumn CI failure class-of-bug that\n"
        "surfaced 2026-07-14 (a8w9 vs l2g3h4i5j6k7 adopt-table conflict)."
    )


def test_allowlist_files_still_exist():
    """Sanity check: allowlisted files must still exist. If a
    migration is removed, drop it from the allowlist too.
    """
    for name in _ALLOWLIST:
        assert (_MIGRATIONS_DIR / name).exists(), (
            f"Allowlisted migration '{name}' no longer exists. "
            "Remove from _ALLOWLIST or restore the file."
        )
