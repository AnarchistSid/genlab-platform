"""PR #528 (2026-06-24): StoryStore.create_story + batch_create_stories
pass ``niche_id`` to the backend wrapper (SR-C migration, second store).

Mirrors PR #526 + #527 for the Stories table:

  * Single-row ``create_story`` forwards ``niche_id`` via the
    ``_sp_call`` wrapper to backend.create().
  * Bulk ``batch_create_stories`` injects ``niche_id`` into each
    record + dispatches on unique-niche set (1 → kwarg, 0 → None
    backward compat, >1 → WARNING + None).

Same regression risks as PR #527's bulk-path tests — if the
injection block disappears, the bulk path silently re-opens the
SR-C hole for stories the same way it did for blueprints
pre-PR-527.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── source pins ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def store_source() -> str:
    import genlab_core.http.story_store as mod

    return Path(mod.__file__).read_text()


def test_create_story_passes_niche_id_via_sp_call(store_source):
    """The single-row create must include ``niche_id=story.get(\"niche_id\")``
    in the ``_sp_call`` invocation. Without this, the kwarg never
    reaches the backend even though the field is in the record body."""
    assert 'niche_id=story.get("niche_id")' in store_source, (
        "create_story must pass niche_id= through _sp_call to the "
        "backend.create call — without this, SR-C SET LOCAL skipped."
    )


def test_batch_injects_niche_id_into_records(store_source):
    """The record-builder loop must populate ``niche_id`` from each
    story dict. Same shape as PR #527's BlueprintStore bulk."""
    assert 'if story.get("niche_id"):' in store_source
    assert 'record["niche_id"] = story["niche_id"]' in store_source


def test_batch_unique_niche_dispatch_logic_present(store_source):
    """All three branches of the dispatch must exist."""
    assert 'niche_ids = {r.get("niche_id") for r in records if r.get("niche_id")}' in store_source
    assert "if len(niche_ids) == 1:" in store_source
    assert "elif len(niche_ids) > 1:" in store_source


def test_batch_heterogeneous_warning_pinned(store_source):
    """WARNING message shape pinned for operator grep + PR anchor."""
    assert "heterogeneous-niche batch" in store_source
    assert "PR #528" in store_source


def test_batch_passes_niche_id_kwarg_to_backend(store_source):
    """Final forward to backend.batch_create."""
    assert "niche_id=batch_niche_id," in store_source


# ── behavioral pins ────────────────────────────────────────────────


def _build_store(backend_callable):
    """Construct a StoryStore with stubbed dependencies."""
    from genlab_core.http.story_store import StoryStore

    return StoryStore(
        sp_call=lambda fn, *args, **kw: fn(*args, **kw),
        backend=backend_callable,
        resolve_source=lambda story: story.get("source", "unknown"),
    )


def _make_proxy_stub():
    proxy = MagicMock()
    proxy.create.return_value = {"id": "story-new-id"}
    proxy.batch_create.return_value = [{"id": "s1"}, {"id": "s2"}]
    return proxy


def test_create_story_forwards_niche_id_to_backend():
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.create_story(
        {
            "story_id": "story-1",
            "title": "test",
            "url": "https://example.com",
            "niche_id": "movies",
        }
    )

    proxy.create.assert_called_once()
    call_kwargs = proxy.create.call_args.kwargs
    assert call_kwargs.get("niche_id") == "movies", (
        f"Expected niche_id='movies' in backend.create kwargs; got {call_kwargs.get('niche_id')!r}."
    )


def test_create_story_without_niche_id_passes_none():
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.create_story(
        {
            "story_id": "story-1",
            "title": "test",
            "url": "https://example.com",
            # No niche_id
        }
    )

    proxy.create.assert_called_once()
    assert proxy.create.call_args.kwargs.get("niche_id") is None


def test_batch_single_niche_passes_kwarg():
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.batch_create_stories(
        [
            {"story_id": "s1", "title": "t1", "url": "u1", "niche_id": "anime"},
            {"story_id": "s2", "title": "t2", "url": "u2", "niche_id": "anime"},
        ]
    )

    proxy.batch_create.assert_called_once()
    assert proxy.batch_create.call_args.kwargs.get("niche_id") == "anime"
    records = proxy.batch_create.call_args.args[1]
    for r in records:
        assert r["niche_id"] == "anime"


def test_batch_no_niche_passes_none_kwarg_and_no_record_field():
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    store.batch_create_stories(
        [
            {"story_id": "s1", "title": "t1", "url": "u1"},
            {"story_id": "s2", "title": "t2", "url": "u2"},
        ]
    )

    proxy.batch_create.assert_called_once()
    assert proxy.batch_create.call_args.kwargs.get("niche_id") is None
    records = proxy.batch_create.call_args.args[1]
    for r in records:
        assert "niche_id" not in r


def test_batch_heterogeneous_warns_and_passes_none(caplog):
    proxy = _make_proxy_stub()
    store = _build_store(lambda table: proxy)

    with caplog.at_level(logging.WARNING):
        store.batch_create_stories(
            [
                {"story_id": "s1", "title": "t1", "url": "u1", "niche_id": "gaming"},
                {"story_id": "s2", "title": "t2", "url": "u2", "niche_id": "sports"},
            ]
        )

    assert proxy.batch_create.call_args.kwargs.get("niche_id") is None
    msgs = [r.message for r in caplog.records if "heterogeneous" in r.message]
    assert len(msgs) == 1
    assert "gaming" in msgs[0] and "sports" in msgs[0]
    # Per-record fields preserved for read-time RLS
    records = proxy.batch_create.call_args.args[1]
    assert records[0]["niche_id"] == "gaming"
    assert records[1]["niche_id"] == "sports"
