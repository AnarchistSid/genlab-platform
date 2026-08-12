"""Pin: deploy.sh detects rename-based new migrations, not just adds.

Motivating incident: 2026-08-12 commit a8242c5c renamed
`a8w9x0y1z2a3_monetization_l3_product_bandit_schema.py` to
`l3mon202608_monetization_l3_product_bandit_schema.py` to break a
duplicate-revision-id collision. The renamed file carries a NEW
revision id (`l3mon202608`) that alembic hasn't seen -> needs to
run on next `alembic upgrade head`.

deploy.sh used `git diff --diff-filter=A` which only detects ADDED
files. Renames show as `R` and were silently skipped, so the deploy
finished with "No new migrations; skipping alembic step" and prod
schema stayed broken. Manual alembic run was needed.

Fix: `--diff-filter=AR` (Added OR Renamed). `--name-only` gives the
destination filename for renames, which is what alembic sees on disk.

This pin locks the fix so a future edit can't quietly regress it.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_SH = REPO_ROOT / "scripts" / "deploy.sh"


class TestDeployMigrationDetectionCatchesRenames:
    def test_uses_diff_filter_AR_for_migration_detection(self):
        """The migration-detection `git diff` call MUST use
        `--diff-filter=AR` (not just `A`) so a rename that gives a
        migration file a new revision id is not silently skipped."""
        content = DEPLOY_SH.read_text()
        # The AR filter must appear in a git diff line that scopes to
        # migrations/versions/. Loose enough to survive comment /
        # whitespace edits, tight enough to catch a regression.
        assert (
            "--diff-filter=AR" in content
        ), (
            "deploy.sh migration detection uses `--diff-filter=A` (or"
            " no filter). Renames like commit a8242c5c will be silently"
            " skipped and prod alembic step won't run. Change to"
            " `--diff-filter=AR`."
        )

    def test_no_lone_diff_filter_A_on_migrations_path(self):
        """Regression pin: forbid the exact pattern that caused the
        original bug (`--diff-filter=A ... migrations/versions/`)."""
        content = DEPLOY_SH.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                # Comment lines may legitimately mention the old form.
                continue
            if "migrations/versions" not in line:
                continue
            # Line touches the migrations path — check filter shape.
            # Allow AR / ARC / etc, reject bare A.
            if "--diff-filter=A" in line and "--diff-filter=AR" not in line:
                # More precise check: is the filter literally just `=A`
                # followed by whitespace or end-of-arg?
                import re
                if re.search(r"--diff-filter=A(?!R)(?!\w)", line):
                    raise AssertionError(
                        f"deploy.sh has a lone `--diff-filter=A` on a "
                        f"migrations-versions line: {line!r}. Renames "
                        f"will be silently skipped."
                    )
