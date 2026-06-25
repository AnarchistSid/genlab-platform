"""User read helpers (PR #559, 2026-06-25).

Step 3 of the SaaS multi-tenancy dependency order documented in
SYSTEM-RESEARCH.md §9. Following PR #557 (tenant model) and PR #558
(tenant-model first consumer), this PR introduces operators as
first-class database entities — no more env-var-as-identity.

## Public surface

  get_user_by_username(username) -> User | None
    Auth-path lookup. Returns the user DTO for a login attempt,
    or None when the username is unknown / DB unavailable /
    DSN unset.

  list_users_for_tenant(tenant_slug_or_id) -> list[User]
    Admin UI lookup. Lists every user bound to the given tenant
    (active + suspended + deactivated all returned; UI does the
    filtering). Empty list on any failure mode.

## Backward compat

This module is read-only and additive. The env-var auth path
(``REVIEW_AUTH_USER`` + ``REVIEW_AUTH_PASS``) keeps working
unchanged. The users table sits as a queryable identity store
waiting for the next PR to wire it into the auth path.

## Failure modes

All helpers FAIL-CLOSED:

  * DATABASE_URL not set         → None / empty list
  * Connection / query exception → None / empty list, WARNING log
  * No matching row              → None / empty list (not an error)

Same conservative-None choice as PR #557's tenant helpers — a
missing users table (e.g. deploy-before-migration race) degrades
to "no user known" rather than crashing the caller. The auth
caller treats None as "fall back to env-var path."

## Why a frozen dataclass instead of a Pydantic model

Same rationale as PR #557's Tenant DTO: auth-path callers
shouldn't mutate identity mid-session. Frozen dataclasses raise
``FrozenInstanceError`` on attempted mutation; Pydantic models
allow mutation by default unless explicitly configured. The
frozen dataclass is the safer default for auth-path code.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class User:
    """Auth-path user DTO. Mirrors the ``users`` table columns
    that downstream code actually needs — id, username, display_name,
    tenant_id, role, status. password_hash + lifecycle timestamps
    intentionally omitted; auth-path code shouldn't depend on them.
    Password verification happens in a dedicated module so the User
    DTO can stay safe to log."""

    id: uuid.UUID
    username: str
    display_name: str
    tenant_id: uuid.UUID
    role: str
    status: str


# Valid role values. Mirrored as a Python constant so callers can
# do role-based checks without hitting the DB; kept in sync with
# the database-side constraint expected on the column (a future
# migration may add an explicit CHECK).
VALID_ROLES: frozenset[str] = frozenset({"admin", "operator", "viewer"})
VALID_STATUSES: frozenset[str] = frozenset({"active", "suspended", "deactivated"})


def _connect():
    """Lazy-import + connect helper. Returns a connection bound to
    admin mode (niche_id='all') because user queries are cross-tenant
    by construction — looking up a user from a login attempt happens
    BEFORE we know which tenant they belong to.

    Returns None when DATABASE_URL isn't set OR when pg_connect can't
    establish a connection. Callers see the wrapped exception via
    the logger and fail-close to None / [].
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[users] DATABASE_URL not set; user lookups disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — fail-close per module contract
        logger.warning("[users] connection failed: %s", exc)
        return None


def _row_to_user(row: dict) -> User:
    """Translate a fetched row to a User DTO. Centralises the column
    extraction so a schema change touches one spot."""
    return User(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        tenant_id=row["tenant_id"],
        role=row["role"],
        status=row["status"],
    )


def get_user_by_username(username: str) -> User | None:
    """Return the user with this username, or None.

    None means EITHER (a) the username doesn't exist (typo, deleted
    account) OR (b) the lookup failed (no DATABASE_URL, connection
    error, no users table). Callers should treat both the same way:
    auth fails / falls back to env-var path.

    The lookup is case-sensitive; usernames are normalised at write
    time (the row stored is what gets matched). Future auth-write
    PRs should lower-case before insert if case-insensitive matching
    is desired.
    """
    if not username:
        return None
    conn_cm = _connect()
    if conn_cm is None:
        return None
    try:
        with conn_cm as conn:
            row = conn.execute(
                """
                SELECT id, username, display_name, tenant_id, role, status
                FROM users
                WHERE username = %s
                LIMIT 1
                """,
                (username,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_user(row)
    except Exception as exc:  # noqa: BLE001 — fail-close
        logger.warning("[users] get_user_by_username(%r) failed: %s", username, exc)
        return None


def list_users_for_tenant(tenant_slug_or_id: str | uuid.UUID) -> list[User]:
    """List users bound to the tenant. Accepts either a slug (e.g.
    'self') or a UUID (the internal tenant id). Empty list on any
    failure mode.

    Returns all users regardless of status (active + suspended +
    deactivated) — the admin UI filters for display. This avoids
    accidentally hiding suspended accounts from an admin trying to
    re-activate them. Sorted by created_at ASC for stable pagination.
    """
    if not tenant_slug_or_id:
        return []
    conn_cm = _connect()
    if conn_cm is None:
        return []
    # Slug vs UUID dispatch — try UUID first, fall back to slug.
    # Both queries are indexed (PK + idx_tenants_slug + idx_users_tenant_id).
    is_uuid = isinstance(tenant_slug_or_id, uuid.UUID)
    if not is_uuid:
        try:
            tenant_uuid = uuid.UUID(str(tenant_slug_or_id))
            is_uuid = True
            tenant_slug_or_id = tenant_uuid
        except (ValueError, TypeError):
            is_uuid = False
    try:
        with conn_cm as conn:
            if is_uuid:
                rows = conn.execute(
                    """
                    SELECT id, username, display_name, tenant_id, role, status
                    FROM users
                    WHERE tenant_id = %s
                    ORDER BY created_at ASC
                    """,
                    (str(tenant_slug_or_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT u.id, u.username, u.display_name, u.tenant_id, u.role, u.status
                    FROM users u
                    JOIN tenants t ON t.id = u.tenant_id
                    WHERE t.slug = %s
                    ORDER BY u.created_at ASC
                    """,
                    (str(tenant_slug_or_id),),
                ).fetchall()
        return [_row_to_user(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 — fail-close
        logger.warning(
            "[users] list_users_for_tenant(%r) failed: %s",
            tenant_slug_or_id,
            exc,
        )
        return []
