"""PR #532 (2026-06-24): SR-A — backend + wrapper accept ``niche_id``
on ``get``, ``update``, ``delete``.

SYSTEM-RESEARCH.md §9 flagged SR-A as Critical: pre-PR these three
methods ran ``SET LOCAL app.niche_id = ''`` (admin mode), meaning
any caller with a record_id could read, mutate, or delete rows
across tenants. The fix mirrors PR #517's create()/batch_create()
shape: optional ``niche_id`` kwarg, used in SET LOCAL when set,
falls through to admin-mode when omitted (backward compat).

These pins are SOURCE-LEVEL (not behavioural) because exercising
the real DB path requires a live Postgres pool. Behavioural
coverage will follow once a per-store migration PR ships and the
end-to-end path can be tested through MagicMock proxies.

If these pins regress, the SR-A migration silently re-opens:
  * wrapper-forward broken → store callers passing niche_id= land
    in admin mode without any error
  * SET LOCAL hardcoded to '' → RLS treats every call as admin
  * delete() lacking the guard → cross-tenant erasure becomes
    possible by anyone with the row id

Companion test for SharePoint accept-and-ignore parity also pinned
so backend-agnostic stores (BlueprintStore etc.) can pass
``niche_id=`` uniformly across both backends without TypeError.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

# ── source pins on PostgresBackend.get/update/delete ────────────────


@pytest.fixture(scope="module")
def postgres_source() -> str:
    import genlab_core.storage.postgres as mod

    return Path(mod.__file__).read_text()


def test_backend_get_signature_accepts_niche_id():
    """Source pin: PostgresBackend.get must declare niche_id kwarg."""
    from genlab_core.storage.postgres import PostgresBackend

    sig = inspect.signature(PostgresBackend.get)
    assert "niche_id" in sig.parameters, (
        "PostgresBackend.get must accept niche_id kwarg (SR-A, PR #532)."
    )


def test_backend_update_signature_accepts_niche_id():
    from genlab_core.storage.postgres import PostgresBackend

    sig = inspect.signature(PostgresBackend.update)
    assert "niche_id" in sig.parameters, (
        "PostgresBackend.update must accept niche_id kwarg (SR-A, PR #532)."
    )


def test_backend_delete_signature_accepts_niche_id():
    from genlab_core.storage.postgres import PostgresBackend

    sig = inspect.signature(PostgresBackend.delete)
    assert "niche_id" in sig.parameters, (
        "PostgresBackend.delete must accept niche_id kwarg (SR-A, PR #532)."
    )


def test_backend_set_local_uses_niche_id_not_hardcoded_empty(postgres_source):
    """The pre-PR-532 code had ``SET LOCAL app.niche_id = ''``
    hardcoded inside get/update/delete. After this PR, those callsites
    must read from the kwarg instead.

    The find() method already had the pattern (``niche_id or ''``);
    pin that the same shape now appears in get/update/delete too.
    """
    # Count the `set_config('app.niche_id', '', true)` LITERAL — should
    # be ZERO occurrences after PR #532 (every call site uses
    # `niche_id or ""` now).
    hardcoded_count = postgres_source.count("""set_config('app.niche_id', %s, true)", ("",)""")
    assert hardcoded_count == 0, (
        f"Found {hardcoded_count} hardcoded SET LOCAL app.niche_id='' "
        f"callsites — PR #532 should have replaced them all with "
        f"`niche_id or ''` (matching the existing find() pattern)."
    )
    # The replacement pattern: `(niche_id or "")` appears at all 3 sites
    # in get/update/delete (plus the existing find/all/claim_status —
    # so ≥3 new + 2-3 pre-existing = ≥5).
    new_count = postgres_source.count('(niche_id or "",)')
    assert new_count >= 5, (
        f'Expected ≥5 occurrences of `(niche_id or "",)` after PR #532; '
        f"found {new_count}. Each get/update/delete adds one."
    )


# ── source pins on update()'s tenant-spoofing guard ─────────────────


def test_update_has_tenant_spoofing_guard(postgres_source):
    """update() must reject ``niche_id`` kwarg/field mismatch with a
    clear ValueError — mirrors create()'s guard from PR #517. Without
    this, a caller passing the wrong niche_id silently mutates rows
    in admin mode."""
    assert "(SR-A guard, PR #532)" in postgres_source
    assert "tenant-spoofing attempt" in postgres_source


# ── source pins on wrapper-forward ──────────────────────────────────


def test_wrapper_get_forwards_niche_id(postgres_source):
    """Wrapper PostgresTableProxy.get must declare niche_id and forward
    to backend.get — symmetric with the create() forward (PR #525)."""
    from genlab_core.storage.postgres import PostgresTableProxy

    sig = inspect.signature(PostgresTableProxy.get)
    assert "niche_id" in sig.parameters
    # Forward pin: the wrapper's body must include niche_id=niche_id
    # in BOTH branches (record_id-given and record_id-omitted forms).
    src = inspect.getsource(PostgresTableProxy.get)
    assert src.count("niche_id=niche_id") >= 2, (
        "Wrapper.get must forward niche_id on BOTH overload branches."
    )


def test_wrapper_update_forwards_niche_id():
    from genlab_core.storage.postgres import PostgresTableProxy

    sig = inspect.signature(PostgresTableProxy.update)
    assert "niche_id" in sig.parameters
    src = inspect.getsource(PostgresTableProxy.update)
    # Update wrapper has 2 overload branches (positional shape variant)
    assert src.count("niche_id=niche_id") >= 2


def test_wrapper_delete_forwards_niche_id():
    from genlab_core.storage.postgres import PostgresTableProxy

    sig = inspect.signature(PostgresTableProxy.delete)
    assert "niche_id" in sig.parameters
    src = inspect.getsource(PostgresTableProxy.delete)
    # Delete wrapper has 2 overload branches too
    assert src.count("niche_id=niche_id") >= 2


# ── SharePoint parity ──────────────────────────────────────────────


def test_sharepoint_get_accepts_niche_id_for_parity():
    """SharePoint backend must accept niche_id on get for backend-
    agnostic stores. Same accept-and-ignore pattern as PR #526's
    SharePoint.create migration."""
    from genlab_core.storage.sharepoint import SharePointBackend

    sig = inspect.signature(SharePointBackend.get)
    assert "niche_id" in sig.parameters


def test_sharepoint_update_accepts_niche_id_for_parity():
    from genlab_core.storage.sharepoint import SharePointBackend

    sig = inspect.signature(SharePointBackend.update)
    assert "niche_id" in sig.parameters


def test_sharepoint_delete_accepts_niche_id_for_parity():
    from genlab_core.storage.sharepoint import SharePointBackend

    sig = inspect.signature(SharePointBackend.delete)
    assert "niche_id" in sig.parameters


# ── behavioral pin via wrapper stub ────────────────────────────────


def test_wrapper_forwards_niche_id_through_get_via_stub():
    """End-to-end forward check using a stub backend — confirms the
    kwarg actually reaches backend.get rather than being silently
    swallowed somewhere in the wrapper."""
    from unittest.mock import MagicMock

    from genlab_core.storage.postgres import PostgresTableProxy

    stub_backend = MagicMock()
    stub_backend.get.return_value = {"id": "rec-1"}

    proxy = PostgresTableProxy.__new__(PostgresTableProxy)
    proxy._backend = stub_backend
    proxy._table = "Blueprints"

    proxy.get("rec-1", niche_id="gaming")

    stub_backend.get.assert_called_once()
    assert stub_backend.get.call_args.kwargs.get("niche_id") == "gaming"


def test_wrapper_forwards_niche_id_through_delete_via_stub():
    """Same end-to-end check for delete — the most-destructive method,
    where silent kwarg drop has the worst consequence."""
    from unittest.mock import MagicMock

    from genlab_core.storage.postgres import PostgresTableProxy

    stub_backend = MagicMock()
    proxy = PostgresTableProxy.__new__(PostgresTableProxy)
    proxy._backend = stub_backend
    proxy._table = "Blueprints"

    proxy.delete("rec-1", niche_id="sports")

    stub_backend.delete.assert_called_once()
    assert stub_backend.delete.call_args.kwargs.get("niche_id") == "sports"


def test_wrapper_forwards_niche_id_through_update_via_stub():
    """End-to-end forward check for update across both overload branches.
    Branch 1: ``update(record_id, fields_dict)`` — fields-as-2nd-positional
    """
    from unittest.mock import MagicMock

    from genlab_core.storage.postgres import PostgresTableProxy

    stub_backend = MagicMock()
    proxy = PostgresTableProxy.__new__(PostgresTableProxy)
    proxy._backend = stub_backend
    proxy._table = "Blueprints"

    proxy.update("rec-1", {"status": "PUBLISHED"}, niche_id="anime")

    stub_backend.update.assert_called_once()
    assert stub_backend.update.call_args.kwargs.get("niche_id") == "anime"
