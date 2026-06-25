"""users table foundation — auth-path identity store

Revision ID: d0e1f2g3h4i5
Revises: c9d0e1f2g3h4
Create Date: 2026-06-25 14:30:00.000000+00:00

PR #559 (2026-06-25): step 3 of the SaaS multi-tenancy dependency
order documented in SYSTEM-RESEARCH.md §9 (after step 2 — tenant
model — landed in PR #557 + first consumer in PR #558).

## Why now

The auth model today is a SINGLE shared credential via env vars
(``REVIEW_AUTH_USER`` + ``REVIEW_AUTH_PASS``). PR #558 introduced
``REVIEW_TENANT_SLUG_<USER>`` as a per-username binding, but the
"user" still isn't a first-class entity — it's a fragment of an
env-var name. The users table makes operators real database
entities so:

  * Multiple operators per tenant become trivial (one row each
    binding to a tenant_id, no env-var explosion).
  * Roles (admin / operator / viewer) become a column instead of
    a separate ad-hoc check.
  * Login activity (last_login_at) can drive UI like "X operators
    active last 7 days" or stale-account alerts.
  * Password hashes have a home for the future (today's
    REVIEW_AUTH_PASS env-var path keeps working — see backward
    compat below).

## Schema

  users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username TEXT NOT NULL UNIQUE,        -- login identifier
                                          -- ('admin', 'gaming_op')
    display_name TEXT NOT NULL,           -- 'Alex Owner'
    tenant_id UUID NOT NULL
        REFERENCES tenants(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'operator',
                                          -- 'admin'|'operator'|
                                          --   'viewer'
    password_hash TEXT,                   -- nullable; NULL means
                                          --   env-var-auth user
                                          --   (no DB password
                                          --   stored yet)
    status TEXT NOT NULL DEFAULT 'active',
                                          -- 'active'|'suspended'|
                                          --   'deactivated'
    last_login_at TIMESTAMPTZ,            -- nullable; set on
                                          --   successful auth
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
  )

### Indexes

  idx_users_username   on (username)     — auth lookup (every
                                           login hits this)
  idx_users_tenant_id  on (tenant_id)    — list users per tenant
                                           (admin UI, audit)

### Seed

Insert ONE admin user bound to the 'self' tenant from PR #557.
``password_hash = NULL`` because the env-var-auth path is still
live; the row's job is to make the operator-identity-as-a-thing
queryable, not to replace authentication yet. The username
matches ``REVIEW_AUTH_USER`` (defaults to 'admin') so a future
auth-wire PR can match env-var sessions to this row.

Idempotent via ON CONFLICT DO NOTHING on the username UNIQUE
constraint.

## Backward compat

Total. No callsite consumes this table yet (read helpers ship
alongside but are unwired). Env-var auth keeps working unchanged.
The users table sits as a queryable identity store waiting for
the next PR to wire it into the auth path.

## RLS

NOT applied. users is admin/operator metadata, not per-niche
content data. A future PR may add RLS keyed on tenant_id if the
threat model requires it (e.g. allowing tenant admins to manage
their own operators).

## Rollback

The downgrade drops the table + both indexes. Safe — the
foundation has zero callsite consumers.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d0e1f2g3h4i5"
down_revision = "c9d0e1f2g3h4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            role TEXT NOT NULL DEFAULT 'operator',
            password_hash TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            last_login_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users (tenant_id)
        """
    )

    # Seed: one 'admin' user bound to the 'self' tenant (PR #557).
    # password_hash stays NULL — env-var-auth (REVIEW_AUTH_PASS) still
    # owns authentication; the row exists so a future auth-wire PR can
    # match the env-var session username to this row. Idempotent via
    # the username UNIQUE constraint.
    op.execute(
        """
        INSERT INTO users (username, display_name, tenant_id, role, status)
        SELECT 'admin', 'Self (admin)', t.id, 'admin', 'active'
        FROM tenants t
        WHERE t.slug = 'self'
        ON CONFLICT (username) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_users_tenant_id")
    op.execute("DROP INDEX IF EXISTS idx_users_username")
    op.execute("DROP TABLE IF EXISTS users")
