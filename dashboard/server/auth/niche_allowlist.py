"""Per-user niche allowlist resolver — SR-F foundation.

SYSTEM-RESEARCH.md §9 SR-F (Medium): "single dashboard credential =
the approval gate is tenant-blind." Today the single admin user
sees + approves blueprints across all 5 niches. The multi-tenant
SaaS future needs per-user scoping so tenant-B operators can't
approve tenant-A's content.

## Foundation-only PR shape

This PR ships the RESOLVER mechanism — no endpoint behavior change
yet. Consumers opt in by calling ``get_allowed_niches()`` and
filtering their result. Same staged template as SR-E (#536 → #537
→ #538):

  * PR #539 (this) — helper that maps the current session user to
    an allowlist. Default ``None`` (unrestricted = current behavior)
    for every endpoint. Opt-in via env var per username.
  * PR #540 (follow-up) — wire blueprint listing endpoints to filter
    by allowlist; reject cross-tenant approve actions with 403.
  * PR #541 (follow-up) — real user table + password hashing +
    per-user session model (today's basic-auth single user gets a
    backward-compat row).

Default state preserved bit-for-bit: when REVIEW_NICHE_ALLOWLIST_*
env vars are unset, the resolver returns None (= "unrestricted").
Existing endpoints can call ``get_allowed_niches()`` opportunistically
without any flag-day coordination.

## Env-var protocol

The allowlist is configured per-username via env vars of shape:

  REVIEW_NICHE_ALLOWLIST_<UPPERCASE_USERNAME>=niche1,niche2

Examples:

  REVIEW_NICHE_ALLOWLIST_ADMIN=gaming,sports,movies,anime,ai_creators
  REVIEW_NICHE_ALLOWLIST_GAMING_OPERATOR=gaming
  REVIEW_NICHE_ALLOWLIST_SPORTS_TEAM=sports

Comma-separated list. Whitespace stripped. Empty list (e.g.
``REVIEW_NICHE_ALLOWLIST_FOO=``) reads as None (unrestricted) — set
to a real niche list to scope. Asterisk (``*``) explicitly means
"unrestricted" if you want to document admin status without
listing every niche.

## Why env-driven (not a DB table) for the foundation

Two reasons:

1. Today's auth is single-user via env (REVIEW_AUTH_USER + _PASS).
   Per-user policies follow the same shape — operators can configure
   without a DB migration.

2. The user-table PR (#541) will be a substantial schema migration.
   This foundation lets endpoints START filtering in PR #540
   without blocking on the user table — the env-driven resolver is
   the same interface either way (just sourced from a different
   backend).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Canonical niche IDs (single source of truth — must match
# configs/niches_registry.yaml). Used to validate env-var entries
# at resolution time so typos don't silently grant access to
# nonexistent niches.
_VALID_NICHE_IDS: frozenset[str] = frozenset({"gaming", "sports", "movies", "anime", "ai_creators"})


def _username_for_session() -> str | None:
    """Resolve the current session's username.

    Pre-#541: returns the REVIEW_AUTH_USER (single shared cred). The
    multi-user path (#541) will look up flask.session['user_id'] →
    user table → username instead.

    Returns None when auth is disabled (localhost-only mode) — the
    resolver then short-circuits to None (unrestricted) at the
    public ``get_allowed_niches`` entry point.
    """
    user = os.environ.get("REVIEW_AUTH_USER", "").strip()
    return user or None


def _env_key_for_user(username: str) -> str:
    """Map a username to its env-var key.

    Username is uppercased and non-alphanumeric chars become
    underscores so any reasonable username produces a valid env-var
    name. ``foo-bar`` → ``REVIEW_NICHE_ALLOWLIST_FOO_BAR``.
    """
    sanitized = "".join(c.upper() if c.isalnum() else "_" for c in username)
    return f"REVIEW_NICHE_ALLOWLIST_{sanitized}"


def _parse_allowlist(raw: str) -> set[str] | None:
    """Parse a comma-separated allowlist string.

    Returns:
      * None — when raw is empty, whitespace-only, or contains the
        literal ``*`` (explicit "unrestricted" marker).
      * set[str] — the validated set of niche IDs from the list.
        Unknown niches are dropped + logged at WARNING (typo defense).
    """
    raw = (raw or "").strip()
    if not raw or raw == "*":
        return None
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    if not parts:
        return None
    if "*" in parts:
        return None
    valid = set()
    rejected = []
    for p in parts:
        if p in _VALID_NICHE_IDS:
            valid.add(p)
        else:
            rejected.append(p)
    if rejected:
        logger.warning(
            "REVIEW_NICHE_ALLOWLIST contained unknown niche IDs %r — dropped. Valid IDs: %s",
            rejected,
            sorted(_VALID_NICHE_IDS),
        )
    return valid if valid else None


def _tenant_slug_env_key_for_user(username: str) -> str:
    """PR #558 (2026-06-25): mirror of ``_env_key_for_user`` for the
    tenant-slug shortcut. ``REVIEW_TENANT_SLUG_<USER>`` binds an
    operator to a tenant in the tenants table, and the allowlist
    is derived from ``list_niches_for_tenant`` at resolution time.

    Example: ``REVIEW_TENANT_SLUG_ACME_OP=acme`` means the operator
    "acme_op" sees whatever niches the "acme" tenant has bindings
    for. Adding a niche to the tenant via SQL automatically grants
    the operator access — no env-var redeploy needed.
    """
    sanitized = "".join(c.upper() if c.isalnum() else "_" for c in username)
    return f"REVIEW_TENANT_SLUG_{sanitized}"


def _allowlist_from_user_record(username: str) -> set[str] | None:
    """PR #560 (2026-06-25): resolve allowlist via the users table.

    Looks up the user by username, then queries the tenant's niche
    bindings. Returns:

      * None if the user doesn't exist in DB OR the tenant has no
        bindings (cold-start) OR the tenant's bindings cover ALL
        valid niches (effectively unrestricted — admin shortcut) OR
        the helper chain fail-closes (no DSN, connection error,
        ImportError on the optional module).
      * A validated set of niche IDs otherwise. Same drift-defense
        as the env-var path — unknown niche IDs from the DB are
        dropped with a WARNING.

    The "ALL valid niches → None" shortcut matters for two reasons:

      1. Performance — downstream SR-F endpoints' fast-path (which
         skips per-record niche checks when get_allowed_niches
         returns None) applies to operators who effectively have
         access to everything. Without this shortcut, every API
         call for a fully-bound admin user runs N per-record DB
         fetches just to confirm the niche is in their (universal)
         allowlist.
      2. Backward compat — pre-PR-560 the "admin" operator returned
         None from get_allowed_niches (no env-var allowlist was
         set). After PR #559 seeded the admin row bound to all 5
         niches, the same operator would resolve to a 5-element
         set. This shortcut preserves the None-returning behavior
         for full-access operators.

    This is the second consumer of PR #557's tenant model (after
    PR #558's REVIEW_TENANT_SLUG_<USER> env path). Together they
    cover both "operator is in the users table" (this) and
    "operator hasn't been migrated to DB yet" (PR #558 env path).
    """
    try:
        from genlab_core.auth.tenants import list_niches_for_tenant
        from genlab_core.auth.users import get_user_by_username
    except ImportError:
        logger.debug("[allowlist] users / tenants module unavailable — skipping DB resolution")
        return None
    user = get_user_by_username(username)
    if user is None:
        return None
    # The user.tenant_id is a UUID (FK to tenants.id); pass the UUID
    # branch of list_niches_for_tenant (PK-indexed lookup) rather
    # than re-deriving the slug.
    niches = list_niches_for_tenant(user.tenant_id)
    if not niches:
        return None
    valid = set()
    rejected = []
    for n in niches:
        if n in _VALID_NICHE_IDS:
            valid.add(n)
        else:
            rejected.append(n)
    if rejected:
        logger.warning(
            "User %r's tenant had unknown niche IDs %r — dropped. Valid IDs: %s",
            username,
            rejected,
            sorted(_VALID_NICHE_IDS),
        )
    if not valid:
        return None
    # Full-access shortcut: tenant covers ALL valid niches = effectively
    # unrestricted. Returning None lets the SR-F endpoints' fast-path
    # skip per-record niche enumeration. See docstring rationale above.
    if valid == _VALID_NICHE_IDS:
        return None
    return valid


def _allowlist_from_tenant(slug: str) -> set[str] | None:
    """Resolve a tenant slug to its allowlist via the tenant model.

    Returns:
      * None if the tenant model lookup returns no niches (unknown
        slug, empty bindings, DB unavailable, no DATABASE_URL —
        the helper fail-closes per its contract).
      * A non-empty set of validated niche IDs otherwise. Same
        validation as ``_parse_allowlist`` — unknown niche IDs from
        the DB (legacy data, drift) are dropped with a WARNING.

    This is the bridge between PR #557's tenant model and SR-F.
    """
    # Lazy import — keep the module importable in environments
    # without the tenant model (e.g. unit tests that don't need it).
    try:
        from genlab_core.auth.tenants import list_niches_for_tenant
    except ImportError:
        logger.debug("[allowlist] tenant model unavailable — skipping tenant resolution")
        return None
    niches = list_niches_for_tenant(slug)
    if not niches:
        return None
    valid = set()
    rejected = []
    for n in niches:
        if n in _VALID_NICHE_IDS:
            valid.add(n)
        else:
            rejected.append(n)
    if rejected:
        logger.warning(
            "Tenant %r had unknown niche IDs %r in its bindings — dropped. Valid IDs: %s",
            slug,
            rejected,
            sorted(_VALID_NICHE_IDS),
        )
    return valid if valid else None


def get_allowed_niches() -> set[str] | None:
    """Return the set of niche IDs the current user is authorized for.

    * ``None`` (the default) means "unrestricted" — the caller must
      treat this as "see / approve everything" to preserve the
      pre-PR-539 single-admin behavior.
    * A non-empty set means scope to those niche IDs.
    * An empty set is structurally impossible (the parser collapses
      empty/all-invalid lists to None) — callers don't need to
      handle that edge case.

    ## Resolution order (PR #560, 2026-06-25)

      1. Explicit list via ``REVIEW_NICHE_ALLOWLIST_<USER>`` env var.
         Deterministic, no DB roundtrip, today's behavior unchanged.
      2. **Users table lookup** (PR #560 — this PR). Resolves
         username → ``users.tenant_id`` → ``list_niches_for_tenant``.
         No env config needed; operator just needs a row in the
         users table (PR #559 seeds the 'admin' row at migration time).
      3. Tenant binding via ``REVIEW_TENANT_SLUG_<USER>`` env var
         (PR #558). Backward-compat fallback for operators not yet
         in the users table.
      4. None (unrestricted) — auth-disabled or no config.

    Step 1 wins when both env vars are set for the same user — the
    explicit list is the more constrained choice (operators can pin
    an exception on a tenant-bound user). A WARNING fires when both
    are set to surface the override.

    Step 2 is consulted BEFORE step 3 because the users table is
    the eventual source of truth (PR #558's env-tenant path is
    explicitly the "user not yet in DB" fallback). When the user
    row exists, the DB binding wins — even if a TENANT_SLUG env var
    is also set. A WARNING fires in that conflict case too.

    Reads at every call (not cached) so an env reload OR a
    tenant_niches row insert/delete OR a users.tenant_id update
    takes effect on next request without a process restart. The
    tenant lookup has a 5s connect timeout so a transient DB
    outage degrades to None (unrestricted) rather than blocking
    the request.
    """
    username = _username_for_session()
    if not username:
        # Auth disabled (localhost-only mode) → no scoping possible
        # because there's no logged-in user concept. Returns None to
        # preserve current "see everything" behavior.
        return None
    env_key = _env_key_for_user(username)
    raw = os.environ.get(env_key, "")
    parsed = _parse_allowlist(raw)
    # Step 1: explicit env-var allowlist wins. If set, also surface
    # any concurrent tenant-slug binding as a WARNING so operators
    # know the env list is overriding it.
    if parsed is not None:
        tenant_key = _tenant_slug_env_key_for_user(username)
        if os.environ.get(tenant_key, "").strip():
            logger.warning(
                "Both %s and %s are set; explicit env-var allowlist wins. "
                "Unset %s to use the tenant-derived list.",
                env_key,
                tenant_key,
                env_key,
            )
        return parsed
    # Step 2: users-table lookup (PR #560). The users table is the
    # eventual source of truth; if a row exists for this username,
    # its tenant binding wins over the env-slug fallback below.
    user_allowlist = _allowlist_from_user_record(username)
    if user_allowlist is not None:
        # Surface any concurrent TENANT_SLUG env var so the operator
        # knows the DB is winning over their env config.
        tenant_key = _tenant_slug_env_key_for_user(username)
        if os.environ.get(tenant_key, "").strip():
            logger.warning(
                "%s is set but users table row also exists for %r; "
                "users table tenant binding wins. Unset %s to silence "
                "this warning.",
                tenant_key,
                username,
                tenant_key,
            )
        return user_allowlist
    # Step 3: tenant-slug env fallback (PR #558). For operators not
    # yet in the users table.
    tenant_key = _tenant_slug_env_key_for_user(username)
    slug = os.environ.get(tenant_key, "").strip()
    if slug:
        return _allowlist_from_tenant(slug)
    # Step 4: unrestricted (pre-PR-539 default).
    return None


def is_niche_allowed(niche_id: str) -> bool:
    """Convenience wrapper: True iff ``niche_id`` is in the current
    user's allowlist OR the user is unrestricted (None allowlist).

    Endpoints adopt this when filtering a single resource (e.g.
    ``GET /api/v1/blueprints/<id>``). For list endpoints, prefer
    calling ``get_allowed_niches()`` once and filtering the list
    in-place (one resolution vs N lookups).
    """
    allowed = get_allowed_niches()
    if allowed is None:
        return True
    return niche_id in allowed
