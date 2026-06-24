"""PR #533 (2026-06-24): BlueprintStore.update_blueprint_status passes
``niche_id`` to backend.update (SR-A migration, 1st store).

Companion to PR #526's create-side migration. PR #532 shipped the
backend + wrapper plumbing for SR-A on get/update/delete. This PR
is the first store-level caller to actually USE the kwarg —
completing the chain end-to-end for the most-traffic update path
(operator review actions hit this every approve/reject/revise click).

``update_blueprint_status`` already took ``niche_id`` as a kwarg
for the find step. The migration is a pure forwarding hop: pass the
same kwarg through to backend.update too.

Without this PR, the find correctly scopes to the tenant but the
update runs in admin mode — a caller with a blueprint id could
mutate rows across tenants by passing the wrong niche_id (the find
would fail, but a separately-obtained id bypasses the find entirely).

If these pins regress, the SR-A migration silently re-opens for the
highest-traffic update surface in the codebase.
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


def test_update_blueprint_status_passes_niche_id(store_source):
    """The update call must pass niche_id=niche_id to backend.update.
    Without this, the find runs scoped but the UPDATE runs admin-mode."""
    assert "niche_id=niche_id," in store_source
    # And the PR anchor for archaeology
    assert "PR #533" in store_source


def test_update_call_uses_typecast_and_niche_id_together(store_source):
    """Both kwargs must be on the same call — a refactor that splits
    them would silently drop one of them. Pin the joint shape."""
    # Find the update block — must contain BOTH typecast=True AND
    # niche_id=niche_id (the order isn't critical but both must exist).
    update_idx = store_source.find('self._backend("Blueprints").update(')
    assert update_idx != -1
    # Look at the next ~500 chars to capture the full call
    block = store_source[update_idx : update_idx + 500]
    assert "typecast=True" in block
    assert "niche_id=niche_id" in block


# ── behavioral pins ────────────────────────────────────────────────


def _build_store(backend_callable, *, found_blueprint=None):
    """Construct a BlueprintStore with stubbed dependencies."""
    from genlab_core.http.blueprint_store import BlueprintStore

    return BlueprintStore(
        sp_call=lambda fn, *args, **kw: fn(*args, **kw),
        backend=backend_callable,
        find_story=lambda story_id: {"id": "story-1", "fields": {}},
        find_template=lambda template_id: None,
        assert_not_scheduled=lambda *a, **kw: None,
    )


def _make_proxy_stub(found=None):
    proxy = MagicMock()
    proxy.find.return_value = [found] if found else []
    return proxy


def test_update_status_forwards_niche_id_to_backend_update():
    """When a caller passes niche_id, both the find AND the update
    must receive it. The fix's whole point is the update-side; the
    find already worked pre-#533."""
    proxy = _make_proxy_stub(found={"id": "bp-1", "fields": {"status": "VISUAL_READY"}})
    store = _build_store(lambda table: proxy)

    store.update_blueprint_status(
        candidate_id="cand-1",
        status="PUBLISHED",
        niche_id="gaming",
    )

    # The find call (existing behaviour) — niche_id passed through
    proxy.find.assert_called_once()
    assert proxy.find.call_args.kwargs.get("niche_id") == "gaming"

    # The update call (PR #533's new behaviour) — niche_id ALSO passed
    proxy.update.assert_called_once()
    assert proxy.update.call_args.kwargs.get("niche_id") == "gaming", (
        f"backend.update did not receive niche_id=gaming; got "
        f"{proxy.update.call_args.kwargs!r}. SR-A migration broken."
    )


def test_update_status_without_niche_id_passes_none():
    """Backward compat: legacy callers that don't pass niche_id
    still work — backend admin-mode fallback fires."""
    proxy = _make_proxy_stub(found={"id": "bp-1", "fields": {}})
    store = _build_store(lambda table: proxy)

    store.update_blueprint_status(
        candidate_id="cand-1",
        status="ARCHIVED",
    )

    # niche_id passed as None to both find AND update
    assert proxy.find.call_args.kwargs.get("niche_id") is None
    assert proxy.update.call_args.kwargs.get("niche_id") is None


def test_update_status_preserves_typecast_kwarg():
    """typecast=True must survive the migration — without it,
    enum-typed status columns would silently coerce wrong."""
    proxy = _make_proxy_stub(found={"id": "bp-1", "fields": {}})
    store = _build_store(lambda table: proxy)

    store.update_blueprint_status(
        candidate_id="cand-1",
        status="PUBLISHED",
        niche_id="movies",
    )

    assert proxy.update.call_args.kwargs.get("typecast") is True


def test_update_status_passes_extra_kwargs_through_to_update():
    """update_blueprint_status accepts **kwargs that get merged into
    the update fields. The niche_id forward must NOT accidentally
    consume those extras."""
    proxy = _make_proxy_stub(found={"id": "bp-1", "fields": {}})
    store = _build_store(lambda table: proxy)

    store.update_blueprint_status(
        candidate_id="cand-1",
        status="PUBLISHED",
        niche_id="anime",
        scheduled_for="2026-07-01T06:30:00Z",
        action_taken="approved",
    )

    # Fields dict (positional[2]) must contain status + the **kwargs
    fields = proxy.update.call_args.args[2]
    assert fields["status"] == "PUBLISHED"
    assert fields["scheduled_for"] == "2026-07-01T06:30:00Z"
    assert fields["action_taken"] == "approved"
    # AND niche_id is on the kwarg, not in fields
    assert "niche_id" not in fields
    assert proxy.update.call_args.kwargs.get("niche_id") == "anime"


def test_update_status_raises_when_blueprint_missing():
    """Pre-existing behaviour: if find returns nothing, raise
    ValueError. The migration must NOT accidentally swallow this."""
    proxy = _make_proxy_stub(found=None)  # find returns []
    store = _build_store(lambda table: proxy)

    with pytest.raises(ValueError, match="Blueprint cand-missing not found"):
        store.update_blueprint_status(
            candidate_id="cand-missing",
            status="ARCHIVED",
            niche_id="sports",
        )

    # No backend.update call — find returned empty, raised, no mutation
    proxy.update.assert_not_called()
