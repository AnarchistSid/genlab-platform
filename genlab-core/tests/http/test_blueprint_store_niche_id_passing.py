"""PR #526 (2026-06-24): BlueprintStore.create_blueprint passes
``niche_id`` to the backend wrapper (SR-C migration, first store).

Architectural prerequisite chain:
  * PR #517 — backend ``create()`` accepts ``niche_id`` + does
    ``SET LOCAL app.niche_id`` before INSERT.
  * PR #525 — wrapper layer (``PostgresTableProxy``) forwards
    the kwarg so it can actually reach the backend.
  * PR #526 (this) — first store-level caller (BlueprintStore)
    actually passes the kwarg, completing the chain end-to-end.

Without these pins the migration silently regresses: a future Edit
removes the kwarg from one of the two backend.create() call sites
(primary or retry-without-unknown-cols path) and the call falls back
to admin-mode INSERT without tenant binding. The retry path is
particularly sneaky — it only triggers on legacy SharePoint schemas
or in-progress Postgres migrations, so the regression would only
surface during operational anomalies (exactly when you don't want
silent failures).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── source pins ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def store_source() -> str:
    import genlab_core.http.blueprint_store as mod

    return Path(mod.__file__).read_text()


def test_primary_create_passes_niche_id(store_source):
    """The main ``backend.create()`` call must forward niche_id from
    the blueprint dict. Without this, the row lands without tenant
    binding even though SR-C plumbing is wired."""
    # The body uses bp_niche_id, computed once at the top of the try.
    assert 'bp_niche_id = blueprint.get("niche_id")' in store_source, (
        "create_blueprint must compute bp_niche_id from the blueprint "
        "dict — single source of truth for both create paths."
    )
    # The primary create call must reference it
    assert "niche_id=bp_niche_id," in store_source, (
        "Primary backend.create() call must pass niche_id=bp_niche_id."
    )


def test_retry_path_also_passes_niche_id(store_source):
    """The retry path (after the UNKNOWN_FIELD_NAME branch strips
    unknown columns) must ALSO pass niche_id. Without it, the
    fallback INSERT lands without tenant binding — exactly the SR-C
    bug class, masquerading as a legacy-column workaround."""
    # Count occurrences of the forward — should be 2 (primary + retry)
    occurrences = store_source.count("niche_id=bp_niche_id,")
    assert occurrences >= 2, (
        f"Expected ≥2 niche_id=bp_niche_id forwards (primary + retry); "
        f"found {occurrences}. The retry path silently dropping the "
        f"kwarg is the most-likely regression shape."
    )


def test_pr_525_wrapper_referenced_in_comment(store_source):
    """The migration comment should reference PR #525 (the wrapper
    forward) as the prerequisite — gives a future archaeologist a
    direct pointer to the architectural change that made this
    possible."""
    assert "PR #525" in store_source or "wrapper-forward" in store_source.lower()


# ── behavioral pins ────────────────────────────────────────────────


def _build_store(backend_callable):
    """Construct a BlueprintStore with stubbed dependencies."""
    from genlab_core.http.blueprint_store import BlueprintStore

    return BlueprintStore(
        sp_call=lambda fn, *args, **kw: fn(*args, **kw),
        backend=backend_callable,
        find_story=lambda story_id: {"id": "story-1", "fields": {}},
        find_template=lambda template_id: None,
        assert_not_scheduled=lambda *a, **kw: None,
    )


def _make_proxy_stub():
    """Mock PostgresTableProxy that captures create() kwargs."""
    proxy = MagicMock()
    proxy.create.return_value = {"id": "new-blueprint-id"}
    return proxy


def test_create_blueprint_forwards_niche_id_from_dict():
    """When ``blueprint["niche_id"]`` is set, that value must reach
    the backend wrapper's ``create()`` call."""
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.create_blueprint(
        {
            "candidate_id": "cand-1",
            "story_id": "story-1",
            "niche_id": "gaming",
            "hook": "test hook",
            "validation_status": {},
        }
    )

    # The call to create() must have included niche_id=gaming
    proxy.create.assert_called_once()
    call_kwargs = proxy.create.call_args.kwargs
    assert call_kwargs.get("niche_id") == "gaming", (
        f"Expected niche_id='gaming' in backend.create kwargs; got {call_kwargs.get('niche_id')!r}."
    )


def test_create_blueprint_passes_none_when_no_niche_id():
    """Backward compat: an old-shape blueprint without ``niche_id``
    still works — passes ``None`` so the backend's admin-mode
    fallback fires (no silent breakage of unmigrated callers)."""
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.create_blueprint(
        {
            "candidate_id": "cand-2",
            "story_id": "story-1",
            "hook": "test hook",
            "validation_status": {},
            # Note: no niche_id
        }
    )

    proxy.create.assert_called_once()
    call_kwargs = proxy.create.call_args.kwargs
    assert call_kwargs.get("niche_id") is None


def test_retry_path_preserves_niche_id_after_unknown_column_error():
    """The retry-without-unknown-columns path strips optional fields
    but MUST keep the niche_id kwarg. Otherwise: legacy-schema rows
    lose tenant binding silently — the worst-of-both-worlds case
    (looks healthy, RLS quietly bypassed)."""
    proxy = MagicMock()

    # First call: raise UNKNOWN_FIELD_NAME-shaped exception
    # Second call (retry): succeed
    proxy.create.side_effect = [
        Exception("UNKNOWN_FIELD_NAME 'template_id'"),
        {"id": "retry-blueprint-id"},
    ]
    store = _build_store(lambda table: proxy)

    store.create_blueprint(
        {
            "candidate_id": "cand-3",
            "story_id": "story-1",
            "niche_id": "sports",
            "hook": "test hook",
            "template_id": "tmpl-1",
            "validation_status": {},
        }
    )

    # Both calls must have niche_id=sports
    assert proxy.create.call_count == 2
    for call in proxy.create.call_args_list:
        assert call.kwargs.get("niche_id") == "sports", (
            f"Call to create() missing niche_id='sports'; got {call.kwargs!r}. "
            f"Both primary AND retry paths must preserve tenant binding."
        )


def test_sharepoint_backend_accepts_niche_id_kwarg_for_parity():
    """BlueprintStore is backend-agnostic — it must work over both
    PostgresBackend (which uses niche_id for SR-C) AND SharePointBackend
    (which has no SR-C concept). PR #526 ships the kwarg on
    SharePointBackend.create/batch_create as accept-and-ignore so
    BlueprintStore can pass niche_id= unconditionally without
    TypeError on the SharePoint path.

    Catches: a refactor that drops the kwarg from SharePointBackend
    silently breaks every BlueprintStore caller on SharePoint."""
    import inspect

    from genlab_core.storage.sharepoint import SharePointBackend

    create_sig = inspect.signature(SharePointBackend.create)
    assert "niche_id" in create_sig.parameters, (
        "SharePointBackend.create() must accept niche_id kwarg for "
        "API parity with PostgresBackend (see PR #526)."
    )

    batch_create_sig = inspect.signature(SharePointBackend.batch_create)
    assert "niche_id" in batch_create_sig.parameters, (
        "SharePointBackend.batch_create() must accept niche_id kwarg "
        "for parity (BlueprintStore batch migration is the follow-up)."
    )


def test_retry_path_strips_unknown_columns_but_keeps_niche_id_kwarg():
    """Sanity: the retry's stripped fields don't accidentally include
    niche_id (which is a tenant marker, not a 'new optional column')."""
    proxy = MagicMock()
    proxy.create.side_effect = [
        Exception("columnNotFound 'template_name'"),
        {"id": "retry-blueprint-id"},
    ]
    store = _build_store(lambda table: proxy)

    store.create_blueprint(
        {
            "candidate_id": "cand-4",
            "story_id": "story-1",
            "niche_id": "anime",
            "hook": "test hook",
            "template_name": "my_template",
            "validation_status": {},
        }
    )

    assert proxy.create.call_count == 2
    # The retry payload must STILL have niche_id in the fields dict
    # (the existing line 121-122 puts it there) — and the kwarg.
    retry_call = proxy.create.call_args_list[1]
    retry_fields = retry_call.args[1]  # (table, fields, ...)
    assert retry_fields.get("niche_id") == "anime", (
        "Retry must preserve niche_id field in the record body too."
    )
    assert retry_call.kwargs.get("niche_id") == "anime"
