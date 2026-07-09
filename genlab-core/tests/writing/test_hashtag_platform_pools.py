"""Contract pins for platform_hashtag_pools.yaml + resolver.

Task #627 (2026-07-09, roadmap E4) — per-platform × per-niche
hashtag pools. Before this PR the generator drew from the same
2-tag ``_NICHE_BASE`` regardless of platform.

## Guarantees pinned here

* Config file exists + parses cleanly at import time.
* Resolver prefers per-platform × per-niche entry, then per-niche
  fallback, then generic ``["#Content"]``.
* Existing behaviour preserved when config is absent — the pre-#627
  ``_NICHE_BASE`` fallback still fires (fail-open).
* End-to-end pin: ``generate_hashtags(platform="tiktok")`` returns
  tiktok-flavored base tags, not IG ones.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from genlab_core.writing.hashtag_generator import (
    _NICHE_BASE,
    _load_platform_pools,
    _resolve_niche_base,
    generate_hashtags,
)

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "platform_hashtag_pools.yaml"


@pytest.fixture(autouse=True)
def _clear_pool_cache():
    """Ensure the LRU cache is empty at the start of each test.

    Tests that monkeypatch the pool need a clean cache to observe
    their patch. Tests that expect the real config need a clean
    cache too so a prior test's patch doesn't leak in.
    """
    _load_platform_pools.cache_clear()
    yield
    _load_platform_pools.cache_clear()


# ── config file sanity ───────────────────────────────────────────


def test_config_file_exists():
    """The pool config must ship in-tree — this file is loaded by
    hashtag_generator at each generation call. Missing = silent
    fall-through to legacy behaviour, which defeats E4's purpose."""
    assert _CONFIG_PATH.exists(), (
        f"Missing {_CONFIG_PATH} — task #627 relies on this file "
        "being in-tree so hashtag_generator._load_platform_pools "
        "can find it."
    )


def test_config_file_parses_and_covers_5_platforms():
    """5 platforms × 5 niches = 25 cells. If a cell drops the
    resolver falls through to ``_NICHE_BASE`` — safe, but silently
    reverts to legacy behaviour for that (platform, niche). This
    test ensures we notice a drop."""
    with _CONFIG_PATH.open() as f:
        data = yaml.safe_load(f)
    platforms = data.get("platforms") or {}
    expected_platforms = {"instagram", "tiktok", "twitter", "facebook", "threads"}
    assert expected_platforms.issubset(platforms.keys()), (
        f"Config missing platforms: {expected_platforms - set(platforms.keys())}"
    )
    expected_niches = {"gaming", "sports", "movies", "anime", "ai_creators"}
    for plat in expected_platforms:
        cells = platforms[plat]
        assert expected_niches.issubset(cells.keys()), (
            f"Platform {plat!r} missing niches: {expected_niches - set(cells.keys())}"
        )


# ── resolver semantics ──────────────────────────────────────────


def test_resolver_prefers_per_platform_pool():
    """When the config supplies a (platform, niche) cell, the
    resolver returns THAT list, not the legacy per-niche fallback."""
    ig_tags = _resolve_niche_base("gaming", "instagram")
    tt_tags = _resolve_niche_base("gaming", "tiktok")
    # Config makes these different — that's the whole point of E4.
    assert ig_tags != tt_tags, (
        "IG and TikTok pools for gaming must differ — if they're "
        "identical the resolver isn't actually reading the config."
    )
    # And IG's config lookup must not fall back to _NICHE_BASE
    # (the legacy 2-tag list) because the config has 3 tags.
    assert ig_tags != _NICHE_BASE["gaming"]


def test_resolver_falls_back_to_niche_base_when_platform_missing(monkeypatch):
    """Unknown platform → fall through to legacy ``_NICHE_BASE``.

    This is the load-bearing fail-open pin: if the config becomes
    unreachable (deploy timing, missing volume mount, misspelled
    platform arg), the generator MUST NOT crash — it must degrade
    to pre-#627 behavior."""
    monkeypatch.setattr(
        "genlab_core.writing.hashtag_generator._load_platform_pools",
        lambda: {},
    )
    _load_platform_pools.cache_clear()
    tags = _resolve_niche_base("gaming", "instagram")
    assert tags == _NICHE_BASE["gaming"], (
        "When platform pool is empty, must fall back to _NICHE_BASE."
    )


def test_resolver_falls_back_when_niche_unknown_in_platform():
    """Platform known but niche unknown → fall through to legacy."""
    # ``youtube`` intentionally NOT in the pool config (docstring
    # explains why — YT uses metadata tags, not hashtags). This
    # exercises the fail-through path.
    tags = _resolve_niche_base("gaming", "youtube")
    assert tags == _NICHE_BASE["gaming"]


def test_resolver_falls_back_to_content_for_totally_unknown_niche():
    """Bottom fallback — some future call site with an unregistered
    niche id must not crash."""
    tags = _resolve_niche_base("crypto_bros", "instagram")
    assert tags == ["#Content"]


# ── end-to-end wire pin ─────────────────────────────────────────


def test_end_to_end_ig_uses_platform_pool_tags():
    """The whole point of #627: generate_hashtags(platform="tiktok")
    returns TikTok-flavoured base tags (from config), not the legacy
    IG-shaped ones."""
    story = {"title": "some gaming clip", "summary": "", "hook": ""}
    ig = generate_hashtags(story, niche_id="gaming", platform="instagram")
    tt = generate_hashtags(story, niche_id="gaming", platform="tiktok")
    # Both should have gaming-relevant tags but not be identical.
    assert ig and tt
    assert ig != tt, (
        "IG and TikTok gaming outputs must differ (they draw from "
        "different platform pools per config). If they're identical "
        "the wire between generate_hashtags and _resolve_niche_base "
        "is broken."
    )


def test_end_to_end_absent_config_preserves_legacy_behavior(monkeypatch):
    """Regression pin: pre-#627 IG output had the legacy 2-tag base.
    When config is absent, generate_hashtags MUST still return the
    same legacy tags — this is the fail-open contract that keeps
    #627 zero-risk to deploy without the config file present."""
    monkeypatch.setattr(
        "genlab_core.writing.hashtag_generator._load_platform_pools",
        lambda: {},
    )
    _load_platform_pools.cache_clear()
    story = {"title": "some gaming clip", "summary": "", "hook": ""}
    tags = generate_hashtags(story, niche_id="gaming", platform="instagram")
    # First 2 tags should be _NICHE_BASE["gaming"] — legacy shape.
    assert tags[:2] == _NICHE_BASE["gaming"]
