"""PR #558 (2026-06-25) — niche_allowlist tenant-slug fallback.

Resolution order pinned by these tests:

  1. ``REVIEW_NICHE_ALLOWLIST_<USER>`` explicit env list → wins
  2. ``REVIEW_TENANT_SLUG_<USER>`` env slug → consult tenant model
  3. None (unrestricted, pre-PR-539 default)

PR #557 shipped the tenant model (tenants + tenant_niches table +
``list_niches_for_tenant`` read helper). This PR makes the existing
SR-F allowlist resolver consult that helper when the explicit env
list isn't set, so onboarding can manage allowlists via SQL on
``tenant_niches`` instead of editing env vars per-operator.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# ── source pins ────────────────────────────────────────────────────


def test_source_pin_tenant_slug_helper_present():
    """The tenant-slug env-key + allowlist-from-tenant helpers exist
    with the expected names. Pin function names so a future refactor
    that renames or inlines them surfaces in tests."""
    import server.auth.niche_allowlist as mod

    src = Path(mod.__file__).read_text()
    assert "def _tenant_slug_env_key_for_user(username: str) -> str:" in src
    assert "def _allowlist_from_tenant(slug: str) -> set[str] | None:" in src
    assert "PR #558" in src


def test_source_pin_resolution_order_documented():
    """The resolution order MUST be documented in get_allowed_niches's
    docstring so a future reader sees the ordering choice without
    having to reverse-engineer the code path."""
    import server.auth.niche_allowlist as mod

    src = Path(mod.__file__).read_text()
    assert "Resolution order" in src
    assert "REVIEW_TENANT_SLUG_<USER>" in src
    # Step 1 wins (the explicit-list-wins choice — pin the rationale)
    assert "explicit env-var allowlist wins" in src


# ── env-var still wins ─────────────────────────────────────────────


def _scope_user(monkeypatch, username, env_allowlist=None, tenant_slug=None):
    """Set up a session for `username` with optional env_allowlist
    and/or tenant_slug env-vars set. Mirrors the helper used in the
    SR-F endpoint tests."""
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


def test_explicit_env_list_wins_over_tenant_slug(monkeypatch, caplog):
    """When BOTH env-var allowlist and tenant-slug are set, env-var
    wins (deterministic — operator can pin an exception on a tenant-
    bound user). A WARNING surfaces the override so the operator
    knows the slug is being ignored."""
    import logging

    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "hybrid_op", env_allowlist="gaming", tenant_slug="acme")

    # Stub the tenant resolution so we can assert it's NOT called
    with patch("server.auth.niche_allowlist._allowlist_from_tenant") as mock_tenant:
        with caplog.at_level(logging.WARNING):
            result = get_allowed_niches()
    assert result == {"gaming"}
    mock_tenant.assert_not_called()  # env-list short-circuits before tenant lookup
    # Override warning fires so the operator knows the slug is ignored
    assert any("explicit env-var allowlist wins" in r.message for r in caplog.records)


def test_only_env_list_no_warning(monkeypatch, caplog):
    """When ONLY the env-var allowlist is set (no tenant_slug), no
    override warning fires — that warning is specifically about the
    both-set conflict case."""
    import logging

    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "env_only_op", env_allowlist="gaming,sports")
    with caplog.at_level(logging.WARNING):
        result = get_allowed_niches()
    assert result == {"gaming", "sports"}
    assert not any("explicit env-var allowlist wins" in r.message for r in caplog.records)


# ── tenant-slug fallback ───────────────────────────────────────────


def test_tenant_slug_resolves_via_helper(monkeypatch):
    """When only tenant_slug is set, the resolver consults
    ``list_niches_for_tenant`` from PR #557 and converts the result
    into the SR-F allowlist set."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "acme_op", tenant_slug="acme")
    with patch(
        "genlab_core.auth.tenants.list_niches_for_tenant",
        return_value=["gaming", "movies"],
    ) as mock_lookup:
        result = get_allowed_niches()
    assert result == {"gaming", "movies"}
    mock_lookup.assert_called_once_with("acme")


def test_tenant_slug_validates_niche_ids(monkeypatch, caplog):
    """A tenant's bindings could include legacy/unknown niche IDs
    (data drift). The resolver drops them with a WARNING — same
    validation contract as the env-var path."""
    import logging

    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "drift_op", tenant_slug="drifted")
    with patch(
        "genlab_core.auth.tenants.list_niches_for_tenant",
        return_value=["gaming", "fashion", "anime"],  # "fashion" no longer exists
    ):
        with caplog.at_level(logging.WARNING):
            result = get_allowed_niches()
    assert result == {"gaming", "anime"}  # fashion dropped
    assert any("'fashion'" in r.message for r in caplog.records)


def test_tenant_slug_returns_none_on_empty_bindings(monkeypatch):
    """Tenant exists but has no niche bindings (cold-start onboarding
    state) → None (unrestricted). This is the FAIL-OPEN choice for
    misconfigured tenants — better than fail-closed locking the
    operator out of everything during setup."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "new_op", tenant_slug="cold_start_tenant")
    with patch("genlab_core.auth.tenants.list_niches_for_tenant", return_value=[]):
        result = get_allowed_niches()
    assert result is None


def test_tenant_slug_returns_none_when_helper_returns_empty(monkeypatch):
    """Same fail-open choice when the tenant helper returns [] from
    its own fail-close path (no DB, DSN missing, connection error).
    Important: this preserves the pre-PR-558 'no env list' → None
    behavior so deploying this PR before the tenant model migration
    landed doesn't break operators."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "any_op", tenant_slug="any_tenant")
    with patch("genlab_core.auth.tenants.list_niches_for_tenant", return_value=[]):
        result = get_allowed_niches()
    assert result is None


def test_tenant_slug_with_only_invalid_niches_returns_none(monkeypatch):
    """If ALL of a tenant's bindings are invalid niches (severe
    data drift), the validated set is empty → None. Caller fails
    open rather than locking the operator out entirely."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "drift_only_op", tenant_slug="all_drift")
    with patch(
        "genlab_core.auth.tenants.list_niches_for_tenant",
        return_value=["fashion", "crypto"],  # both invalid
    ):
        result = get_allowed_niches()
    assert result is None


# ── no config = no scoping (backward compat) ───────────────────────


def test_no_env_no_tenant_returns_none(monkeypatch):
    """Neither env-var allowlist nor tenant-slug → None (pre-PR-539
    default, unrestricted). Backward compat for every operator who
    hasn't been migrated to either system."""
    from server.auth.niche_allowlist import get_allowed_niches

    _scope_user(monkeypatch, "legacy_op")  # no env, no slug
    with patch("server.auth.niche_allowlist._allowlist_from_tenant") as mock_tenant:
        result = get_allowed_niches()
    assert result is None
    # Tenant resolver not called — saves a DB roundtrip on the
    # most common (unrestricted) path
    mock_tenant.assert_not_called()


def test_tenant_model_import_failure_falls_through(monkeypatch):
    """If genlab_core.auth.tenants can't be imported (e.g. against
    an old core version), the helper logs at DEBUG and returns None.
    The dashboard must NOT crash because of a missing optional
    dependency on the tenant model."""
    from server.auth import niche_allowlist as mod

    _scope_user(monkeypatch, "isolated_op", tenant_slug="something")
    # Simulate ImportError by patching the import location
    with patch.dict("sys.modules", {"genlab_core.auth.tenants": None}):
        result = mod.get_allowed_niches()
    assert result is None


# ── tenant-key construction symmetry ───────────────────────────────


def test_tenant_slug_env_key_matches_env_allowlist_format():
    """``_tenant_slug_env_key_for_user`` must use the SAME username
    sanitization as ``_env_key_for_user``. Otherwise an operator
    named ``foo-bar`` could set ``REVIEW_NICHE_ALLOWLIST_FOO_BAR``
    but the tenant lookup would search a different key."""
    from server.auth.niche_allowlist import (
        _env_key_for_user,
        _tenant_slug_env_key_for_user,
    )

    for username in ["plain", "foo-bar", "with.dot", "MiXeDcAsE", "with_underscore"]:
        env_key = _env_key_for_user(username)
        tenant_key = _tenant_slug_env_key_for_user(username)
        # The suffix (after the prefix) must match
        env_suffix = env_key.removeprefix("REVIEW_NICHE_ALLOWLIST_")
        tenant_suffix = tenant_key.removeprefix("REVIEW_TENANT_SLUG_")
        assert env_suffix == tenant_suffix, (
            f"username sanitization drift: {env_suffix=} vs {tenant_suffix=}"
        )
