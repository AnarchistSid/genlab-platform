"""PR #530 (2026-06-24): ABTestStore.create_ab_test passes
``niche_id`` to the backend wrapper (SR-C migration, 4th store).

ABTestStore.create_ab_test takes the test dict directly (no
field-builder pattern like Templates/Stories), so the migration is
just adding ``niche_id=test.get(\"niche_id\")`` to the backend.create
call. The test dict already carries ``niche_id`` from callers.

The ab_tests table on prod has a ``niche_id`` column (verified
2026-06-24 schema probe — 117 rows, tenant-scoped).

Additional pin: the proxy-truthy gate still short-circuits when
the table isn't configured. Don't accidentally regress that to
attempt a write that the BacklogClient can't deliver.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── source pins ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def store_source() -> str:
    import genlab_core.http.ab_test_store as mod

    return Path(mod.__file__).read_text()


def test_ab_test_create_passes_niche_id_kwarg(store_source):
    """The kwarg forward MUST reach backend.create. Without it,
    every new A/B test row lands without tenant binding."""
    assert 'niche_id=test.get("niche_id"),' in store_source


def test_pr_530_anchor_present(store_source):
    """PR anchor for archaeology."""
    assert "PR #530" in store_source


# ── behavioral pins ────────────────────────────────────────────────


def _build_store(*, proxy_configured=True):
    """Construct an ABTestStore with stubbed backend.

    ``proxy_configured`` toggles the proxy-truthy gate — when False,
    the store should short-circuit and never call backend.create.
    """
    from genlab_core.http.ab_test_store import ABTestStore

    backend_proxy = MagicMock()
    backend_proxy.create.return_value = {"id": "ab-new-id"}
    proxy_ref = MagicMock() if proxy_configured else None
    store = ABTestStore(ab_tests=proxy_ref, backend=lambda table: backend_proxy)
    return store, backend_proxy


def test_create_with_niche_id_forwards_to_backend():
    store, backend = _build_store()

    store.create_ab_test({"test_id": "ab-001", "name": "hook_v1_vs_v2", "niche_id": "movies"})

    backend.create.assert_called_once()
    call_kwargs = backend.create.call_args.kwargs
    assert call_kwargs.get("niche_id") == "movies"


def test_create_without_niche_id_passes_none():
    """Backward compat — legacy callers without niche_id still work."""
    store, backend = _build_store()

    store.create_ab_test({"test_id": "ab-001", "name": "test"})

    backend.create.assert_called_once()
    assert backend.create.call_args.kwargs.get("niche_id") is None


def test_create_preserves_typecast_kwarg():
    """typecast=True must survive the migration — A/B tests use
    typed columns (variants list, scores dict)."""
    store, backend = _build_store()
    store.create_ab_test({"test_id": "ab-001", "niche_id": "anime"})
    assert backend.create.call_args.kwargs.get("typecast") is True


def test_proxy_unconfigured_short_circuits_no_backend_call():
    """The existing proxy-truthy gate (when AB_Tests table isn't
    configured) must still short-circuit. Otherwise the new kwarg
    would land in a no-table-exists call and the operator sees
    confusing 'no such table' errors instead of the friendly
    'AB_Tests table not configured' WARNING."""
    store, backend = _build_store(proxy_configured=False)

    result = store.create_ab_test({"test_id": "ab-001", "niche_id": "gaming"})

    assert result is None
    backend.create.assert_not_called()
