"""PR #535 (2026-06-24): SR-B RLS WITH CHECK normalization migration.

Source-level pins for the migration's shape — the actual policy
update can only be verified against a live Postgres instance (which
CI runs in the test-storage job via the alembic upgrade smoke).

Without these pins:
  * dropping the WITH CHECK from tier_history → re-opens SR-B for
    that table; INSERTs land with any (or no) niche_id GUC
  * narrowing the 3 USING clauses back → silent partial regression
    where admin-mode SELECT can't read rows admin-mode INSERT just
    wrote (storage-layer asymmetry)
  * skipping the downgrade body → alembic downgrade head -1 errors
    with "no downgrade defined" — operators can't roll back

The chain ``a7b8c9d0e1f2`` (creates tier_history with USING only) →
``b8c9d0e1f2g3`` (this PR, adds WITH CHECK) is the explicit
revision-graph link the test pins. A future migration mis-ordering
that detaches from this chain would silently lose the WITH CHECK
fix on rebase.
"""

from __future__ import annotations

from pathlib import Path

import pytest

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "migrations"
    / "versions"
    / "b8c9d0e1f2g3_sr_b_rls_with_check_normalization.py"
)


@pytest.fixture(scope="module")
def src() -> str:
    return MIGRATION_PATH.read_text()


# ── revision chain ─────────────────────────────────────────────────


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), (
        f"SR-B normalization migration missing at {MIGRATION_PATH}. PR #535 ships this file."
    )


def test_revision_id_pinned(src):
    """The revision ID must match the filename slug. Mismatches break
    alembic's revision discovery."""
    assert 'revision = "b8c9d0e1f2g3"' in src


def test_down_revision_chains_to_tier_history(src):
    """Must chain from a7b8c9d0e1f2 (the migration that CREATED
    tier_history without WITH CHECK). Detaching from this chain
    would mean the SR-B fix doesn't apply to the same tier_history
    schema."""
    assert 'down_revision = "a7b8c9d0e1f2"' in src


# ── upgrade body content ───────────────────────────────────────────


def test_upgrade_adds_with_check_to_tier_history(src):
    """The core SR-B fix: tier_history must get a WITH CHECK clause."""
    upgrade_start = src.find("def upgrade()")
    downgrade_start = src.find("def downgrade()")
    upgrade_body = src[upgrade_start:downgrade_start]

    # tier_history's new policy must include WITH CHECK in the upgrade
    assert "CREATE POLICY niche_isolation ON tier_history" in upgrade_body
    # And the WITH CHECK clause must be present (separate from USING)
    tier_block_start = upgrade_body.find("CREATE POLICY niche_isolation ON tier_history")
    tier_block = upgrade_body[tier_block_start : tier_block_start + 500]
    assert "WITH CHECK" in tier_block, (
        "tier_history's new policy MUST include WITH CHECK — this is "
        "the entire point of PR #535 for that table."
    )


def test_upgrade_normalizes_all_4_tables(src):
    """All 4 affected tables — tier_history + ab_tests +
    audience_snapshots + email_subscribers — must appear in upgrade."""
    upgrade_start = src.find("def upgrade()")
    downgrade_start = src.find("def downgrade()")
    upgrade_body = src[upgrade_start:downgrade_start]

    for table in ("tier_history", "ab_tests", "audience_snapshots", "email_subscribers"):
        assert f"CREATE POLICY niche_isolation ON {table}" in upgrade_body, (
            f"Migration upgrade must touch {table}'s niche_isolation policy."
        )


def test_upgrade_uses_canonical_policy_shape(src):
    """The canonical shape (used by the other 16 tables) is the 3-way
    OR: niche_id match OR ('', 'all') OR NULL. Pinning that the
    migration reuses this exact shape avoids silently introducing
    a 4th variant. The shape lives in _CANONICAL_USING_AND_CHECK
    at module level (interpolated into all 4 policy CREATEs)."""
    # The 3-way OR pattern must exist somewhere in the file (module
    # constant interpolated into the upgrade body via f-string).
    assert "current_setting('app.niche_id', true) IN ('', 'all')" in src
    assert "current_setting('app.niche_id', true) IS NULL" in src
    # And the constant must be referenced inside the upgrade body
    upgrade_start = src.find("def upgrade()")
    downgrade_start = src.find("def downgrade()")
    upgrade_body = src[upgrade_start:downgrade_start]
    assert "_CANONICAL_USING_AND_CHECK" in upgrade_body, (
        "Upgrade body must interpolate the canonical shape constant — "
        "duplicating the SQL inline risks introducing a 4th variant."
    )


# ── downgrade body content ─────────────────────────────────────────


def test_downgrade_restores_tier_history_using_only(src):
    """Downgrade must restore tier_history's pre-PR-535 USING-only
    shape. A no-op downgrade would leave the WITH CHECK in place
    even when the operator rolls back — defeating the purpose of
    alembic's down direction."""
    downgrade_start = src.find("def downgrade()")
    downgrade_body = src[downgrade_start:]

    # Scope to the tier_history op.execute block specifically: starts
    # at the tier_history CREATE POLICY and ends at the closing
    # triple-quote on the same op.execute call. The for-loop body
    # for the OTHER 3 tables (which DOES include WITH CHECK) comes
    # after this block and shouldn't be included in the scan.
    tier_block_start = downgrade_body.find("CREATE POLICY niche_isolation ON tier_history")
    assert tier_block_start != -1
    # End at the closing triple-quote of the tier_history op.execute
    # (the first `"""` that follows the CREATE statement)
    tier_block_end = downgrade_body.find('"""', tier_block_start)
    assert tier_block_end != -1
    tier_block = downgrade_body[tier_block_start:tier_block_end]
    assert "WITH CHECK" not in tier_block, (
        "tier_history downgrade must restore USING-only shape — "
        "WITH CHECK is the new addition, not the old state."
    )


def test_downgrade_touches_all_4_tables(src):
    """Downgrade must touch all 4 tables (no partial-revert state)."""
    downgrade_start = src.find("def downgrade()")
    downgrade_body = src[downgrade_start:]

    # tier_history is treated specially (its downgrade is unique);
    # the other 3 are looped via the `for table in (...)` shape.
    for table in ("tier_history", "ab_tests", "audience_snapshots", "email_subscribers"):
        assert table in downgrade_body, (
            f"Downgrade must touch {table} — partial reverts leave the DB in mixed state."
        )


# ── idempotency pattern ────────────────────────────────────────────


def test_upgrade_uses_drop_if_exists_pattern(src):
    """Every policy update must use ``DROP POLICY IF EXISTS`` before
    CREATE — matches the canonical pattern from every prior RLS
    migration on this project. Without IF EXISTS, re-running the
    migration after a partial failure errors instead of healing."""
    upgrade_start = src.find("def upgrade()")
    downgrade_start = src.find("def downgrade()")
    upgrade_body = src[upgrade_start:downgrade_start]

    # 4 tables × 1 DROP each = 4 occurrences minimum
    drop_count = upgrade_body.count("DROP POLICY IF EXISTS niche_isolation ON")
    assert drop_count >= 4, (
        f"Upgrade must DROP POLICY IF EXISTS before CREATE for all 4 "
        f"tables — found {drop_count} drops."
    )


def test_downgrade_uses_drop_if_exists_pattern(src):
    """Same idempotency requirement on the downgrade side."""
    downgrade_start = src.find("def downgrade()")
    downgrade_body = src[downgrade_start:]
    # tier_history is explicit + the for-loop over the other 3
    assert "DROP POLICY IF EXISTS niche_isolation ON" in downgrade_body
