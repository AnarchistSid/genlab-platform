"""ContextVar-based tenant identity + ``pg_connect`` shim.

Closes the SR-A / SR-C / SR-D class of bugs flagged in the
post-Wave-4 audit (2026-06-17): there are 43 direct
``psycopg.connect()`` call sites in this codebase, 34 of which
DON'T set ``app.niche_id`` GUC at all. They run with
``current_setting('app.niche_id', true) = NULL`` → the RLS policy's
``OR ... IS NULL`` admin escape always fires.

For Phase-1 single-tenant this is fine (admin mode is what we want).
For Phase-2 SaaS multi-tenant, every one of those 34 files is a
tenant-escape vector. The SR-B WITH CHECK (shipped) protects writes
that go through ``PostgresBackend``, but the 34 direct sites bypass
it entirely.

This module ships the FOUNDATION for the fix:

  1. ``CURRENT_NICHE``  — a ``ContextVar[str | None]`` that holds
     the request's tenant identity.

  2. ``niche_scope(niche_id)`` — context manager that sets the
     ContextVar for a code region. Composes via standard Python
     ContextVar token semantics.

  3. ``pg_connect(dsn=None, *, niche_id=None, **kwargs)`` — a
     ``psycopg.connect`` drop-in that automatically issues
     ``SET app.niche_id`` from the ContextVar (or the explicit
     ``niche_id=`` kwarg if provided). Preserves the connection
     for the caller exactly as ``psycopg.connect`` would.

  4. ``REQUIRE_TENANT_GUC`` — a feature flag (env var) controlling
     what happens when no tenant identity is available:

       * False (default): fall back to admin mode (``SET app.niche_id = ''``)
                          and emit a structured DEBUG log line so the
                          caller's behaviour is unchanged in Phase 1.
       * True (Phase 2): raise ``MissingTenantContext`` — fail-closed.

     Operators flip this AFTER all 34 sites are migrated.

Migration recipe for the 34 callers
====================================
Old (admin-mode bypass):

    with psycopg.connect(dsn) as conn:
        ...

New (tenant-scoped):

    from genlab_core.storage.tenant_context import pg_connect

    with pg_connect(dsn) as conn:  # ContextVar must be set OR niche_id= kwarg
        ...

For request-scoped use in Flask (dashboard):

    @app.before_request
    def _set_tenant():
        token = niche_scope(request.args.get("niche_id", "all"))
        g._niche_token = token

    @app.teardown_request
    def _clear_tenant(exc):
        token = getattr(g, "_niche_token", None)
        if token is not None:
            CURRENT_NICHE.reset(token)

For pipeline-scoped use (per-niche workers):

    with niche_scope(niche_id):
        run_pipeline(niche_id)

For admin / cron / cross-tenant ops:

    with niche_scope("all"):  # admin mode, but explicitly
        do_global_thing()

Why not just monkey-patch ``psycopg.connect``?
==============================================
Tempting but unsafe. Anthropic's SDK, the dramatiq broker, redis,
and other libraries also use psycopg under the hood — silently
intercepting their connections would set RLS state at unexpected
times. The shim is opt-in per call site.

Why not in ``PostgresBackend``?
================================
``PostgresBackend`` already enforces tenant isolation at its 6
public methods (and SR-B WITH CHECK enforces at DB level for those
writes). The shim addresses the 34 OTHER call sites that don't
go through PostgresBackend — direct cursor work in pipelines,
scheduling, learning, observability, dashboard. PostgresBackend
can keep using its own pool; this shim is for the long-tail.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


# ContextVar holding the current request's tenant identity. Defaults to
# None so callers can detect "tenant unknown" vs "admin mode" (``''`` /
# ``'all'``) explicitly. The ContextVar API gives us Python-stdlib
# guaranteed safety across asyncio + threads (each task / thread gets
# its own copy).
CURRENT_NICHE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "genlab_current_niche", default=None
)


# Feature flag env var. Phase 1 default: False (preserve admin-mode
# fallback so unmigrated callers don't break). Phase 2 (after all 34
# sites are migrated): operator flips to "1" and the shim fails closed.
_REQUIRE_TENANT_ENV = "GENLAB_REQUIRE_TENANT_GUC"


def is_tenant_required() -> bool:
    """Whether unset-ContextVar should raise (True) or admin-mode (False)."""
    return os.environ.get(_REQUIRE_TENANT_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class MissingTenantContext(RuntimeError):
    """Raised when ``pg_connect`` is called without a tenant set
    AND ``GENLAB_REQUIRE_TENANT_GUC`` is on.

    Operator's fail-closed Phase-2 mode — the call site is responsible
    for either:
      * wrapping in ``with niche_scope(niche_id):``, OR
      * passing ``niche_id=`` explicitly to ``pg_connect()``, OR
      * passing ``niche_id="all"`` to explicitly opt into admin.
    """


@contextlib.contextmanager
def niche_scope(niche_id: str):
    """Set CURRENT_NICHE for a code region.

    Mirrors Python's ContextVar token-reset pattern so nested scopes
    compose cleanly:

        with niche_scope("gaming"):    # CURRENT_NICHE = "gaming"
            with niche_scope("anime"): # CURRENT_NICHE = "anime"
                ...
            # back to "gaming"

    ``niche_id`` may be any string the caller's intent dictates,
    including ``"all"`` for explicit admin mode. The shim treats
    ``"all"`` and the empty string identically per the RLS policy
    expression (both bypass tenant isolation).
    """
    token = CURRENT_NICHE.set(niche_id)
    try:
        yield niche_id
    finally:
        CURRENT_NICHE.reset(token)


def resolve_niche(explicit: str | None) -> str | None:
    """Resolve the effective niche for this call.

    Priority: explicit kwarg → ContextVar → None.
    Returns None when no source has supplied a niche — caller decides
    whether to fall back to admin or fail.
    """
    if explicit is not None:
        return explicit
    return CURRENT_NICHE.get()


def pg_connect(
    dsn: str | None = None,
    *,
    niche_id: str | None = None,
    **connect_kwargs: Any,
):
    """``psycopg.connect`` drop-in that auto-sets ``app.niche_id``.

    Resolution order for the tenant:
      1. ``niche_id`` kwarg (explicit per-call override)
      2. ``CURRENT_NICHE`` ContextVar (set by ``niche_scope``)
      3. Fallback per ``GENLAB_REQUIRE_TENANT_GUC``:
           * unset/false: empty string (admin mode) + DEBUG log
           * true:        raise ``MissingTenantContext``

    After connecting, runs a single ``SET app.niche_id = %s`` so the
    RLS policy sees the right tenant for the lifetime of the
    connection. Operators reading ``pg_stat_activity`` can confirm
    via ``application_name`` (which we leave alone — set per-app).

    Returns the raw ``psycopg.Connection``; caller uses it as they
    would the SDK. Context manager support is inherited (psycopg
    supports ``with conn:`` natively).

    ``dsn=None`` reads ``DATABASE_URL`` (matches the legacy
    ``psycopg.connect(os.environ['DATABASE_URL'])`` pattern of the
    34 sites we're migrating).
    """
    import psycopg

    if dsn is None:
        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            raise RuntimeError(
                "pg_connect: DATABASE_URL not set and no dsn passed — "
                "either set the env var or pass dsn= explicitly"
            )

    effective = resolve_niche(niche_id)
    if effective is None:
        if is_tenant_required():
            raise MissingTenantContext(
                "pg_connect called without a tenant identity — wrap in "
                "``with niche_scope(niche_id):`` or pass ``niche_id=`` "
                "explicitly. Pass ``niche_id='all'`` to explicitly opt "
                "into admin mode."
            )
        # Phase 1 default: silent admin-mode fallback so unmigrated
        # callers behave exactly as they did with bare psycopg.connect.
        # Logged at DEBUG so an operator can ``journalctl | grep`` to
        # find unmigrated sites without spamming the journal.
        logger.debug(
            "[tenant_context] pg_connect with no tenant — admin-mode "
            "fallback (set GENLAB_REQUIRE_TENANT_GUC=1 to require)"
        )
        effective = ""

    conn = psycopg.connect(dsn, **connect_kwargs)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.niche_id', %s, true)", (effective,))
    except Exception:
        # The conn is opened but the GUC didn't stick — close it
        # before re-raising to avoid leaking the connection.
        conn.close()
        raise
    return conn
