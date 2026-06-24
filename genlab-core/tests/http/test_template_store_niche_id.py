"""PR #529 (2026-06-24): TemplateStore.create_template passes
``niche_id`` to the backend wrapper (SR-C migration, 3rd store).

TemplateStore is the simplest of the 5 stores: single-row create
only, no bulk path. Same migration pattern as PR #528's StoryStore
create_story — the template dict carries ``niche_id`` from callers,
and we just need to surface it as a record field AND pass the kwarg
so the backend's SET LOCAL fires.

The Templates table on prod has a ``niche_id`` column (verified
2026-06-24 schema probe) — so this IS a tenant-scoped table and SR-C
applies. Per-tenant content templates for the multi-niche pipeline.

If these pins regress, every new template creation lands without
tenant binding, and the row's read-time RLS check evaluates
against the wrong scope.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── source pins ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def store_source() -> str:
    import genlab_core.http.template_store as mod

    return Path(mod.__file__).read_text()


def test_template_create_injects_niche_id_field(store_source):
    """The record must carry ``niche_id`` when the template dict has
    one. Mirrors the BlueprintStore/StoryStore pattern."""
    assert 'if template.get("niche_id"):' in store_source
    assert 'fields["niche_id"] = template["niche_id"]' in store_source


def test_template_create_passes_niche_id_kwarg(store_source):
    """The kwarg forward MUST reach backend.create — otherwise the
    SET LOCAL optimization is skipped and only the record-side tag
    lands (downgraded SR-C)."""
    assert 'niche_id=template.get("niche_id"),' in store_source


def test_pr_529_anchor_present(store_source):
    """A future archaeologist should be able to grep for PR #529 and
    land at the comment that explains the migration."""
    assert "PR #529" in store_source


# ── behavioral pins ────────────────────────────────────────────────


def _build_store():
    """Construct a TemplateStore with stubbed backend."""
    from genlab_core.http.template_store import TemplateStore

    proxy = MagicMock()
    proxy.create.return_value = {"id": "tmpl-new-id"}
    store = TemplateStore(backend=lambda table: proxy)
    return store, proxy


def _minimal_template(**overrides):
    base = {
        "template_id": "tmpl_001",
        "name": "Test Template",
        "format": "reel_60s",
    }
    base.update(overrides)
    return base


def test_template_with_niche_id_forwards_to_backend():
    store, proxy = _build_store()

    store.create_template(_minimal_template(niche_id="gaming"))

    proxy.create.assert_called_once()
    call_kwargs = proxy.create.call_args.kwargs
    assert call_kwargs.get("niche_id") == "gaming", (
        f"Expected niche_id='gaming' in backend.create kwargs; got {call_kwargs.get('niche_id')!r}."
    )
    # Record body also carries the tag (defense in depth — read-time
    # RLS reads the row's column, not the GUC)
    fields = proxy.create.call_args.args[1]
    assert fields["niche_id"] == "gaming"


def test_template_without_niche_id_passes_none_no_field():
    """Backward compat: a template without niche_id passes None
    (backend admin-mode fallback) AND record has no niche_id field
    — preserves the long-standing dict shape."""
    store, proxy = _build_store()

    store.create_template(_minimal_template())  # no niche_id

    call_kwargs = proxy.create.call_args.kwargs
    assert call_kwargs.get("niche_id") is None
    fields = proxy.create.call_args.args[1]
    assert "niche_id" not in fields


def test_template_typecast_kwarg_preserved():
    """The existing typecast=True must survive the migration —
    important because Templates uses typed columns (best_for as
    array, max_slides as int)."""
    store, proxy = _build_store()
    store.create_template(_minimal_template(niche_id="anime"))
    assert proxy.create.call_args.kwargs.get("typecast") is True
