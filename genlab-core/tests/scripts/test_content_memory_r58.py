"""R-58: ``scripts/content_memory.py`` dedup must not scan the entire
never-purged history per check.

The audit found ``check_duplicate``/``find_similar`` iterating
``_load_posts()`` with no limit and re-tokenizing both sides of every
pairwise call. On the 4GB Hetzner box with the "DO NOT PURGE"
Content_Memory rule, that grows O(N) RAM+CPU forever.

After R-58 the dedup path uses:

  * A recency window (``DEFAULT_DEDUP_WINDOW_DAYS=90``) — relevance
    horizon for "near-duplicate?" is naturally bounded.
  * Pre-computed token sets per post (no re-tokenization per check).
  * Exact-hash short-circuit (identical text → similarity 1.0
    without any Jaccard computation).
  * Hard cap (``_MAX_DEDUP_POSTS=5000``) so a pathological window
    setting can't reintroduce the unbounded scan.

These tests pin each behaviour independently.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import content_memory as cm  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_module_cache():
    """Each test gets a clean module-level cache — tests must not
    depend on the cache state another test left behind."""
    cm._invalidate_cache()
    yield
    cm._invalidate_cache()


def _post(text: str, days_ago: int = 0, **extra) -> dict:
    """Build a post fixture with a posted_at offset from now."""
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    return {
        "content_hash": cm._content_hash(text),
        "platform": "instagram",
        "text": text,
        "engagement": 0,
        "likes": 0,
        "comments": 0,
        "posted_at": ts,
        "media_type": "reel",
        "ingested_at": ts,
        "niche_id": "gaming",
        "is_viral": False,
        "viral_multiplier": 0,
        **extra,
    }


def _install_posts(posts: list[dict]) -> None:
    """Stub the loader paths so ``_load_posts`` returns ``posts``
    (enriched with tokens) without hitting SharePoint or disk."""
    # ``_load_posts`` calls ``_sp_load_all`` first; make that return [].
    # Then it falls through to ``_load_local``, which we patch directly.
    cm._sp_load_all = lambda: []
    cm._load_local = lambda: list(posts)  # copy — _enrich mutates in place


# ── Pre-tokenization ──────────────────────────────────────────────────


def test_r58_load_posts_pre_tokenizes_each_post() -> None:
    """``_load_posts`` must stamp ``_token_set`` on every post at load
    time so per-check work doesn't re-tokenize the same strings."""
    _install_posts(
        [
            _post("Breaking news about the new game release today"),
            _post("Trending clip from yesterday's match"),
        ]
    )
    posts = cm._load_posts(force=True)
    assert all("_token_set" in p for p in posts)
    assert all(isinstance(p["_token_set"], set) for p in posts)
    # Spot-check: stopwords are stripped, length-2 words are out.
    assert "news" in posts[0]["_token_set"]
    assert "the" not in posts[0]["_token_set"]


def test_r58_check_duplicate_uses_pretokenized_set_not_retokenize() -> None:
    """The hot path inside ``check_duplicate`` must consume the cached
    ``_token_set``, not re-tokenize each post on every pairwise call."""
    _install_posts(
        [
            _post("Breaking news about the new game release"),
            _post("Different content entirely"),
        ]
    )

    real_tokenize = cm._tokenize
    call_count = {"n": 0}

    def counting_tokenize(text):
        call_count["n"] += 1
        return real_tokenize(text)

    with patch.object(cm, "_tokenize", side_effect=counting_tokenize):
        cm.check_duplicate("Breaking news about a different topic")

    # Allowed tokenize calls: enrichment (2 posts) + query (1) + hash (0)
    # = 3 total. Without the optimization we'd see >= 5 (enrichment +
    # query + 2 per pairwise re-tokenize).
    assert call_count["n"] <= 3, (
        f"R-58 regression: ``check_duplicate`` is still re-tokenizing per "
        f"pairwise call. Got {call_count['n']} tokenize() invocations "
        f"for 2 posts + 1 query (expected <= 3)."
    )


# ── Recency window ────────────────────────────────────────────────────


def test_r58_check_duplicate_skips_posts_outside_recency_window() -> None:
    """Posts older than ``window_days`` must be skipped by the scan."""
    _install_posts(
        [
            _post("Trending today match win"),  # in-window
            _post("Trending today match win", days_ago=400),  # OUT-of-window
        ]
    )
    matches = cm.check_duplicate("Trending today match win", threshold=0.4)
    # Recent post is a duplicate, ancient post is filtered out → exactly 1 match.
    assert len(matches) == 1
    # And it's the recent one.
    posted_at = matches[0]["posted_at"]
    cutoff = (datetime.now(UTC) - timedelta(days=cm.DEFAULT_DEDUP_WINDOW_DAYS)).isoformat()
    assert posted_at >= cutoff


def test_r58_window_days_zero_disables_filter() -> None:
    """``window_days=0`` opts back into the legacy scan-everything
    behaviour for one-off audits."""
    _install_posts(
        [
            _post("Match win highlight", days_ago=400),
            _post("Match win highlight", days_ago=800),
        ]
    )
    matches = cm.check_duplicate("Match win highlight", threshold=0.4, window_days=0)
    # No filter → both ancient posts match.
    assert len(matches) == 2


def test_r58_check_duplicate_skips_malformed_timestamp() -> None:
    """A historical record with an empty/malformed ``posted_at`` must
    be treated as out-of-window — defends against one corrupt row
    silently re-introducing the unbounded scan."""
    _install_posts(
        [
            _post("Recent valid post"),
            {**_post("Corrupt-timestamp record"), "posted_at": "", "ingested_at": ""},
        ]
    )
    matches = cm.check_duplicate("Recent valid post", threshold=0.4)
    assert len(matches) == 1
    assert "Recent" in matches[0]["text"]


# ── Hash short-circuit ────────────────────────────────────────────────


def test_r58_check_duplicate_exact_hash_returns_similarity_1() -> None:
    """A byte-identical re-post must hit the exact-hash short-circuit
    and return similarity 1.0 — no Jaccard computation needed."""
    target = "Trending game release today"
    _install_posts([_post(target)])
    matches = cm.check_duplicate(target, threshold=0.4)
    assert len(matches) == 1
    assert matches[0]["similarity"] == 1.0


# ── Hard cap ──────────────────────────────────────────────────────────


def test_r58_check_duplicate_respects_hard_cap() -> None:
    """Even with ``window_days=0`` (no recency filter), the scan stops
    after ``_MAX_DEDUP_POSTS`` posts."""
    # Build cap + 50 in-window posts; force the cap to a small number
    # so the test is fast.
    with patch.object(cm, "_MAX_DEDUP_POSTS", 10):
        posts = [_post(f"unique-content-{i}") for i in range(50)]
        # Same query token to make every post a potential match.
        for p in posts:
            p["text"] = "shared content marker phrase " + p["text"]
        _install_posts(posts)
        matches = cm.check_duplicate("shared content marker phrase", threshold=0.05, window_days=0)
    # Cap is 10 → at most 10 matches.
    assert len(matches) <= 10
