"""tenant model foundation — tenants + tenant_niches join

Revision ID: c9d0e1f2g3h4
Revises: b8c9d0e1f2g3
Create Date: 2026-06-25 13:30:00.000000+00:00

PR #557 (2026-06-25): step 2 of the SaaS multi-tenancy dependency
order documented in SYSTEM-RESEARCH.md §9 (after the data-isolation
work of SR-A..SR-F closed in PRs #517..#556).

## Schema

Two new tables. **Foundation only** — no callsite changes in this
PR. Existing pg_connect(niche_id=X) calls continue to work unchanged
(niche_id remains the RLS GUC; tenant_id is a parallel relation that
becomes the authoritative tenant scope in a follow-up PR).

### tenants

  id          UUID PK   gen_random_uuid()
  slug        TEXT      UNIQUE NOT NULL   (URL-safe, e.g. "self",
                                          "acme-brands")
  name        TEXT      NOT NULL          (display, e.g.
                                          "Self (owner)")
  status      TEXT      NOT NULL          ('active'|'suspended'|
                                          'churned')
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()

Slug is the operator-facing identifier (used in URLs, logs); id is
the durable FK target.

### tenant_niches

  tenant_id   UUID    FK → tenants.id ON DELETE CASCADE
  niche_id    TEXT    NOT NULL          (matches existing
                                        blueprints.niche_id text
                                        values: 'gaming', 'sports',
                                        'movies', 'anime',
                                        'ai_creators')
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
  PRIMARY KEY (tenant_id, niche_id)

Many-to-many. A tenant may run multiple niches; a niche could
theoretically be co-owned (not common but the data model allows it).
The composite PK + ON DELETE CASCADE means deleting a tenant
automatically un-binds its niches.

### Indexes

  idx_tenants_slug      on (slug)               — auth lookup
  idx_tenant_niches_n   on (niche_id)           — reverse lookup
                                                  (which tenant owns
                                                   this niche?)

### Seed

One seed row + 5 bindings: the "self" tenant for the current
owner-mode, bound to all 5 known niches (ai_creators, gaming,
sports, movies, anime). This preserves backward compat — any future
"look up tenant for niche" call gets a non-null answer for every
production niche.

## RLS

NOT applied in this PR. tenants + tenant_niches are
operator/admin-only tables (read by the dashboard auth path,
written by the onboarding flow when it exists). They are NOT
per-niche scoped data. Adding RLS later is straightforward if the
threat model demands it.

## Rollback

The downgrade drops both tables. Safe — the foundation has no
callsite consumers yet.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c9d0e1f2g3h4"
down_revision = "b8c9d0e1f2g3"
branch_labels = None
depends_on = None


# Slug → (display_name, niche_ids). Seeded as the self-tenant for the
# 5 known niches; matches the niche_id text values used in
# blueprints, publishing_analytics, etc.
_SELF_TENANT_SLUG = "self"
_SELF_TENANT_NAME = "Self (owner)"
_SELF_TENANT_NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants (slug)
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_niches (
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            niche_id TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, niche_id)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_tenant_niches_niche
        ON tenant_niches (niche_id)
        """
    )

    # Seed the self-tenant + bind to the 5 known niches. Idempotent
    # via ON CONFLICT DO NOTHING — running this migration twice (or
    # against a partially-seeded DB) is safe. SQL written as literal
    # strings (not f-string-interpolated module constants) so source-
    # level pin tests can grep the niche IDs without parsing Python.
    op.execute(
        """
        INSERT INTO tenants (slug, name, status)
        VALUES ('self', 'Self (owner)', 'active')
        ON CONFLICT (slug) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO tenant_niches (tenant_id, niche_id)
        SELECT t.id, n.niche_id
        FROM tenants t
        CROSS JOIN (
            VALUES ('ai_creators'), ('gaming'), ('sports'),
                   ('movies'), ('anime')
        ) AS n(niche_id)
        WHERE t.slug = 'self'
        ON CONFLICT (tenant_id, niche_id) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_tenant_niches_niche")
    op.execute("DROP TABLE IF EXISTS tenant_niches")
    op.execute("DROP INDEX IF EXISTS idx_tenants_slug")
    op.execute("DROP TABLE IF EXISTS tenants")
