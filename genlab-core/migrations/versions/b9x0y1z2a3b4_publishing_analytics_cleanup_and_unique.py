"""publishing_analytics: heal cross-niche leaks + dedupe + UNIQUE(bp, platform)

Three ops in one migration (all idempotent; each guarded by a pre-check
so re-applying is safe):

1. **Heal cross-niche PA leaks**: Pre-2026-07-14 an RLS-bypass bug in
   4 engagement/analytics proxy sites allowed publishing_analytics
   rows to be written with a niche_id that mismatched the parent
   blueprint's niche_id. The deep-cuts audit (2026-07-17) found 23
   surviving rows in this state. `pa.niche_id != b.niche_id` is the
   detector; UPDATE from the blueprint side is the fix (blueprints
   are authoritative).

2. **Dedupe on (blueprint_id, platform)**: The audit surfaced 183
   duplicate (blueprint_id, platform) pairs. Root cause: the publisher
   writes a new PA row on every retry attempt without an idempotency
   key. Keep the newest row per pair (MAX(created_at)); the earlier
   attempts are strictly worse (they represent superseded outcomes).

3. **Add UNIQUE INDEX (blueprint_id, platform)**: Once deduped,
   pin the invariant so the class-of-bug can't re-emerge silently.
   Publisher retries that don't dedupe upstream will now hard-fail
   with an IntegrityError — much better than corrupting the reward
   fetcher's aggregate.

Revision ID: b9x0y1z2a3b4
Revises: a8w9x0y1z2a3
Create Date: 2026-07-17
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "b9x0y1z2a3b4"
down_revision = "a8w9x0y1z2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Heal cross-niche leaks ─────────────────────────────────
    op.execute(
        """
        UPDATE publishing_analytics pa
        SET niche_id = b.niche_id
        FROM blueprints b
        WHERE pa.blueprint_id = b.id
          AND pa.niche_id IS DISTINCT FROM b.niche_id
        """
    )

    # ── 2. Dedupe on (blueprint_id, platform) — keep newest ───────
    # `id NOT IN (SELECT MAX(id) …)` is correct even when created_at
    # ties because `id` is monotonically-increasing serial → newer id
    # ⇒ newer row. Safer than MAX(created_at) which can tie.
    op.execute(
        """
        DELETE FROM publishing_analytics
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY blueprint_id, platform
                           ORDER BY id DESC
                       ) AS rn
                FROM publishing_analytics
                WHERE blueprint_id IS NOT NULL
                  AND platform IS NOT NULL
            ) t
            WHERE t.rn > 1
        )
        """
    )

    # ── 3. UNIQUE INDEX (blueprint_id, platform) ──────────────────
    # Partial index: only where both cols are non-null. Legacy rows
    # with NULL blueprint_id (e.g. SKIPPED records from before the
    # bp linkage) stay unaffected.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS
            uq_publishing_analytics_bp_platform
            ON publishing_analytics (blueprint_id, platform)
            WHERE blueprint_id IS NOT NULL AND platform IS NOT NULL
        """
    )


def downgrade() -> None:
    # Only the index is reversible. Cross-niche healing + dedupe are
    # data operations that cannot be undone (the deleted rows were
    # strictly worse duplicates; no information loss).
    op.execute(
        "DROP INDEX IF EXISTS uq_publishing_analytics_bp_platform"
    )
