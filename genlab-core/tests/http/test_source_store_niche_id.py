"""PR #531 (2026-06-24): SourceStore.create_source passes
``niche_id`` to the backend wrapper (SR-C migration, 5th + FINAL store).

Completes the SR-C store-level migration arc:

  * PR #517 — backend.create + batch_create accept niche_id kwarg
  * PR #525 — wrapper layer forwards niche_id to backend
  * PR #526 — BlueprintStore.create_blueprint (1st store)
  * PR #527 — BlueprintStore.batch_create_blueprints (bulk)
  * PR #528 — StoryStore.create_story + batch_create_stories
  * PR #529 — TemplateStore.create_template
  * PR #530 — ABTestStore.create_ab_test
  * **PR #531 — SourceStore.create_source (this)**

The sources table on prod has a ``niche_id`` column (verified
2026-06-24 schema probe — 219 rows, tenant-scoped). Some sources are
operator-owned per-niche (e.g. a CriticalRush YouTube channel watched
only by the gaming pipeline). Pre-PR rows landed without tenant
binding.

After this PR every BacklogClient store-level create path passes
``niche_id`` end-to-end. The SR-C bug class is closed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── source pins ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def store_source() -> str:
    import genlab_core.http.source_store as mod

    return Path(mod.__file__).read_text()


def test_source_create_injects_niche_id_field(store_source):
    """The record must carry ``niche_id`` when source dict has one."""
    assert 'if source.get("niche_id"):' in store_source
    assert 'fields["niche_id"] = source["niche_id"]' in store_source


def test_source_create_passes_niche_id_kwarg(store_source):
    """Kwarg forward to backend.create."""
    assert 'niche_id=source.get("niche_id"),' in store_source


def test_pr_531_anchor_present(store_source):
    """Future-archaeology anchor."""
    assert "PR #531" in store_source


# ── behavioral pins ────────────────────────────────────────────────


def _build_store():
    """Construct a SourceStore with stubbed backend."""
    from genlab_core.http.source_store import SourceStore

    proxy = MagicMock()
    proxy.create.return_value = {"id": "src-new-id"}
    store = SourceStore(backend=lambda table: proxy)
    return store, proxy


def _minimal_source(**overrides):
    base = {
        "source_id": "src_001",
        "domain": "example.com",
        "url": "https://example.com/feed",
        "type": "rss",
    }
    base.update(overrides)
    return base


def test_source_with_niche_id_forwards_to_backend():
    store, proxy = _build_store()

    store.create_source(_minimal_source(niche_id="movies"))

    proxy.create.assert_called_once()
    call_kwargs = proxy.create.call_args.kwargs
    assert call_kwargs.get("niche_id") == "movies"
    # Record body also carries the tag — defense in depth for read-RLS
    fields = proxy.create.call_args.args[1]
    assert fields["niche_id"] == "movies"


def test_source_without_niche_id_passes_none_no_field():
    """Backward compat: legacy callers without niche_id still work.
    Record has no niche_id field — preserves the long-standing
    dict shape."""
    store, proxy = _build_store()

    store.create_source(_minimal_source())

    proxy.create.assert_called_once()
    assert proxy.create.call_args.kwargs.get("niche_id") is None
    fields = proxy.create.call_args.args[1]
    assert "niche_id" not in fields


def test_source_create_preserves_all_promoted_fields():
    """The migration must NOT accidentally drop any of the existing
    field-builder lines. Pin the canonical full shape."""
    store, proxy = _build_store()
    store.create_source(
        _minimal_source(
            name="My Feed",
            priority=2.5,
            enabled=False,
            authority_score=0.9,
            niche_id="ai_creators",
        )
    )
    fields = proxy.create.call_args.args[1]
    assert fields["source_id"] == "src_001"
    assert fields["domain"] == "example.com"
    assert fields["name"] == "My Feed"
    assert fields["url"] == "https://example.com/feed"
    assert fields["type"] == "rss"
    assert fields["priority"] == 2.5
    assert fields["enabled"] is False
    assert fields["authority_score"] == 0.9
    assert fields["niche_id"] == "ai_creators"
