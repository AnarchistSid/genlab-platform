"""Tests for the SR-A/C/D ContextVar GUC shim.

Audit context (post-Wave-4, 2026-06-17): 34 direct ``psycopg.connect``
sites in the codebase don't set ``app.niche_id`` GUC. SR-B's WITH
CHECK only protects PostgresBackend writes; the long tail is wide
open. This shim is the foundation for migrating those sites.

Tests pin:
  * ContextVar set/reset semantics (composes via tokens)
  * niche_scope context manager
  * resolve_niche precedence (explicit > ContextVar > None)
  * pg_connect's tenant-resolution + SET-config behaviour
  * Phase-1 admin-mode fallback (current default)
  * Phase-2 fail-closed when GENLAB_REQUIRE_TENANT_GUC=1
  * MissingTenantContext raised with actionable message
  * Connection leak guard when GUC set fails
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from genlab_core.storage.tenant_context import (
    CURRENT_NICHE,
    MissingTenantContext,
    is_tenant_required,
    niche_scope,
    pg_connect,
    resolve_niche,
)

# ── ContextVar mechanics ──────────────────────────────────────────────


class TestContextVarBasics:
    def setup_method(self):
        # Force a clean ContextVar before each test
        CURRENT_NICHE.set(None)

    def test_default_is_none(self):
        assert CURRENT_NICHE.get() is None

    def test_set_via_scope_visible_inside(self):
        with niche_scope("gaming"):
            assert CURRENT_NICHE.get() == "gaming"

    def test_scope_resets_on_exit(self):
        with niche_scope("gaming"):
            pass
        assert CURRENT_NICHE.get() is None

    def test_nested_scopes_compose(self):
        with niche_scope("gaming"):
            with niche_scope("anime"):
                assert CURRENT_NICHE.get() == "anime"
            assert CURRENT_NICHE.get() == "gaming"
        assert CURRENT_NICHE.get() is None

    def test_scope_reset_even_on_exception(self):
        with pytest.raises(RuntimeError, match="boom"):
            with niche_scope("gaming"):
                raise RuntimeError("boom")
        assert CURRENT_NICHE.get() is None


# ── resolve_niche precedence ──────────────────────────────────────────


class TestResolveNiche:
    def setup_method(self):
        CURRENT_NICHE.set(None)

    def test_explicit_overrides_contextvar(self):
        with niche_scope("contextvar_niche"):
            assert resolve_niche("explicit_niche") == "explicit_niche"

    def test_contextvar_used_when_no_explicit(self):
        with niche_scope("contextvar_niche"):
            assert resolve_niche(None) == "contextvar_niche"

    def test_none_when_neither_set(self):
        assert resolve_niche(None) is None

    def test_empty_string_explicit_is_admin_mode_not_none(self):
        """An explicit empty string means "admin mode" — distinct from
        None which means "no opinion, ask the context".
        """
        assert resolve_niche("") == ""


# ── Feature flag ──────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_flag_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_REQUIRE_TENANT_GUC", raising=False)
        assert is_tenant_required() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "yes", "on", "TRUE"])
    def test_flag_on_for_truthy_values(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_REQUIRE_TENANT_GUC", val)
        assert is_tenant_required() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
    def test_flag_off_for_falsy_values(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_REQUIRE_TENANT_GUC", val)
        assert is_tenant_required() is False


# ── pg_connect resolution + SET behaviour ─────────────────────────────


class TestPgConnect:
    def setup_method(self):
        CURRENT_NICHE.set(None)

    def _fake_psycopg(self, fail_setconfig=False):
        """Build a psycopg mock that captures SET calls."""
        cur = MagicMock()
        cur.__enter__ = MagicMock(return_value=cur)
        cur.__exit__ = MagicMock(return_value=False)

        captured_sets: list = []

        def execute(sql, params=None):
            if fail_setconfig and "set_config" in sql:
                raise RuntimeError("simulated GUC failure")
            captured_sets.append((sql, params))

        cur.execute = execute

        conn = MagicMock()
        conn.cursor.return_value = cur

        psycopg_mod = MagicMock()
        psycopg_mod.connect.return_value = conn
        return psycopg_mod, conn, captured_sets

    def test_explicit_niche_passed_to_set_config(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        psycopg_mod, conn, captured = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            pg_connect(niche_id="gaming")
        assert any("set_config" in sql and params == ("gaming",) for sql, params in captured), (
            f"expected SET app.niche_id = 'gaming', got: {captured}"
        )

    def test_contextvar_niche_passed_to_set_config(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        psycopg_mod, conn, captured = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            with niche_scope("anime"):
                pg_connect()
        assert any("set_config" in sql and params == ("anime",) for sql, params in captured)

    def test_explicit_overrides_contextvar(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        psycopg_mod, conn, captured = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            with niche_scope("gaming"):
                pg_connect(niche_id="movies")
        # Explicit "movies" wins over context's "gaming"
        assert any("set_config" in sql and params == ("movies",) for sql, params in captured)

    def test_phase1_admin_fallback_when_no_tenant(self, monkeypatch):
        """Default behaviour: no tenant → empty string admin-mode +
        DEBUG log. Connection still returned, no exception."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        monkeypatch.delenv("GENLAB_REQUIRE_TENANT_GUC", raising=False)
        psycopg_mod, conn, captured = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            result = pg_connect()
        assert result is conn
        assert any("set_config" in sql and params == ("",) for sql, params in captured)

    def test_phase2_fail_closed_when_no_tenant(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        monkeypatch.setenv("GENLAB_REQUIRE_TENANT_GUC", "1")
        psycopg_mod, _, _ = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            with pytest.raises(MissingTenantContext, match="without a tenant identity"):
                pg_connect()

    def test_phase2_explicit_niche_still_works(self, monkeypatch):
        """Fail-closed only fires when NO tenant is available —
        explicit ``niche_id=`` should still bypass the check."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        monkeypatch.setenv("GENLAB_REQUIRE_TENANT_GUC", "1")
        psycopg_mod, conn, captured = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            result = pg_connect(niche_id="gaming")
        assert result is conn

    def test_phase2_admin_mode_explicit_allowed(self, monkeypatch):
        """Even in fail-closed mode, explicit ``niche_id="all"`` (or
        empty string) is the operator's "I really mean admin" signal."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        monkeypatch.setenv("GENLAB_REQUIRE_TENANT_GUC", "1")
        psycopg_mod, conn, captured = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            result = pg_connect(niche_id="all")
        assert result is conn
        assert any("set_config" in sql and params == ("all",) for sql, params in captured)

    def test_no_dsn_no_env_raises_runtimeerror(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        psycopg_mod, _, _ = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            with pytest.raises(RuntimeError, match="DATABASE_URL"):
                pg_connect()

    def test_explicit_dsn_overrides_env(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://env-url")
        psycopg_mod, conn, captured = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            pg_connect(dsn="postgresql://explicit-url", niche_id="gaming")
        # The first positional arg to connect() should be the explicit DSN
        assert psycopg_mod.connect.call_args.args[0] == "postgresql://explicit-url"

    def test_connect_kwargs_passthrough(self, monkeypatch):
        """Extra kwargs (like connect_timeout, row_factory) pass through
        to psycopg.connect untouched — operators rely on these."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        psycopg_mod, conn, captured = self._fake_psycopg()
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            pg_connect(niche_id="gaming", connect_timeout=5, autocommit=True)
        assert psycopg_mod.connect.call_args.kwargs == {
            "connect_timeout": 5,
            "autocommit": True,
        }

    def test_guc_set_failure_closes_connection_before_reraise(self, monkeypatch):
        """If the SET app.niche_id command fails, we don't want to leak
        a half-initialised connection — close it before re-raising so
        the pool stays healthy."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")
        psycopg_mod, conn, _ = self._fake_psycopg(fail_setconfig=True)
        with patch.dict("sys.modules", {"psycopg": psycopg_mod}):
            with pytest.raises(RuntimeError, match="simulated GUC failure"):
                pg_connect(niche_id="gaming")
        conn.close.assert_called_once()
