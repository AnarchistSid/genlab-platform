#!/usr/bin/env python3
"""
Gen Lab — Intelligence Hub
Unified read/write layer across all intelligence systems.
Every system writes to its own storage; this hub reads from all of them
and provides a single API for consumers (morning briefing, pipeline, publisher).

Niche-aware: pass niche_id to filter signals per channel.
Platform-aware: new platforms just need to appear in social_analytics data.

Usage (as library):
    from intelligence_hub import get_full_state, get_trend_boost, get_optimal_time
    from intelligence_hub import check_content_overlap, get_winning_patterns

Usage (CLI — quick intelligence snapshot):
    python3 scripts/intelligence_hub.py
    python3 scripts/intelligence_hub.py --niche gaming --json
"""
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Run via: uv run --package genlab-core python scripts/intelligence_hub.py
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("intelligence_hub")

# ── Registry ────────────────────────────────────────────────────────

KNOWN_NICHES = {
    "ai_creators":  {"brand": "Blackbox Brief",  "folder": "BlackboxBrief"},
    "gaming":   {"brand": "CriticalRush",     "folder": "CriticalRush"},
    "sports":   {"brand": "ClutchWire",       "folder": "ClutchWire"},
    "movies":   {"brand": "SpliceReel",       "folder": "SpliceReel"},
    "anime":    {"brand": "FrameDrift",        "folder": "FrameDrift"},
}

KNOWN_PLATFORMS = [
    "youtube", "instagram", "facebook", "x_twitter", "threads", "tiktok",
]

# ── File locations (each system writes here) ────────────────────────

GENLAB_DIR = Path.home() / ".genlab"
TRENDS_FILE = GENLAB_DIR / "trend_cache.json"
SCHEDULE_FILE = GENLAB_DIR / "optimal_schedule.json"
VIRAL_FILE = GENLAB_DIR / "viral_alerts.json"
BASELINES_FILE = GENLAB_DIR / "engagement_baselines.json"
MEMORY_CACHE = GENLAB_DIR / "content_memory" / "posts.json"
BRIEFING_FILE = GENLAB_DIR / "morning_briefing.json"


def _read_json(path: Path) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return None


# ── Trend Signals ───────────────────────────────────────────────────

def get_cached_trends(niche_id: str | None = None) -> dict[str, Any]:
    """Read cached Google Trends data. Returns {} if no cache."""
    data = _read_json(TRENDS_FILE)
    if not data:
        return {}

    if niche_id and "niches" in data:
        niche_data = data.get("niches", {}).get(niche_id, {})
        return {
            "generated_at": data.get("generated_at"),
            "daily_trending": data.get("daily_trending", []),
            "niche": niche_data,
        }
    return data


def get_trend_boost(topic: str, niche_id: str | None = None) -> float:
    """Get a 0–100 trend boost score for a topic.

    Reads from cached trends. Returns 0 if no data.
    Used by pipeline scoring to prioritize trending stories.
    """
    data = get_cached_trends(niche_id)
    if not data:
        return 0.0

    # Check daily trending (exact match)
    for t in data.get("daily_trending", []):
        if topic.lower() in t.get("query", "").lower():
            return 80.0  # Strong boost for trending topics

    # Check niche-specific data
    niche = data.get("niche", {}) if niche_id else {}
    for bucket in ["trending_topics", "hot_topics"]:
        for t in niche.get(bucket, []):
            kw = t.get("keyword", "").lower()
            if kw in topic.lower() or topic.lower() in kw:
                score = t.get("score", 50)
                direction = t.get("direction", "stable")
                bonus = {"rising": 20, "stable": 0, "falling": -15}.get(direction, 0)
                return min(score + bonus, 100.0)

    return 0.0


# ── Posting Schedule ────────────────────────────────────────────────

def get_cached_schedule(niche_id: str | None = None) -> dict[str, Any]:
    """Read cached posting optimizer output, optionally niche-scoped."""
    data = _read_json(SCHEDULE_FILE) or {}
    if not data:
        return {}

    # Handle legacy flat format (has "platforms" at top level)
    if "platforms" in data and "_global" not in data:
        return data

    # Nested niche-keyed format
    if niche_id and niche_id in data:
        return data[niche_id]
    return data.get("_global", {})


def get_optimal_time(platform: str, niche_id: str | None = None) -> str | None:
    """Best posting time in IST for a platform. Returns 'HH:MM' or None.

    Used by publishing daemon to choose when to post.
    """
    schedule = get_cached_schedule(niche_id)
    if not schedule:
        return None

    # Platform-specific recommendation
    pdata = schedule.get("platforms", {}).get(platform, {})
    best_hours = pdata.get("best_hours_ist", [])
    if best_hours:
        return best_hours[0].get("hour")

    # Cross-platform fallback
    return schedule.get("recommended_posting_time_ist")


def get_all_optimal_times(niche_id: str | None = None) -> dict[str, str | None]:
    """Best posting time per platform."""
    return {p: get_optimal_time(p, niche_id) for p in KNOWN_PLATFORMS}


# ── Viral Alerts ────────────────────────────────────────────────────

def get_recent_viral_alerts(hours: int = 48) -> list[dict]:
    """Get viral/trending alerts from the last N hours."""
    alerts = _read_json(VIRAL_FILE) or []
    if not alerts:
        return []

    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    recent = [a for a in alerts if a.get("detected_at", "") >= cutoff]
    return recent[-20:]


def get_baselines(niche_id: str | None = None) -> dict[str, float]:
    """Current engagement baselines per platform, optionally niche-scoped."""
    data = _read_json(BASELINES_FILE) or {}
    if not data:
        return {}

    # Handle legacy flat format (no nested dicts)
    if not any(isinstance(v, dict) for v in data.values()):
        return data

    # Nested niche-keyed format
    if niche_id and niche_id in data:
        return data[niche_id]
    return data.get("_global", {})


# ── Content Memory ──────────────────────────────────────────────────

def get_memory_stats(niche_id: str | None = None) -> dict[str, Any]:
    """Content memory summary stats."""
    try:
        from content_memory import _load_posts
        posts = _load_posts()
    except Exception:
        posts = _read_json(MEMORY_CACHE) or []

    if niche_id:
        posts = [p for p in posts if p.get("niche_id") == niche_id]

    if not posts:
        return {"total_posts": 0}

    platforms = {}
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


def check_content_overlap(text: str, niche_id: str | None = None, threshold: float = 0.4) -> dict[str, Any]:
    """Check if similar content exists in memory.

    Used by blueprint composition to prevent duplicate posts.
    Returns: {is_duplicate: bool, matches: [...], max_similarity: float}
    """
    try:
        from content_memory import check_duplicate
        matches = check_duplicate(text, threshold=threshold)
        if niche_id:
            matches = [m for m in matches if m.get("niche_id") == niche_id]
        return {
            "is_duplicate": len(matches) > 0,
            "max_similarity": matches[0]["similarity"] if matches else 0.0,
            "matches": matches[:3],
        }
    except Exception as e:
        logger.warning("Content overlap check failed: %s", e)
        return {"is_duplicate": False, "max_similarity": 0.0, "matches": [], "error": str(e)}


def get_winning_patterns(niche_id: str | None = None, top_n: int = 5) -> dict[str, Any]:
    """Get patterns from top-performing content.

    Used by content generation to replicate successful approaches.
    """
    try:
        from content_memory import _load_posts, _tokenize

        posts = _load_posts()
        if niche_id:
            posts = [p for p in posts if p.get("niche_id") == niche_id]
        if not posts:
            return {"analyzed_posts": 0}

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
            if count >= 2:
                ratio = count / max(rest_words.get(word, 0), 0.5)
                winner_ratio[word] = ratio

        hot_words = sorted(winner_ratio.items(), key=lambda x: x[1], reverse=True)[:top_n]

        return {
            "analyzed_posts": len(posts),
            "winner_count": len(winners),
            "hot_words": [{"word": w, "ratio": round(r, 1)} for w, r in hot_words],
        }
    except Exception as e:
        return {"error": str(e)}


# ── Full State (Morning Briefing) ──────────────────────────────────

def get_full_state(niche_id: str | None = None) -> dict[str, Any]:
    """Complete intelligence snapshot. Used by morning briefing."""
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "niche_id": niche_id,
        "trends": get_cached_trends(niche_id),
        "schedule": get_cached_schedule(niche_id),
        "viral_alerts": get_recent_viral_alerts(),
        "baselines": get_baselines(niche_id),
        "content_memory": get_memory_stats(niche_id),
        "optimal_times": get_all_optimal_times(niche_id),
    }


# ── CLI ─────────────────────────────────────────────────────────────

def print_state(state: dict[str, Any]) -> None:
    print(f"\n{'=' * 60}")
    print("  Gen Lab — Intelligence Hub")
    niche = state.get("niche_id")
    if niche:
        brand = KNOWN_NICHES.get(niche, {}).get("brand", niche)
        print(f"  Niche: {brand} ({niche})")
    print(f"  {state['generated_at'][:19]}Z")
    print(f"{'=' * 60}")

    # Content Memory
    mem = state.get("content_memory", {})
    print("\n  CONTENT MEMORY")
    print(f"    Posts tracked:    {mem.get('total_posts', 0)}")
    print(f"    Viral posts:     {mem.get('viral_posts', 0)}")
    print(f"    Avg engagement:  {mem.get('avg_engagement', 0):.0f}")
    if mem.get("by_platform"):
        for p, c in mem["by_platform"].items():
            print(f"      {p:14s} {c} posts")

    # Viral Alerts
    alerts = state.get("viral_alerts", [])
    if alerts:
        print(f"\n  VIRAL ALERTS ({len(alerts)} recent)")
        for a in alerts[-3:]:
            print(f"    {a.get('level', '?')} [{a.get('platform', '?').upper()}] "
                  f"{a.get('multiplier', 0)}x — {a.get('text', '')[:50]}")
    else:
        print("\n  VIRAL ALERTS: none")

    # Trends
    trends = state.get("trends", {})
    if trends:
        daily = trends.get("daily_trending", [])
        if daily:
            print("\n  TRENDING NOW")
            for t in daily[:5]:
                print(f"    - {t.get('query', '?')}")

        niche_data = trends.get("niche", {})
        hot = niche_data.get("hot_topics", [])
        if hot:
            print("\n  NICHE HOT TOPICS")
            for t in hot[:3]:
                arrow = {"rising": "^", "falling": "v", "stable": "="}.get(t.get("direction", ""), "?")
                print(f"    {arrow} {t.get('keyword', '?'):20s} score: {t.get('score', 0):.0f}")
    else:
        print("\n  TRENDS: no cached data (run trend_signals.py --full)")

    # Posting Schedule
    times = state.get("optimal_times", {})
    active_times = {p: t for p, t in times.items() if t}
    if active_times:
        print("\n  OPTIMAL POSTING TIMES (IST)")
        for p, t in active_times.items():
            print(f"    {p:14s} {t}")
    else:
        print("\n  POSTING SCHEDULE: no data (run posting_optimizer.py)")

    # Baselines
    baselines = state.get("baselines", {})
    if baselines:
        print("\n  ENGAGEMENT BASELINES")
        for p, b in baselines.items():
            print(f"    {p:14s} {b:>8.0f} median")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Intelligence Hub")
    parser.add_argument("--niche", type=str, help="Filter by niche_id")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--trend-boost", type=str, help="Get trend boost for a topic")
    parser.add_argument("--overlap", type=str, help="Check content overlap")
    parser.add_argument("--optimal-time", type=str, help="Get optimal time for platform")
    args = parser.parse_args()

    if args.trend_boost:
        score = get_trend_boost(args.trend_boost, args.niche)
        print(json.dumps({"topic": args.trend_boost, "boost": score}) if args.json
              else f"Trend boost for '{args.trend_boost}': {score:.0f}/100")

    elif args.overlap:
        result = check_content_overlap(args.overlap, args.niche)
        print(json.dumps(result, indent=2) if args.json
              else f"Duplicate: {result['is_duplicate']} (max similarity: {result['max_similarity']:.0%})")

    elif args.optimal_time:
        t = get_optimal_time(args.optimal_time, args.niche)
        print(json.dumps({"platform": args.optimal_time, "time_ist": t}) if args.json
              else f"Optimal time for {args.optimal_time}: {t or 'no data'} IST")

    else:
        state = get_full_state(args.niche)
        if args.json:
            print(json.dumps(state, indent=2, default=str))
        else:
            print_state(state)
