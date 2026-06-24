"""PR #534 (2026-06-24): SR-A update migration for the remaining 4
stores — Story / Source / ABTest / Asset.

Companion to PR #533 (BlueprintStore.update_blueprint_status). Same
shape, applied 4 times: pass ``niche_id=`` to backend.update so the
SET LOCAL app.niche_id step fires and RLS USING scopes correctly.

Two new-kwarg sites in this PR:
  * SourceStore.update_source_fetch_status — new ``niche_id=`` kwarg
  * ABTestStore.update_ab_test — new ``niche_id=`` kwarg

Two existing-kwarg sites (just forward the existing arg):
  * StoryStore.update_story_status — already had niche_id for find
  * AssetStore.update_asset_status — already had niche_id for find

All four sites land in this single PR because each is a 1-line
forwarding hop following the exact template from PR #533. Combined
test file keeps the SR-A migration history co-located for future
operators auditing tenant-binding correctness.

If these pins regress, the SR-A migration silently re-opens for
non-blueprint mutations — most callers don't trigger these paths
often, so a regression could land without operator visibility for
weeks.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

# ── source pins ─────────────────────────────────────────────────────


def _source(module_name: str) -> str:
    import importlib

    mod = importlib.import_module(module_name)
    return Path(mod.__file__).read_text()


def test_story_store_update_passes_niche_id():
    """StoryStore.update_story_status forwards niche_id to backend.update."""
    src = _source("genlab_core.http.story_store")
    # The update block must include niche_id=niche_id alongside the
    # existing fields dict + table + record id positional args.
    assert "niche_id=niche_id," in src
    assert "PR #534" in src


def test_source_store_update_passes_niche_id():
    """SourceStore.update_source_fetch_status forwards niche_id to
    BOTH the find AND the update (the find now needs it too — new
    kwarg on the public method)."""
    src = _source("genlab_core.http.source_store")
    # Method signature pin — new kwarg
    assert "def update_source_fetch_status(" in src
    assert "niche_id: str | None = None," in src
    # And the niche_id reaches both find + update
    assert "PR #534" in src


def test_ab_test_store_update_passes_niche_id():
    """ABTestStore.update_ab_test takes a new niche_id kwarg + forwards."""
    src = _source("genlab_core.http.ab_test_store")
    assert "def update_ab_test(" in src
    assert "niche_id: str | None = None," in src
    assert "PR #534" in src


def test_asset_store_update_passes_niche_id():
    """AssetStore.update_asset_status forwards the existing niche_id."""
    src = _source("genlab_core.http.asset_store")
    # The update block (after the find) must include niche_id=niche_id
    assert "niche_id=niche_id," in src
    assert "PR #534" in src


# ── behavioral pins ────────────────────────────────────────────────


def _make_proxy_with_found(found_record):
    """Mock proxy where find returns one record (the update path's
    happy case)."""
    proxy = MagicMock()
    proxy.find.return_value = [found_record] if found_record else []
    return proxy


def test_story_update_status_forwards_niche_id_behaviorally():
    """End-to-end: niche_id reaches backend.update on StoryStore."""
    from genlab_core.http.story_store import StoryStore

    proxy = _make_proxy_with_found({"id": "story-rec-1"})
    store = StoryStore(
        sp_call=lambda fn, *args, **kw: fn(*args, **kw),
        backend=lambda table: proxy,
        resolve_source=lambda story: "rss",
    )

    store.update_story_status("sid_1", "VALIDATED", niche_id="movies")

    proxy.find.assert_called_once()
    assert proxy.find.call_args.kwargs.get("niche_id") == "movies"
    proxy.update.assert_called_once()
    assert proxy.update.call_args.kwargs.get("niche_id") == "movies"


def test_source_update_fetch_status_forwards_niche_id_behaviorally():
    """End-to-end: SourceStore's new niche_id kwarg reaches both
    find and update."""
    from genlab_core.http.source_store import SourceStore

    proxy = _make_proxy_with_found({"id": "src-rec-1"})
    store = SourceStore(backend=lambda table: proxy)

    store.update_source_fetch_status("src-1", "ok", niche_id="sports")

    proxy.find.assert_called_once()
    assert proxy.find.call_args.kwargs.get("niche_id") == "sports"
    proxy.update.assert_called_once()
    assert proxy.update.call_args.kwargs.get("niche_id") == "sports"


def test_source_update_fetch_status_backward_compat_no_niche_id():
    """Legacy callers (no niche_id) still work — kwarg defaults to None
    on both find and update."""
    from genlab_core.http.source_store import SourceStore

    proxy = _make_proxy_with_found({"id": "src-rec-1"})
    store = SourceStore(backend=lambda table: proxy)

    store.update_source_fetch_status("src-1", "ok")  # no niche_id

    assert proxy.find.call_args.kwargs.get("niche_id") is None
    assert proxy.update.call_args.kwargs.get("niche_id") is None


def test_ab_test_update_forwards_niche_id_behaviorally():
    from genlab_core.http.ab_test_store import ABTestStore

    proxy = MagicMock()
    proxy.find.return_value = [{"id": "ab-rec-1"}]
    store = ABTestStore(ab_tests=MagicMock(), backend=lambda table: proxy)

    store.update_ab_test("test-1", {"status": "complete"}, niche_id="gaming")

    proxy.find.assert_called_once()
    assert proxy.find.call_args.kwargs.get("niche_id") == "gaming"
    proxy.update.assert_called_once()
    assert proxy.update.call_args.kwargs.get("niche_id") == "gaming"


def test_ab_test_update_backward_compat_no_niche_id():
    """Existing callers that don't pass niche_id still work
    (admin-mode fallback)."""
    from genlab_core.http.ab_test_store import ABTestStore

    proxy = MagicMock()
    proxy.find.return_value = [{"id": "ab-rec-1"}]
    store = ABTestStore(ab_tests=MagicMock(), backend=lambda table: proxy)

    store.update_ab_test("test-1", {"status": "complete"})

    assert proxy.find.call_args.kwargs.get("niche_id") is None
    assert proxy.update.call_args.kwargs.get("niche_id") is None


def test_asset_update_forwards_niche_id_behaviorally():
    from genlab_core.http.asset_store import AssetStore

    proxy = _make_proxy_with_found({"id": "asset-rec-1"})
    store = AssetStore(
        backend=lambda table: proxy,
        find_story=lambda sid, **kw: None,
        find_blueprint=lambda bid, **kw: None,
        normalize_asset_source_type=lambda t: t,
    )

    store.update_asset_status("asset-1", "READY", niche_id="anime")

    proxy.find.assert_called_once()
    assert proxy.find.call_args.kwargs.get("niche_id") == "anime"
    proxy.update.assert_called_once()
    assert proxy.update.call_args.kwargs.get("niche_id") == "anime"


def test_ab_test_unconfigured_short_circuits_no_niche_id_attempt():
    """ABTestStore's truthy-proxy gate must still short-circuit
    BEFORE the new niche_id kwarg matters. Otherwise an unconfigured
    AB_Tests table would attempt a find that fails with 'no such
    table' instead of the friendly silent return."""
    from genlab_core.http.ab_test_store import ABTestStore

    proxy = MagicMock()
    store = ABTestStore(ab_tests=None, backend=lambda table: proxy)  # not configured

    store.update_ab_test("test-1", {"status": "complete"}, niche_id="movies")

    # Find/update never called — short-circuited at the proxy gate
    proxy.find.assert_not_called()
    proxy.update.assert_not_called()
