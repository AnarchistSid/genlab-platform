"""Tenant model read helpers (PR #557, 2026-06-25).

Step 2 of the SaaS multi-tenancy dependency order documented in
SYSTEM-RESEARCH.md §9. Foundation for the tenant abstraction the
system has been missing — ``niche_id`` was doing double duty as
content-category AND tenant-key; the migration in this PR adds a
proper ``tenants`` table + ``tenant_niches`` join, and these
helpers expose them to callers.

## Public surface

  get_tenant_for_niche(niche_id) -> Tenant | None
    Reverse-lookup. Returns the tenant that owns the niche, or
    None for orphan niches (legacy data, mis-seeded niche_id).

  list_niches_for_tenant(tenant_slug_or_id) -> list[str]
    Forward-lookup. Returns the niche_ids bound to the tenant,
    sorted. Empty list when the tenant exists but has no bindings
    yet (cold-start onboarding state).

  get_tenant_by_slug(slug) -> Tenant | None
    Auth-path lookup. Slugs are URL-safe identifiers ("self",
    "acme-brands") and what the operator-facing surfaces use.

## Backward compat

This module is read-only and additive. Existing callers that pass
``niche_id`` to ``pg_connect()`` continue to work unchanged — the
GUC stays ``app.niche_id``. A follow-up PR will add ``app.tenant_id``
as a parallel scope; this PR just makes the relation queryable.

## Failure modes

All helpers FAIL-CLOSED:

  * DATABASE_URL not set         → None / empty list
  * Connection / query exception → None / empty list, WARNING log
  * No matching row              → None / empty list (not an error)

The conservative None / [] choice means a missing tenants table
(e.g. running against an old DB before the migration) degrades to
"unknown tenant" rather than crashing the caller. Callers should
treat None as "no tenant binding available" and fall back to legacy
niche-only behavior (the current code path everywhere).
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tenant:
    """Minimal tenant DTO. Mirrors the ``tenants`` table columns
    that downstream code actually needs — id, slug, name, status.
    created_at / updated_at intentionally omitted; auth-path code
    shouldn't depend on lifecycle timestamps."""

    id: uuid.UUID
    slug: str
    name: str
    status: str


def _connect():
    """Lazy-import + connect helper. Returns a connection bound to
    admin mode (niche_id='all') because tenant queries are
    cross-tenant by construction — looking up which tenant owns a
    niche can't be scoped to a single niche.

    Returns None when DATABASE_URL isn't set OR when pg_connect can't
    establish a connection. Callers see the wrapped exception via
    the logger and fail-close to None / [].
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[tenants] DATABASE_URL not set; tenant lookups disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — fail-close per module contract
        logger.warning("[tenants] connection failed: %s", exc)
        return None


def get_tenant_for_niche(niche_id: str) -> Tenant | None:
    """Return the tenant that owns ``niche_id``, or None.

    None means EITHER (a) the niche has no tenant binding (orphan /
    pre-migration data) OR (b) the lookup failed (no DATABASE_URL,
    connection error, no tenants table). Callers should treat both
    the same way: fall back to legacy niche-only behavior.

    A niche could theoretically be co-owned by multiple tenants
    (the join table allows it); when that happens this helper
    returns the FIRST result by tenant.created_at ASC (the oldest
    binding wins). In the current owner-mode there's exactly one
    tenant ("self") per niche, so the order doesn't matter; we pin
    a deterministic choice for the future.
    """
    if not niche_id:
        return None
    conn_cm = _connect()
    if conn_cm is None:
        return None
    try:
        with conn_cm as conn:
            row = conn.execute(
                """
                SELECT t.id, t.slug, t.name, t.status
                FROM tenants t
                JOIN tenant_niches tn ON tn.tenant_id = t.id
                WHERE tn.niche_id = %s
                ORDER BY t.created_at ASC
                LIMIT 1
                """,
                (niche_id,),
            ).fetchone()
        if row is None:
            return None
        return Tenant(id=row["id"], slug=row["slug"], name=row["name"], status=row["status"])
    except Exception as exc:  # noqa: BLE001 — fail-close
        logger.warning("[tenants] get_tenant_for_niche(%r) failed: %s", niche_id, exc)
        return None


def list_niches_for_tenant(tenant_slug_or_id: str | uuid.UUID) -> list[str]:
    """Forward-lookup: niches bound to the tenant. Accepts either
    a slug (operator-facing identifier) or a UUID (internal id).

    Returns sorted list for deterministic ordering. Empty list on
    any failure mode (unknown tenant, DB error, no DATABASE_URL).
    """
    if not tenant_slug_or_id:
        return []
    conn_cm = _connect()
    if conn_cm is None:
        return []
    # Slug vs UUID — try UUID first, fall back to slug. Both queries
    # are indexed (slug via idx_tenants_slug; UUID via PK).
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
                    SELECT niche_id FROM tenant_niches
                    WHERE tenant_id = %s
                    ORDER BY niche_id
                    """,
                    (str(tenant_slug_or_id),),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT tn.niche_id
                    FROM tenant_niches tn
                    JOIN tenants t ON t.id = tn.tenant_id
                    WHERE t.slug = %s
                    ORDER BY tn.niche_id
                    """,
                    (str(tenant_slug_or_id),),
                ).fetchall()
        return [r["niche_id"] for r in rows]
    except Exception as exc:  # noqa: BLE001 — fail-close
        logger.warning(
            "[tenants] list_niches_for_tenant(%r) failed: %s",
            tenant_slug_or_id,
            exc,
        )
        return []


def get_tenant_by_slug(slug: str) -> Tenant | None:
    """Auth-path lookup — translate the operator-facing slug to a
    Tenant DTO. None when the slug is unknown or the lookup fails.
    """
    if not slug:
        return None
    conn_cm = _connect()
    if conn_cm is None:
        return None
    try:
        with conn_cm as conn:
            row = conn.execute(
                "SELECT id, slug, name, status FROM tenants WHERE slug = %s LIMIT 1",
                (slug,),
            ).fetchone()
        if row is None:
            return None
        return Tenant(id=row["id"], slug=row["slug"], name=row["name"], status=row["status"])
    except Exception as exc:  # noqa: BLE001 — fail-close
        logger.warning("[tenants] get_tenant_by_slug(%r) failed: %s", slug, exc)
        return None
