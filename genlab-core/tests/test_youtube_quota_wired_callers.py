"""PR #537 (2026-06-24): SR-E wire — trending_video_fetcher and
platforms/youtube pass niche_id to YouTubeQuotaTracker.

Companion to PR #536 (the foundation that added the kwarg). PR #538
(the next step) flips on per-niche budget enforcement; this PR is
the wire so the per-niche counters actually populate before that
enforcement turns on.

If these pins regress, the per-niche counter stays at 0 forever
because no caller ever sends niche_id. PR #538 would then enforce
against empty data — every niche always under its quota → no
behavior change → SR-E remains effectively open.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

# ── source pins ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fetcher_src() -> str:
    import genlab_core.media.trending_video_fetcher as mod

    return Path(mod.__file__).read_text()


@pytest.fixture(scope="module")
def youtube_src() -> str:
    import genlab_core.platforms.youtube as mod

    return Path(mod.__file__).read_text()


def test_fetcher_can_afford_passes_niche_id(fetcher_src):
    """The search-gate must call can_afford with niche_id=niche_id.
    Without this, the per-niche counter never populates from the
    fetch side — PR #538's enforcement has no per-niche data to act on."""
    # The exact call shape — pinned so a refactor that drops the kwarg
    # silently re-opens SR-E.
    assert 'can_afford("search", niche_id=niche_id)' in fetcher_src
    assert "PR #537" in fetcher_src


def test_fetcher_record_passes_niche_id(fetcher_src):
    """The post-search record() must also include niche_id — otherwise
    the gate populates correctly but the actual spend lands global-only."""
    assert 'record("search", niche_id=niche_id)' in fetcher_src


def test_youtube_platform_can_afford_replaces_can_upload(youtube_src):
    """The platform client migrated from can_upload() to
    can_afford("upload", niche_id=...). can_upload() doesn't accept
    niche_id (foundation-PR scope), so migrating is the clean path —
    can_afford is functionally identical today and accepts the kwarg."""
    # The new call shape
    assert 'can_afford("upload", niche_id=self.niche_id)' in youtube_src
    # The old call shape must NOT remain in the upload-gate code path
    # (still exists in tests / other contexts — narrow the search to
    # the upload-gate context).
    upload_gate_start = youtube_src.find("YouTube quota gate blocked upload")
    assert upload_gate_start != -1
    # Look backwards 500 chars to capture the gate's call site
    gate_block = youtube_src[max(0, upload_gate_start - 500) : upload_gate_start + 100]
    assert "can_upload()" not in gate_block, (
        "Upload gate must use can_afford('upload', niche_id=...) — "
        "can_upload() doesn't accept niche_id so per-niche enforcement "
        "would skip the upload path."
    )


def test_youtube_platform_status_passes_niche_id(youtube_src):
    """The error-message status() call should pass niche_id so logs
    include per-tenant detail."""
    assert "status(niche_id=self.niche_id)" in youtube_src


def test_youtube_platform_record_passes_niche_id(youtube_src):
    """The post-upload record() must include niche_id so per-niche
    upload counts populate (not just global)."""
    assert 'record("upload", niche_id=self.niche_id)' in youtube_src


# ── behavioral pins (via stubbed tracker) ──────────────────────────


def test_fetcher_search_gate_routes_niche_id_to_tracker(monkeypatch):
    """End-to-end behavioral: when the fetcher's search gate is invoked,
    the underlying quota tracker's can_afford receives niche_id from
    the caller. Patches _get_persistent_quota to return a recording stub."""
    from unittest.mock import MagicMock

    import genlab_core.media.trending_video_fetcher as mod

    stub = MagicMock()
    stub.can_afford.return_value = False  # block search to exit early
    monkeypatch.setattr(mod, "_get_persistent_quota", lambda: stub)

    # Instantiate a fetcher + invoke the (private) keyword search method
    fetcher = mod.TrendingVideoFetcher(api_key="fake-key")
    from datetime import datetime

    fetcher._search_recent(  # type: ignore[attr-defined]
        query="test",
        niche_id="gaming",
        published_after=datetime.now(UTC),
    )

    # can_afford was called with the niche_id kwarg
    stub.can_afford.assert_called_once_with("search", niche_id="gaming")


def test_fetcher_does_not_pass_niche_id_on_global_increment(fetcher_src):
    """The legacy _increment_quota(100, ...) helper is per-process,
    log-only, and pre-existing — it has NO niche_id concept. The
    migration must touch the persistent tracker calls only; the
    log-only QUOTA_TRACKER dict stays unchanged so the historical
    debug logging shape is preserved."""
    # _increment_quota is a positional-only 2-arg helper — no niche_id
    assert "_increment_quota(100," in fetcher_src
    # And the QUOTA_TRACKER dict shape unchanged (no per-niche key here)
    assert 'QUOTA_TRACKER: dict[str, int] = {"units_used": 0, "rss_fetches": 0}' in fetcher_src
