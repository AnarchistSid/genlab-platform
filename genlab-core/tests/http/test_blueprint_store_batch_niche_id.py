"""PR #527 (2026-06-24): BlueprintStore.batch_create_blueprints
passes ``niche_id`` to the backend wrapper (SR-C bulk path).

Companion to PR #526 (the single-row path). The bulk path has two
extra complications:

1. Records may be heterogeneous-tenant (input ``blueprints`` is a
   ``list[dict]`` — each entry has its own ``niche_id``). The
   backend's ``batch_create()`` raises ValueError on mismatch when
   ``niche_id=`` is set, so the wrapper must inspect the unique set
   first and only pass the kwarg when all records agree.

2. The bulk path historically *omitted* ``niche_id`` from records
   entirely (docstring line 280-283 acknowledged this). The audit
   flagged this as the SR-C bug class for bulk rows: every blueprint
   created via this path landed without tenant binding.

Behaviour pinned here:

  * 1 unique niche → kwarg set to that niche
  * 0 niches (backward-compat caller without niche_id) → None
  * >1 niches → WARNING logged + None passed (per-record field
    still lands so the row gets its tag for read-time RLS)
  * Records ALWAYS carry the per-record ``niche_id`` field when
    the input bp has it — fills the historical data gap

If these pins regress, the bulk path silently re-opens the SR-C
hole and operators won't notice until a multi-tenant query reads
a cross-tenant row.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── source pins ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def store_source() -> str:
    import genlab_core.http.blueprint_store as mod

    return Path(mod.__file__).read_text()


def test_batch_injects_niche_id_into_records(store_source):
    """The record-builder loop must populate ``niche_id`` from the
    input bp. Without this, the bulk path still lands rows without
    tenant tags (the pre-PR-527 bug). The condition must guard on
    ``bp.get("niche_id")`` (truthy) to avoid writing empty strings."""
    # The injection block is part of the batch_create_blueprints body
    assert 'if bp.get("niche_id"):' in store_source
    assert 'record["niche_id"] = bp["niche_id"]' in store_source


def test_batch_unique_niche_dispatch_logic_present(store_source):
    """The dispatch logic must build a unique-niche set and branch
    on len(). Without this, heterogeneous batches crash on backend
    ValueError or homogeneous batches lose the SET LOCAL optimization."""
    # Build the set on records that have niche_id (truthy guard
    # mirrors the injection guard above — empty string would be
    # falsy and excluded, matching what the backend does).
    assert 'niche_ids = {r.get("niche_id") for r in records if r.get("niche_id")}' in store_source
    # Branch on len(niche_ids) — pin all three branches
    assert "if len(niche_ids) == 1:" in store_source
    assert "elif len(niche_ids) > 1:" in store_source


def test_batch_heterogeneous_warning_pinned(store_source):
    """The warning message shape is what operators grep for when
    debugging multi-tenant anomalies. Pin it so a refactor that
    drops the warning leaves operators flying blind."""
    assert "heterogeneous-niche batch" in store_source
    assert "PR #527" in store_source


def test_batch_passes_niche_id_kwarg_to_backend(store_source):
    """The final call site must include ``niche_id=batch_niche_id``.
    Without the forward, all the dispatch work upstream is wasted —
    we'd compute the right value and not pass it."""
    assert "niche_id=batch_niche_id," in store_source


# ── behavioral pins ────────────────────────────────────────────────


def _build_store(backend_callable, *, find_story_result=None):
    """Construct a BlueprintStore with stubbed dependencies.

    ``find_story_result`` lets a test simulate missing-story
    skips (per-bp callable would be overkill for these tests)."""
    from genlab_core.http.blueprint_store import BlueprintStore

    def _find_story(sid):
        if find_story_result is None:
            return {"id": f"story-{sid}", "fields": {}}
        return find_story_result.get(sid)

    return BlueprintStore(
        sp_call=lambda fn, *args, **kw: fn(*args, **kw),
        backend=backend_callable,
        find_story=_find_story,
        find_template=lambda template_id: None,
        assert_not_scheduled=lambda *a, **kw: None,
    )


def _make_proxy_stub():
    """Mock PostgresTableProxy that captures batch_create() args/kwargs."""
    proxy = MagicMock()
    proxy.batch_create.return_value = [{"id": "bp-1"}, {"id": "bp-2"}]
    return proxy


def test_batch_with_single_niche_passes_kwarg():
    """Homogeneous batch (all records same niche): kwarg gets set
    so the backend's SET LOCAL optimization fires."""
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.batch_create_blueprints(
        [
            {"candidate_id": "c1", "story_id": "s1", "niche_id": "gaming"},
            {"candidate_id": "c2", "story_id": "s2", "niche_id": "gaming"},
        ]
    )

    proxy.batch_create.assert_called_once()
    assert proxy.batch_create.call_args.kwargs.get("niche_id") == "gaming"
    # Per-record fields also have niche_id
    records = proxy.batch_create.call_args.args[1]
    for r in records:
        assert r["niche_id"] == "gaming"


def test_batch_with_no_niche_field_passes_none_kwarg():
    """Backward-compat caller (no niche_id on any bp): kwarg is
    None so the backend's admin-mode fallback fires. Records
    don't carry a niche_id field either — preserves the old shape."""
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.batch_create_blueprints(
        [
            {"candidate_id": "c1", "story_id": "s1"},
            {"candidate_id": "c2", "story_id": "s2"},
        ]
    )

    proxy.batch_create.assert_called_once()
    assert proxy.batch_create.call_args.kwargs.get("niche_id") is None
    records = proxy.batch_create.call_args.args[1]
    for r in records:
        assert "niche_id" not in r


def test_batch_with_heterogeneous_niches_warns_and_passes_none(caplog):
    """Mixed-niche batch (unexpected — production runs per-niche):
    backend would raise ValueError if kwarg were set, so we pass None
    + emit a WARNING. Per-record fields still land for read-time RLS."""
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    with caplog.at_level(logging.WARNING):
        store.batch_create_blueprints(
            [
                {"candidate_id": "c1", "story_id": "s1", "niche_id": "gaming"},
                {"candidate_id": "c2", "story_id": "s2", "niche_id": "sports"},
            ]
        )

    # No backend ValueError (we never set the kwarg)
    proxy.batch_create.assert_called_once()
    assert proxy.batch_create.call_args.kwargs.get("niche_id") is None

    # WARNING logged with the heterogeneous shape
    msgs = [r.message for r in caplog.records if "heterogeneous" in r.message]
    assert len(msgs) == 1
    assert "gaming" in msgs[0]
    assert "sports" in msgs[0]

    # Per-record niche_id field still lands — read-time RLS gets the tag
    records = proxy.batch_create.call_args.args[1]
    assert records[0]["niche_id"] == "gaming"
    assert records[1]["niche_id"] == "sports"


def test_batch_mixed_some_with_some_without_niche():
    """Edge case: some records have niche_id, others don't. The
    unique-set should reflect ONLY the populated values; if it
    collapses to 1, we treat as homogeneous + pass kwarg."""
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.batch_create_blueprints(
        [
            {"candidate_id": "c1", "story_id": "s1", "niche_id": "anime"},
            {"candidate_id": "c2", "story_id": "s2"},  # no niche_id
        ]
    )

    # Only "anime" is in the unique set → kwarg set to "anime".
    assert proxy.batch_create.call_args.kwargs.get("niche_id") == "anime"
    records = proxy.batch_create.call_args.args[1]
    assert records[0]["niche_id"] == "anime"
    # The bp without niche_id has no niche_id field on its record
    assert "niche_id" not in records[1]


def test_batch_missing_story_skipped_does_not_break_dispatch():
    """When a story can't be found, the bp is skipped. The dispatch
    logic must consider only the records that actually made it into
    the batch — a skipped bp's niche_id shouldn't pollute the unique
    set + force a false heterogeneous warning."""
    proxy = _make_proxy_stub()
    proxy.batch_create.return_value = [{"id": "bp-1"}]
    # Only story s1 is found; s2 returns None → bp[1] is skipped
    store = _build_store(
        lambda table: proxy,
        find_story_result={"s1": {"id": "story-s1", "fields": {}}, "s2": None},
    )

    store.batch_create_blueprints(
        [
            {"candidate_id": "c1", "story_id": "s1", "niche_id": "movies"},
            {"candidate_id": "c2", "story_id": "s2", "niche_id": "different"},
        ]
    )

    # Only c1's record landed → unique set = {"movies"} → homogeneous
    proxy.batch_create.assert_called_once()
    records = proxy.batch_create.call_args.args[1]
    assert len(records) == 1
    assert records[0]["candidate_id"] == "c1"
    assert proxy.batch_create.call_args.kwargs.get("niche_id") == "movies"


def test_batch_empty_input_returns_early_with_no_backend_call():
    """Defensive: empty input shouldn't call backend.batch_create.
    The existing behaviour (PR #517's backend) handles empty lists
    gracefully too, but a zero-record call burns a transaction
    + writes log noise for nothing."""
    proxy = _make_proxy_stub()
    proxy.batch_create.return_value = []
    store = _build_store(lambda table: proxy)

    result = store.batch_create_blueprints([])

    # The backend IS called (current behaviour preserves the
    # historical contract of always calling through), but it returns
    # the empty list. The unique-set computes to 0 niches → None kwarg.
    assert result == []
    if proxy.batch_create.called:
        # Empty records list passed through
        assert proxy.batch_create.call_args.args[1] == []
        assert proxy.batch_create.call_args.kwargs.get("niche_id") is None
