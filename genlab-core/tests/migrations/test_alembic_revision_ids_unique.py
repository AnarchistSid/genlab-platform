"""2026-08-12: pin that no two alembic migrations share the same
revision id.

Motivating incident: `a8w9x0y1z2a3_monetization_l3_product_bandit_schema.py`
and `a8w9x0y1z2a3_blueprints_action_taken_source.py` both declared
`revision = "a8w9x0y1z2a3"`. Alembic silently picked one (the
action_taken_source one won by filesystem order); the monetization
one was NEVER applied to prod.

35-day silent-fail: every affiliate click INSERT hit
    column "commission_pct" of relation "affiliate_clicks" does not exist
and was swallowed by log_click's outer try/except. Result: 0 rows
in affiliate_clicks for the entire window, entire product-bandit
reward loop dead. This pin fires at CI on any future duplicate.

Detection heuristic beyond CI: alembic emits `UserWarning: Revision
X is present more than once` on any command touching the duplicate.
`grep -r "^revision = " migrations/versions/ | sort | uniq -c`
also surfaces duplicates.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path


class TestAlembicRevisionIdsUnique:
    def test_no_duplicate_revision_ids(self):
        """Every migration file's `revision = "..."` string must be
        unique across the whole versions/ directory. Alembic silently
        picks one when duplicates exist, which is a silent-fail class:
        the loser is never applied, and callers of the "missing"
        schema hit `column does not exist` errors that get swallowed
        by defensive try/except blocks downstream."""
        versions_dir = (
            Path(__file__).resolve().parents[2]
            / "migrations"
            / "versions"
        )
        assert versions_dir.exists(), (
            f"migrations/versions/ not at expected path: {versions_dir}"
        )

        revision_pattern = re.compile(r'^revision\s*=\s*"([^"]+)"', re.MULTILINE)
        revisions_seen: list[tuple[str, str]] = []

        for py_file in sorted(versions_dir.glob("*.py")):
            content = py_file.read_text()
            for match in revision_pattern.finditer(content):
                revisions_seen.append((match.group(1), py_file.name))

        # Group by revision id and find any that appear more than once
        rev_counts = Counter(rev for rev, _ in revisions_seen)
        duplicates = {rev: count for rev, count in rev_counts.items() if count > 1}

        if duplicates:
            # Build a helpful error message: for each duplicate rev,
            # list all files claiming it
            details_lines = []
            for dup_rev in duplicates:
                files = sorted(
                    fname for rev, fname in revisions_seen if rev == dup_rev
                )
                details_lines.append(f"  {dup_rev!r}: {files}")
            details = "\n".join(details_lines)
            raise AssertionError(
                f"Duplicate alembic revision id(s) found. Alembic will "
                f"silently pick one, leaving the other(s) unapplied — a "
                f"35-day silent-fail bit us with `a8w9x0y1z2a3` (see "
                f"docstring). Rename each duplicate to a unique id and "
                f"rechain any successors:\n{details}"
            )
