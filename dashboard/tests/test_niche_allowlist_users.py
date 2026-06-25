"""PR #560 (2026-06-25) — niche_allowlist consumes the users table.

Adds a NEW resolution step between PR #558's env-list (step 1) and
env-tenant-slug (now step 3). The new step 2 looks up the username
in the users table (PR #559's seed includes 'admin' → 'self' tenant)
and resolves the allowlist via the user's tenant.

  1. REVIEW_NICHE_ALLOWLIST_<USER>=… explicit env list (wins)
  2. users-table lookup → user.tenant_id → tenant niches (NEW)
  3. REVIEW_TENANT_SLUG_<USER>=acme env fallback (PR #558)
  4. None (unrestricted)

Step 2 is the EVENTUAL source of truth — PR #558's env-slug path is
explicitly the "user not yet in DB" fallback. Once a user row
exists, the DB binding wins, even when a concurrent TENANT_SLUG env
var is set (a WARNING fires to surface the override).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch

# ── source pins ────────────────────────────────────────────────────


def test_source_pin_user_record_helper_present():
    """The _allowlist_from_user_record helper exists with the
    expected signature. Pin function name + PR anchor."""
    import server.auth.niche_allowlist as mod

    src = Path(mod.__file__).read_text()
    assert "def _allowlist_from_user_record(username: str) -> set[str] | None:" in src
    assert "PR #560" in src


def test_source_pin_resolution_order_documents_step_2():
    """The new step 2 (users-table lookup) MUST be documented in the
    get_allowed_niches docstring so future readers understand the
    DB-wins-over-env-slug precedence without reverse-engineering."""
    import server.auth.niche_allowlist as mod

    src = Path(mod.__file__).read_text()
    assert "Users table lookup" in src
    # Match the rationale phrase regardless of line wrap (docstring
    # may wrap mid-phrase). Collapse whitespace before checking.
    import re

    normalized = re.sub(r"\s+", " ", src)
    assert "users table is the eventual source of truth" in normalized
    # The DB-wins-over-env-slug rationale (anti-refactor pin)
    assert "users table tenant binding wins" in src


def test_source_pin_step_2_uses_uuid_branch():
    """user.tenant_id is a UUID — list_niches_for_tenant must be
    called with the UUID branch (PK-indexed), not the slug join.
    Pin the SQL shape choice by checking the helper code."""
    import server.auth.niche_allowlist as mod

    src = Path(mod.__file__).read_text()
    # The wire passes user.tenant_id directly (UUID input)
    assert "list_niches_for_tenant(user.tenant_id)" in src


# ── behavioral pins ────────────────────────────────────────────────


def _scope_user(monkeypatch, username, env_allowlist=None, tenant_slug=None):
    """Set up a session for `username` with optional env vars."""
    monkeypatch.setenv("REVIEW_AUTH_USER", username)
    env_key = "REVIEW_NICHE_ALLOWLIST_" + "".join(
        c.upper() if c.isalnum() else "_" for c in username
    )
    tenant_key = "REVIEW_TENANT_SLUG_" + "".join(
        c.upper() if c.isalnum() else "_" for c in username
    )
    if env_allowlist is not None:
        monkeypatch.setenv(env_key, env_allowlist)
    else:
        monkeypatch.delenv(env_key, raising=False)
    if tenant_slug is not None:
        monkeypatch.setenv(tenant_key, tenant_slug)
    else:
        monkeypatch.delenv(tenant_key, raising=False)
    monkeypatch.setattr(
        "server.auth.niche_allowlist._username_for_session",
        lambda: username,
    )


def _fake_user(tenant_id=None):
    """Build a minimal User DTO for mocking get_user_by_username."""
    from genlab_core.auth.users import User

    return User(
        id=uuid.uuid4(),
        username="admin",
        display_name="Self (admin)",
        tenant_id=tenant_id or uuid.uuid4(),
        role="admin",
        status="active",
    )


def test_users_table_lookup_wins_over_tenant_slug(monkeypatch, caplog):
    """When user row exists in DB AND TENANT_SLUG env is set, DB wins
    AND a WARNING fires. The DB is the eventual source of truth."""
    import logging

    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "admin", tenant_slug="some_other_tenant")
    tid = uuid.uuid4()
    user = _fake_user(tenant_id=tid)

    with patch("genlab_core.auth.users.get_user_by_username", return_value=user):
        with patch(
            "genlab_core.auth.tenants.list_niches_for_tenant",
            return_value=["gaming", "anime"],
        ) as mock_lookup:
            with caplog.at_level(logging.WARNING):
                result = get_allowed_niches()
    assert result == {"gaming", "anime"}
    # Verify list_niches_for_tenant was called with the UUID (not slug)
    mock_lookup.assert_called_once_with(tid)
    assert any("users table tenant binding wins" in r.message for r in caplog.records)


def test_users_table_lookup_no_warning_when_no_tenant_slug(monkeypatch, caplog):
    """When only the DB user is set (no TENANT_SLUG env), no warning
    fires — that warning is specifically about the both-set conflict."""
    import logging

    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "admin")  # no env, no slug
    user = _fake_user()

    with patch("genlab_core.auth.users.get_user_by_username", return_value=user):
        with patch(
            "genlab_core.auth.tenants.list_niches_for_tenant",
            return_value=["gaming"],
        ):
            with caplog.at_level(logging.WARNING):
                result = get_allowed_niches()
    assert result == {"gaming"}
    assert not any("users table tenant binding wins" in r.message for r in caplog.records)


def test_explicit_env_list_still_wins_over_users_table(monkeypatch):
    """Step 1 (env list) MUST still win even when the user is in DB.
    Operators need to be able to pin an exception on a DB-bound user."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "admin", env_allowlist="movies")

    # Even if the user record would resolve to {gaming, anime},
    # the explicit env list takes precedence.
    with patch("genlab_core.auth.users.get_user_by_username") as mock_user:
        result = get_allowed_niches()
    assert result == {"movies"}
    # Critical: user lookup not even attempted (step 1 short-circuits)
    mock_user.assert_not_called()


def test_no_user_falls_through_to_tenant_slug(monkeypatch):
    """When the user row doesn't exist, fall through to the
    PR #558 TENANT_SLUG env path."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "legacy_op", tenant_slug="acme")

    with patch("genlab_core.auth.users.get_user_by_username", return_value=None):
        with patch(
            "genlab_core.auth.tenants.list_niches_for_tenant",
            return_value=["movies"],
        ) as mock_lookup:
            result = get_allowed_niches()
    assert result == {"movies"}
    # The TENANT_SLUG fallback was used (slug input, not UUID)
    mock_lookup.assert_called_once_with("acme")


def test_no_user_no_slug_returns_none(monkeypatch):
    """No DB user + no TENANT_SLUG env → None (unrestricted).
    Backward compat for pre-PR-558 operators not yet migrated."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "isolated_op")
    with patch("genlab_core.auth.users.get_user_by_username", return_value=None):
        result = get_allowed_niches()
    assert result is None


def test_user_with_empty_tenant_bindings_returns_none(monkeypatch):
    """User row exists but tenant has zero niche bindings (cold-start
    onboarding state) → None (unrestricted). Fail-OPEN choice for
    misconfigured tenants — same as PR #558."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "admin")
    user = _fake_user()

    with patch("genlab_core.auth.users.get_user_by_username", return_value=user):
        with patch("genlab_core.auth.tenants.list_niches_for_tenant", return_value=[]):
            result = get_allowed_niches()
    assert result is None


def test_user_tenant_with_drift_bindings_drops_unknowns(monkeypatch, caplog):
    """Drift defense: if the user's tenant has unknown niche IDs in
    its bindings, drop them with a WARNING (same contract as the
    env-var path)."""
    import logging

    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "drift_op")
    user = _fake_user()

    with patch("genlab_core.auth.users.get_user_by_username", return_value=user):
        with patch(
            "genlab_core.auth.tenants.list_niches_for_tenant",
            return_value=["gaming", "fashion", "anime"],  # fashion invalid
        ):
            with caplog.at_level(logging.WARNING):
                result = get_allowed_niches()
    assert result == {"gaming", "anime"}
    assert any("'fashion'" in r.message for r in caplog.records)


def test_user_lookup_import_error_falls_through_gracefully(monkeypatch):
    """If the users/tenants module can't be imported (e.g. running
    against an old core), the helper returns None and step 3
    (TENANT_SLUG env) still gets a chance to run."""
    from server.auth.niche_allowlist import _allowlist_from_user_record

    # Simulate ImportError on both modules
    with patch.dict(
        "sys.modules",
        {"genlab_core.auth.users": None, "genlab_core.auth.tenants": None},
    ):
        result = _allowlist_from_user_record("admin")
    assert result is None


def test_step_2_consulted_even_with_concurrent_tenant_slug(monkeypatch):
    """The user-table lookup runs even when TENANT_SLUG env is also
    set — it's not a fall-through behavior, it's a precedence one.
    Pin this so a future refactor doesn't accidentally swap the
    'tenant_slug exists → skip user lookup' logic."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "admin", tenant_slug="ignored")
    user = _fake_user()

    with patch("genlab_core.auth.users.get_user_by_username", return_value=user) as mock_user:
        with patch(
            "genlab_core.auth.tenants.list_niches_for_tenant",
            return_value=["gaming"],
        ):
            get_allowed_niches()
    # User lookup WAS called (not short-circuited by TENANT_SLUG)
    mock_user.assert_called_once_with("admin")


def test_full_access_tenant_returns_none_for_fast_path(monkeypatch):
    """When a user's tenant binds to ALL valid niches, the resolver
    returns None — same behavior as 'no allowlist set' / 'admin
    unrestricted'. This is the full-access shortcut: downstream SR-F
    endpoints' fast-path applies, so we don't waste per-record
    enumeration on operators who effectively see everything.

    The 'admin' seed row from PR #559 binds to the 'self' tenant which
    PR #557 seeded with all 5 niches → admin gets the unrestricted
    fast-path despite being a real user record. Backward compat.
    """
    from server.auth.niche_allowlist import _VALID_NICHE_IDS, get_allowed_niches

    _scope_user(monkeypatch, "admin")
    user = _fake_user()

    with patch("genlab_core.auth.users.get_user_by_username", return_value=user):
        with patch(
            "genlab_core.auth.tenants.list_niches_for_tenant",
            return_value=sorted(_VALID_NICHE_IDS),
        ):
            result = get_allowed_niches()
    assert result is None, "Full-access tenant should resolve to unrestricted (None)"


def test_partial_access_tenant_returns_specific_set(monkeypatch):
    """When a tenant binds to fewer than ALL valid niches, the
    resolver returns the explicit set (full-access shortcut does
    NOT apply). Pin the boundary: 4 of 5 niches → set, 5 of 5 → None."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "partial_op")
    user = _fake_user()

    with patch("genlab_core.auth.users.get_user_by_username", return_value=user):
        with patch(
            "genlab_core.auth.tenants.list_niches_for_tenant",
            return_value=["gaming", "sports", "movies", "anime"],  # 4/5
        ):
            result = get_allowed_niches()
    assert result == {"gaming", "sports", "movies", "anime"}


def test_no_user_no_slug_no_env_no_db_roundtrip_overhead(monkeypatch):
    """Performance guard: the common no-config case must not hit
    the DB for the user lookup. The `_username_for_session` returns
    a value, but if there's no DATABASE_URL the user helper
    fail-closes to None on the first call without further round-trips.

    Pin via call count: get_user_by_username is called once (the
    fail-close happens INSIDE the helper, not at the caller). The
    SECOND call (for TENANT_SLUG fallback) would only happen if a
    slug was set."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "any_op")
    with patch("genlab_core.auth.users.get_user_by_username", return_value=None) as mock_user:
        with patch(
            "genlab_core.auth.tenants.list_niches_for_tenant",
        ) as mock_tenant:
            result = get_allowed_niches()
    assert result is None
    assert mock_user.call_count == 1  # one lookup, returned None, fall-through
    # list_niches_for_tenant NOT called (no user, no slug)
    mock_tenant.assert_not_called()
