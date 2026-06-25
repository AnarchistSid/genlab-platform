"""source_channel_id is promoted to a real blueprints column.

Foundation PR after #585. Pins:

  * "source_channel_id" appears in PROMOTED_COLUMNS["blueprints"]
    so values land in the real column instead of the JSONB extra
    blob — the proposer's WHERE clause + GROUP BY need the
    indexed column, not a JSONB key lookup.
  * Migration file exists with both ADD COLUMN + partial index
    on (niche_id, source_channel_id)
  * Migration uses the merge syntax (down_revision = tuple) so
    the alembic 'multiple heads' state from before this PR is
    resolved by the upgrade.
"""

from __future__ import annotations

from pathlib import Path


def test_source_channel_id_in_blueprints_promoted_set():
    """If this set drops source_channel_id, writes silently land in
    the JSONB ``extra`` column and the proposer's per-channel
    aggregation will return zero rows — same failure mode as the
    R-63 stories.video_id bug captured in the inline comment."""
    from genlab_core.storage.postgres import PROMOTED_COLUMNS

    assert "source_channel_id" in PROMOTED_COLUMNS["blueprints"]


def test_migration_file_present_with_expected_shape():
    """Source pin: the migration file ships ADD COLUMN + index +
    merge-of-heads via the down_revision tuple. Drift in any of
    the three breaks either the schema or the alembic graph."""
    here = Path(__file__).parent.parent.parent / "migrations" / "versions"
    matches = list(here.glob("*blueprints_source_channel_id.py"))
    assert len(matches) == 1, (
        f"expected exactly one migration file matching the source-channel-id "
        f"foundation; found {matches}"
    )
    src = matches[0].read_text()

    # Schema bits
    assert "ADD COLUMN IF NOT EXISTS source_channel_id TEXT" in src
    assert "idx_bp_source_channel" in src
    # Partial index on (niche_id, source_channel_id) — the proposer
    # filters by niche AND non-null channel
    assert "ON blueprints (niche_id, source_channel_id)" in src
    assert "WHERE source_channel_id IS NOT NULL" in src

    # Merge-of-heads tuple — both head IDs must be in the
    # down_revision declaration
    assert 'down_revision = ("p6k7l8m9n0o1", "f2g3h4i5j6k7")' in src


def test_migration_has_downgrade_for_reversibility():
    """Every Alembic migration ships a downgrade path. Otherwise
    deploys that need to roll back lose the column + index."""
    here = Path(__file__).parent.parent.parent / "migrations" / "versions"
    src = next(here.glob("*blueprints_source_channel_id.py")).read_text()
    assert "def downgrade()" in src
    assert "DROP COLUMN IF EXISTS source_channel_id" in src
    assert "DROP INDEX IF EXISTS idx_bp_source_channel" in src
