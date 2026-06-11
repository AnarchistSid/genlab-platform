#!/usr/bin/env python3
"""
Gen Lab — Content Memory Engine
Tracks published content, finds winning patterns, and prevents duplicate posts.
Primary storage: SharePoint (Content_Memory list). Local JSON as write-through cache.

Usage:
    python3 scripts/content_memory.py --ingest          # Ingest recent posts from all platforms
    python3 scripts/content_memory.py --check "topic"   # Check if similar content was posted
    python3 scripts/content_memory.py --winners          # Show top performing content patterns
    python3 scripts/content_memory.py --similar "text"   # Find similar past posts
    python3 scripts/content_memory.py --sync             # Force sync local cache ↔ SharePoint
    python3 scripts/content_memory.py --ingest --niche gaming  # Niche-tagged ingestion
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time as _time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# NOTE: Run via: uv run --package genlab-core python scripts/content_memory.py
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("content_memory")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

MEMORY_DIR = Path.home() / ".genlab" / "content_memory"
POSTS_FILE = MEMORY_DIR / "posts.json"

# SharePoint list config — loaded from lists_config.yaml (single source of truth)
_FALLBACK_CONTENT_MEMORY_LIST_ID = "aabea5e0-6225-4aec-890c-98fe2bb1814e"


def _load_content_memory_list_id() -> str:
    """Read Content_Memory list_id from lists_config.yaml."""
    try:
        import yaml

        config_path = os.environ.get(
            "BACKLOG_CONFIG_PATH",
            str(
                Path(__file__).resolve().parent.parent
                / "genlab-core"
                / "config"
                / "lists_config.yaml"
            ),
        )
        with open(config_path) as f:
            config = yaml.safe_load(f)
        return config["Content_Memory"]["list_id"]
    except Exception:
        logger.debug("Could not load Content_Memory list_id from config — using fallback")
        return _FALLBACK_CONTENT_MEMORY_LIST_ID


# ── SharePoint proxy ────────────────────────────────────────────────

_proxy = None


def _get_proxy():
    """Lazy-init the Content_Memory proxy via BacklogClient."""
    global _proxy
    if _proxy is not None:
        return _proxy
    try:
        from genlab_core.http.backlog_client import BacklogClient

        client = BacklogClient()
        proxy = getattr(client, "content_memory", None)
        if proxy is None:
            logger.warning(
                "BacklogClient has no content_memory proxy — falling back to local-only mode"
            )
            return None
        _proxy = proxy
        return _proxy
    except Exception as e:
        logger.warning("BacklogClient init failed (%s) — falling back to local-only", e)
        return None


# ── Local cache ─────────────────────────────────────────────────────


def _ensure_dirs():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def _load_local() -> list[dict]:
    if POSTS_FILE.exists():
        try:
            return json.loads(POSTS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("posts.json is corrupt; returning empty")
    return []


def _save_local(posts: list[dict]):
    _ensure_dirs()
    tmp = POSTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(posts, indent=2, default=str))
    os.replace(tmp, POSTS_FILE)  # atomic on POSIX


# ── SharePoint CRUD ─────────────────────────────────────────────────


def _sp_create(post: dict) -> str | None:
    """Create a post in SharePoint. Returns record ID or None."""
    proxy = _get_proxy()
    if not proxy:
        return None
    try:
        fields = {
            "content_hash": post["content_hash"],
            "platform": post["platform"],
            "post_text": post.get("text", "")[:5000],
            "engagement": post.get("engagement", 0),
            "likes": post.get("likes", 0),
            "comments_count": post.get("comments", 0),
            "posted_at": post.get("posted_at", ""),
            "media_type": post.get("media_type", ""),
            "ingested_at": post.get("ingested_at", ""),
        }
        if post.get("niche_id"):
            fields["niche_id"] = post["niche_id"]
        if post.get("is_viral"):
            fields["is_viral"] = True
            fields["viral_multiplier"] = post.get("viral_multiplier", 0)
        record = proxy.create(fields)
        return record.get("id")
    except Exception as e:
        logger.warning("SharePoint create failed: %s", e)
        return None


def _sp_load_all() -> list[dict]:
    """Load all posts from SharePoint."""
    proxy = _get_proxy()
    if not proxy:
        return []
    try:
        records = proxy.all()
        posts = []
        for rec in records:
            f = rec.get("fields", rec)
            posted = f.get("posted_at", "")
            ingested = f.get("ingested_at", "")
            # Graph API may return datetime objects — normalize to strings
            if hasattr(posted, "isoformat"):
                posted = posted.isoformat()
            if hasattr(ingested, "isoformat"):
                ingested = ingested.isoformat()
            posts.append(
                {
                    "content_hash": f.get("content_hash", ""),
                    "platform": f.get("platform", ""),
                    "text": f.get("post_text", ""),
                    "engagement": f.get("engagement", 0) or 0,
                    "likes": f.get("likes", 0) or 0,
                    "comments": f.get("comments_count", 0) or 0,
                    "posted_at": posted or "",
                    "media_type": f.get("media_type", ""),
                    "ingested_at": ingested or "",
                    "niche_id": f.get("niche_id", ""),
                    "is_viral": bool(f.get("is_viral")),
                    "viral_multiplier": f.get("viral_multiplier", 0) or 0,
                    "_sp_id": rec.get("id", ""),
                }
            )
        return posts
    except Exception as e:
        logger.warning("SharePoint load failed: %s", e)
        return []


def _sp_find_by_hash(content_hash: str) -> dict | None:
    """Check if a post exists in SharePoint by content_hash."""
    proxy = _get_proxy()
    if not proxy:
        return None
    try:
        records = proxy.all(
            formula=f"{{content_hash}}='{content_hash}'",
            max_records=1,
        )
        return records[0] if records else None
    except Exception:
        return None


# ── Unified load (SharePoint-first, local fallback, TTL cache) ─────

_posts_cache: list[dict] | None = None
_posts_cache_ts: float = 0.0
_POSTS_CACHE_TTL = 60.0  # seconds


# R-58: per-check tunables. ``DEFAULT_DEDUP_WINDOW_DAYS`` caps the
# similarity scan to recent posts (90 days = a quarter; trending
# content older than a quarter is extremely unlikely to be a
# near-duplicate of anything today). The "DO NOT PURGE" rule from
# CLAUDE.md applies to the underlying store, NOT to what we load
# into RAM for a similarity check — those are different concerns.
DEFAULT_DEDUP_WINDOW_DAYS = 90

# Hard cap so a pathological window setting can't reintroduce the
# unbounded scan. Trending-content workloads on the 4GB Hetzner box
# top out far below this in practice (~few thousand records).
_MAX_DEDUP_POSTS = 5000


def _enrich_with_tokens(posts: list[dict]) -> list[dict]:
    """R-58: pre-compute the token set for each post ONCE at load time.

    Before R-58 ``check_duplicate``/``find_similar`` re-tokenized both
    sides of every pairwise call. With N posts and a single check that's
    N redundant tokenizations of the same N strings on every cache hit.
    Pre-computing here amortizes tokenization across the whole TTL window
    (60s by default).

    Stored under ``_token_set`` to avoid clobbering any SharePoint
    field name. Tokenization is identical to ``_tokenize`` so legacy
    callers that re-tokenize get the same set.
    """
    for post in posts:
        # Idempotent: ``_load_posts`` may be re-invoked within the cache
        # TTL; recomputing is wasted work but not wrong.
        if "_token_set" not in post:
            post["_token_set"] = _tokenize(post.get("text", ""))
    return posts


def _load_posts(force: bool = False) -> list[dict]:
    """Load posts: in-process cache → SharePoint → local JSON.

    R-58: each loaded post is enriched with a pre-computed ``_token_set``
    so subsequent ``check_duplicate``/``find_similar`` calls don't re-
    tokenize the same strings on every invocation.
    """
    global _posts_cache, _posts_cache_ts

    if (
        not force
        and _posts_cache is not None
        and (_time.monotonic() - _posts_cache_ts) < _POSTS_CACHE_TTL
    ):
        return _posts_cache

    sp_posts = _sp_load_all()
    if sp_posts:
        _save_local(sp_posts)
        _posts_cache = _enrich_with_tokens(sp_posts)
        _posts_cache_ts = _time.monotonic()
        return _posts_cache

    local = _load_local()
    _posts_cache = _enrich_with_tokens(local)
    _posts_cache_ts = _time.monotonic()
    return _posts_cache


def _invalidate_cache():
    """Clear in-process cache after writes."""
    global _posts_cache, _posts_cache_ts
    _posts_cache = None
    _posts_cache_ts = 0.0


# ── Text processing ─────────────────────────────────────────────────


def _content_hash(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9\s]", "", text.lower().strip())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return hashlib.md5(cleaned.encode()).hexdigest()[:12]


def _tokenize(text: str) -> set[str]:
    text = re.sub(r"[^a-z0-9\s]", "", text.lower())
    words = text.split()
    stops = {
        "the",
        "a",
        "an",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "shall",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "up",
        "about",
        "into",
        "through",
        "and",
        "but",
        "or",
        "nor",
        "not",
        "so",
        "yet",
        "both",
        "either",
        "neither",
        "this",
        "that",
        "these",
        "those",
        "just",
        "its",
        "it",
        "they",
    }
    return {w for w in words if w not in stops and len(w) > 2}


def engagement_score(post: dict) -> float:
    """Canonical engagement formula — used by all intelligence systems."""
    likes = post.get("likes", 0) or post.get("views", 0) or 0
    comments = post.get("comments", 0) or 0
    shares = post.get("shares", 0) or post.get("retweets", 0) or 0
    replies = post.get("replies", 0) or 0
    return likes + (comments * 3) + (shares * 5) + (replies * 2)


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _jaccard_set_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """R-58: same semantics as ``_jaccard_similarity`` but takes
    already-tokenized sets. The hot path through ``check_duplicate``/
    ``find_similar`` uses this against the pre-computed ``_token_set``
    on each post so the same N strings aren't re-tokenized on every
    pairwise call within a TTL window."""
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _within_window(post: dict, cutoff_iso: str | None) -> bool:
    """R-58: include ``post`` in the similarity scan iff its
    ``posted_at`` (ISO 8601 string) is at-or-after ``cutoff_iso``.

    Empty/malformed timestamps are treated as out-of-window so a
    half-corrupted historical record can't grow the scan unboundedly.
    ``cutoff_iso=None`` disables the filter (legacy "scan everything"
    behaviour) — used by analytics paths that genuinely want all
    history (e.g. ``ingest_from_analytics``)."""
    if cutoff_iso is None:
        return True
    ts = post.get("posted_at") or post.get("ingested_at") or ""
    if not ts:
        return False
    # Cheap string-prefix compare works because both are ISO 8601
    # ("2026-06-11T..."). Avoids per-post datetime.fromisoformat() cost
    # which would re-introduce the per-post overhead we just removed.
    return ts >= cutoff_iso


def _recency_cutoff_iso(window_days: int) -> str:
    """ISO 8601 timestamp for ``window_days`` ago — the dedup cutoff."""
    from datetime import timedelta

    return (datetime.now(UTC) - timedelta(days=window_days)).isoformat()


# ── Public API ──────────────────────────────────────────────────────


def flag_viral(content_hash: str, multiplier: float, platform: str = "") -> bool:
    """Mark a post as viral in SharePoint and local cache.

    Called by viral_detector when a post exceeds the viral threshold.
    """
    proxy = _get_proxy()
    if proxy:
        try:
            records = proxy.all(
                formula=f"{{content_hash}}='{content_hash}'",
                max_records=1,
            )
            if records:
                proxy.update(
                    records[0]["id"],
                    {
                        "is_viral": True,
                        "viral_multiplier": round(multiplier, 1),
                    },
                )
                logger.info("Flagged %s as viral (%sx) in SharePoint", content_hash, multiplier)
                return True
        except Exception as e:
            logger.warning("Failed to flag viral in SharePoint: %s", e)

    # Update local cache too
    posts = _load_local()
    for post in posts:
        if post.get("content_hash") == content_hash:
            post["is_viral"] = True
            post["viral_multiplier"] = round(multiplier, 1)
            _save_local(posts)
            _invalidate_cache()
            return True
    return False


def get_memory_stats(niche_id: str | None = None) -> dict[str, Any]:
    """Summary stats for the intelligence hub."""
    posts = _load_posts()
    if niche_id:
        posts = [p for p in posts if p.get("niche_id") == niche_id]
    if not posts:
        return {"total_posts": 0}

    platforms: dict[str, int] = {}
    for p in posts:
        plat = p.get("platform", "unknown")
        platforms[plat] = platforms.get(plat, 0) + 1

    engagements = [p.get("engagement", 0) or 0 for p in posts]
    viral_count = sum(1 for p in posts if p.get("is_viral"))

    return {
        "total_posts": len(posts),
        "by_platform": platforms,
        "viral_posts": viral_count,
        "avg_engagement": round(sum(engagements) / len(engagements), 1) if engagements else 0,
        "top_engagement": max(engagements) if engagements else 0,
    }


def ingest_from_analytics(days: int = 30, niche_id: str | None = None) -> dict[str, int]:
    """Ingest recent posts from all platform analytics into memory."""
    from social_analytics import (
        get_facebook_analytics,
        get_instagram_analytics,
        get_threads_analytics,
        get_x_analytics,
        get_youtube_analytics,
    )

    existing = _load_posts()
    existing_hashes = {p.get("content_hash") for p in existing}
    new_count = 0
    sp_count = 0

    platform_fns = {
        "youtube": get_youtube_analytics,
        "instagram": get_instagram_analytics,
        "facebook": get_facebook_analytics,
        "x_twitter": get_x_analytics,
        "threads": get_threads_analytics,
    }

    for platform, fn in platform_fns.items():
        try:
            data = fn(days)
            if "error" in data:
                logger.warning("Skipping %s: %s", platform, data["error"])
                continue

            posts = (
                data.get("recent_videos")
                or data.get("recent_posts")
                or data.get("recent_tweets")
                or []
            )

            for post in posts:
                text = (
                    post.get("title")
                    or post.get("caption")
                    or post.get("text")
                    or post.get("message")
                    or ""
                )
                if not text:
                    continue

                ch = _content_hash(text)
                if ch in existing_hashes:
                    continue

                engagement = engagement_score(post)

                new_post = {
                    "content_hash": ch,
                    "platform": platform,
                    "text": text[:500],
                    "engagement": engagement,
                    "likes": post.get("likes", 0) or post.get("views", 0) or 0,
                    "comments": post.get("comments", 0) or 0,
                    "posted_at": post.get("published")
                    or post.get("posted")
                    or post.get("created_at"),
                    "media_type": post.get("type"),
                    "ingested_at": datetime.now(UTC).isoformat(),
                    "niche_id": niche_id,
                }

                # Write to SharePoint
                sp_id = _sp_create(new_post)
                if sp_id:
                    new_post["_sp_id"] = sp_id
                    sp_count += 1

                existing.append(new_post)
                existing_hashes.add(ch)
                new_count += 1

        except Exception as e:
            logger.warning("Failed to ingest %s: %s", platform, e)

    _save_local(existing)
    _invalidate_cache()
    return {
        "new_posts_ingested": new_count,
        "synced_to_sharepoint": sp_count,
        "total_posts": len(existing),
    }


def check_duplicate(
    text: str,
    threshold: float = 0.4,
    window_days: int = DEFAULT_DEDUP_WINDOW_DAYS,
) -> list[dict]:
    """Check if similar content has been posted before.

    R-58: bounded scan. The "DO NOT PURGE" rule on Content_Memory
    governs the underlying store, not what we hold in RAM for a
    similarity check. We use:

      * A recency window (``window_days`` — default 90; relevance
        horizon for "is this near-duplicate" is naturally bounded).
      * Pre-computed token sets per post (eliminates per-check
        re-tokenization of the same N strings).
      * Exact-hash short-circuit (identical text → similarity 1.0
        without any Jaccard computation).
      * Hard cap (``_MAX_DEDUP_POSTS``) so a pathological window
        setting can't reintroduce the unbounded scan.

    Pass ``window_days=0`` to scan everything (legacy behaviour);
    intended only for one-off audits, never live checks.
    """
    posts = _load_posts()
    cutoff = _recency_cutoff_iso(window_days) if window_days > 0 else None

    query_tokens = _tokenize(text)
    query_hash = _content_hash(text)

    matches = []
    scanned = 0
    for post in posts:
        if scanned >= _MAX_DEDUP_POSTS:
            break
        if not _within_window(post, cutoff):
            continue
        scanned += 1

        # Exact-hash short-circuit avoids the Jaccard pass entirely
        # for byte-identical content (the common-case re-post detect).
        if post.get("content_hash") and post["content_hash"] == query_hash:
            sim = 1.0
        else:
            sim = _jaccard_set_similarity(
                query_tokens, post.get("_token_set") or _tokenize(post.get("text", ""))
            )

        if sim >= threshold:
            matches.append(
                {
                    "text": post["text"][:100],
                    "platform": post["platform"],
                    "similarity": round(sim, 3),
                    "engagement": post.get("engagement", 0),
                    "posted_at": post.get("posted_at"),
                }
            )
    return sorted(matches, key=lambda x: x["similarity"], reverse=True)


def find_similar(
    text: str,
    top_n: int = 5,
    window_days: int = DEFAULT_DEDUP_WINDOW_DAYS,
) -> list[dict]:
    """Find the most similar past posts.

    Same R-58 bounded-scan semantics as ``check_duplicate``.
    """
    posts = _load_posts()
    cutoff = _recency_cutoff_iso(window_days) if window_days > 0 else None

    query_tokens = _tokenize(text)

    scored = []
    scanned = 0
    for post in posts:
        if scanned >= _MAX_DEDUP_POSTS:
            break
        if not _within_window(post, cutoff):
            continue
        scanned += 1

        sim = _jaccard_set_similarity(
            query_tokens, post.get("_token_set") or _tokenize(post.get("text", ""))
        )
        if sim > 0.05:
            scored.append(
                {
                    "text": post["text"][:100],
                    "platform": post["platform"],
                    "similarity": round(sim, 3),
                    "engagement": post.get("engagement", 0),
                    "likes": post.get("likes", 0),
                    "posted_at": post.get("posted_at"),
                }
            )
    return sorted(scored, key=lambda x: x["similarity"], reverse=True)[:top_n]


def get_winners(top_n: int = 10) -> list[dict]:
    """Get top performing posts by engagement."""
    posts = _load_posts()
    ranked = sorted(posts, key=lambda x: x.get("engagement", 0), reverse=True)
    winners = []
    for post in ranked[:top_n]:
        winners.append(
            {
                "text": post["text"][:80],
                "platform": post["platform"],
                "engagement": post.get("engagement", 0),
                "likes": post.get("likes", 0),
                "posted_at": post.get("posted_at"),
            }
        )
    return winners


def get_winning_patterns() -> dict[str, Any]:
    """Analyze patterns in top performing content."""
    posts = _load_posts()
    if not posts:
        return {"error": "no_posts_in_memory"}

    ranked = sorted(posts, key=lambda x: x.get("engagement", 0), reverse=True)
    cutoff = max(len(ranked) // 4, 1)
    winners = ranked[:cutoff]
    rest = ranked[cutoff:]

    winner_words: dict[str, int] = {}
    for post in winners:
        for word in _tokenize(post.get("text", "")):
            winner_words[word] = winner_words.get(word, 0) + 1

    rest_words: dict[str, int] = {}
    for post in rest:
        for word in _tokenize(post.get("text", "")):
            rest_words[word] = rest_words.get(word, 0) + 1

    winner_ratio = {}
    for word, count in winner_words.items():
        rest_count = rest_words.get(word, 0)
        if count >= 2:
            ratio = count / max(rest_count, 0.5)
            winner_ratio[word] = ratio

    hot_words = sorted(winner_ratio.items(), key=lambda x: x[1], reverse=True)[:15]

    platform_engagement: dict[str, list[int]] = {}
    for post in posts:
        p = post["platform"]
        if p not in platform_engagement:
            platform_engagement[p] = []
        platform_engagement[p].append(post.get("engagement", 0))

    platform_avg = {p: round(sum(v) / len(v), 1) for p, v in platform_engagement.items()}

    return {
        "analyzed_posts": len(posts),
        "winner_count": len(winners),
        "hot_words": [{"word": w, "winner_ratio": round(r, 1)} for w, r in hot_words],
        "platform_avg_engagement": platform_avg,
        "top_winners": get_winners(5),
    }


def sync_to_sharepoint() -> dict[str, int]:
    """Force-sync local posts → SharePoint (fills gaps)."""
    proxy = _get_proxy()
    if not proxy:
        return {"error": "sharepoint_unavailable", "synced": 0}

    local = _load_local()
    synced = 0
    skipped = 0

    for post in local:
        ch = post.get("content_hash", "")
        if not ch:
            continue
        existing = _sp_find_by_hash(ch)
        if existing:
            skipped += 1
            continue
        sp_id = _sp_create(post)
        if sp_id:
            synced += 1

    return {"synced": synced, "skipped": skipped, "total_local": len(local)}


# ── CLI ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Content Memory Engine")
    parser.add_argument("--ingest", action="store_true", help="Ingest recent posts")
    parser.add_argument("--check", type=str, help="Check if topic was posted before")
    parser.add_argument("--similar", type=str, help="Find similar past posts")
    parser.add_argument("--winners", action="store_true", help="Show top performers")
    parser.add_argument("--patterns", action="store_true", help="Analyze winning patterns")
    parser.add_argument("--sync", action="store_true", help="Sync local → SharePoint")
    parser.add_argument("--stats", action="store_true", help="Show memory stats")
    parser.add_argument("--days", type=int, default=30, help="Lookback for ingestion")
    parser.add_argument("--niche", type=str, default=None, help="Niche ID (required for --ingest)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.stats:
        stats = get_memory_stats(args.niche)
        if args.json:
            print(json.dumps(stats, indent=2))
        else:
            print("\nContent Memory Stats:")
            print(f"  Posts: {stats.get('total_posts', 0)}")
            print(f"  Viral: {stats.get('viral_posts', 0)}")
            print(f"  Avg engagement: {stats.get('avg_engagement', 0):.0f}")
            for p, c in stats.get("by_platform", {}).items():
                print(f"    {p}: {c}")

    elif args.ingest:
        if not args.niche:
            print("Error: --niche is required for --ingest (e.g. --niche ai_creators)")
            sys.exit(1)
        result = ingest_from_analytics(args.days, niche_id=args.niche)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"Ingested {result['new_posts_ingested']} new posts "
                f"({result['synced_to_sharepoint']} synced to SharePoint, "
                f"total: {result['total_posts']})"
            )

    elif args.sync:
        result = sync_to_sharepoint()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"Synced {result['synced']} posts to SharePoint "
                f"(skipped {result['skipped']} existing)"
            )

    elif args.check:
        dupes = check_duplicate(args.check)
        if args.json:
            print(json.dumps(dupes, indent=2))
        elif dupes:
            print(f"\nFound {len(dupes)} similar post(s):")
            for d in dupes[:5]:
                print(f"  [{d['platform']}] {d['similarity']:.0%} match — {d['text']}")
                print(f"    engagement: {d['engagement']}, posted: {d.get('posted_at', '?')}")
        else:
            print("No similar content found — safe to post.")

    elif args.similar:
        similar = find_similar(args.similar)
        if args.json:
            print(json.dumps(similar, indent=2))
        else:
            print("\nMost similar past posts:")
            for s in similar:
                print(f"  [{s['platform']}] {s['similarity']:.0%} — {s['text']}")
                print(f"    {s['likes']} likes, posted: {s.get('posted_at', '?')}")

    elif args.winners:
        winners = get_winners()
        if args.json:
            print(json.dumps(winners, indent=2))
        else:
            print("\nTop Performing Posts:")
            for i, w in enumerate(winners, 1):
                print(f"  {i:2d}. [{w['platform']}] {w['text']}")
                print(f"      engagement: {w['engagement']}, likes: {w['likes']}")

    elif args.patterns:
        patterns = get_winning_patterns()
        if args.json:
            print(json.dumps(patterns, indent=2, default=str))
        else:
            print(
                f"\nWinning Content Patterns ({patterns.get('analyzed_posts', 0)} posts analyzed)"
            )
            print("\n  Words that win:")
            for hw in patterns.get("hot_words", [])[:10]:
                bar = "#" * min(int(hw["winner_ratio"]), 20)
                print(f"    {hw['word']:20s} {hw['winner_ratio']:>5.1f}x  {bar}")
            print("\n  Platform avg engagement:")
            for p, avg in patterns.get("platform_avg_engagement", {}).items():
                print(f"    {p:12s} {avg:>10.0f}")

    else:
        print(
            "Usage: --ingest | --check 'topic' | --similar 'text' | --winners | --patterns | --sync"
        )
