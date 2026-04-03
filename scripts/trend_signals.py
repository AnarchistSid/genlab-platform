#!/usr/bin/env python3
"""
Gen Lab — Google Trends Signal Engine
Checks trending topics and scores them for content creation potential.
Integrates with the pipeline to prioritize stories on rising topics.

Usage:
    python3 scripts/trend_signals.py                         # Show current trends
    python3 scripts/trend_signals.py --query "Elden Ring"    # Check specific topic
    python3 scripts/trend_signals.py --niche gaming          # Niche-specific trends
    python3 scripts/trend_signals.py --json                  # Machine-readable output
"""
import argparse
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("trend_signals")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TRENDS_CACHE = os.path.expanduser("~/.genlab/trend_cache.json")

# Niche-specific seed keywords for trend discovery
NICHE_SEEDS = {
    "gaming": [
        "gaming", "PS5", "Xbox", "Nintendo", "Steam", "Elden Ring",
        "GTA 6", "esports", "Twitch", "game release",
    ],
    "ai_creators": [
        "AI", "ChatGPT", "OpenAI", "Sora", "Runway", "Midjourney",
        "deepfake", "machine learning", "LLM", "artificial intelligence",
    ],
    "sports": [
        "NFL", "NBA", "Premier League", "UFC", "FIFA",
        "Champions League", "cricket", "F1", "Olympics",
    ],
    "movies": [
        "Marvel", "movie trailer", "box office", "Netflix",
        "Disney", "new movie", "Oscar", "blockbuster",
    ],
    "anime": [
        "anime", "manga", "One Piece", "Jujutsu Kaisen",
        "Dragon Ball", "Crunchyroll", "new anime", "Studio Ghibli",
    ],
}


def get_trending_searches(geo: str = "US") -> list[dict[str, Any]]:
    """Get current trending searches from Google Trends."""
    from pytrends.request import TrendReq

    pytrends = TrendReq(hl="en-US", tz=330)  # IST offset
    try:
        trending = pytrends.trending_searches(pn="united_states" if geo == "US" else geo)
        results = []
        for _, row in trending.head(20).iterrows():
            results.append({"query": row[0], "source": "google_trends_daily"})
        return results
    except Exception as e:
        logger.warning("Failed to fetch trending searches: %s", e)
        return []


def get_realtime_trends(geo: str = "US") -> list[dict[str, Any]]:
    """Get real-time trending topics."""
    from pytrends.request import TrendReq

    pytrends = TrendReq(hl="en-US", tz=330)
    try:
        rt = pytrends.realtime_trending_searches(pn="US")
        results = []
        if rt is not None and not rt.empty:
            for _, row in rt.head(15).iterrows():
                title = row.get("title", row.get("entityNames", ""))
                if isinstance(title, list):
                    title = ", ".join(title)
                results.append({
                    "query": str(title)[:100],
                    "source": "google_trends_realtime",
                })
        return results
    except Exception as e:
        logger.warning("Failed to fetch realtime trends: %s", e)
        return []


def check_topic_interest(keywords: list[str], timeframe: str = "now 7-d") -> dict[str, Any]:
    """Check interest level for specific keywords over time."""
    from pytrends.request import TrendReq

    if not keywords:
        return {}

    pytrends = TrendReq(hl="en-US", tz=330)

    # Google Trends allows max 5 keywords at once
    results = {}
    for batch_start in range(0, len(keywords), 5):
        batch = keywords[batch_start:batch_start + 5]
        try:
            pytrends.build_payload(batch, cat=0, timeframe=timeframe, geo="", gprop="")
            interest = pytrends.interest_over_time()

            if interest is not None and not interest.empty:
                for kw in batch:
                    if kw in interest.columns:
                        values = interest[kw].tolist()
                        current = values[-1] if values else 0
                        peak = max(values) if values else 0
                        avg = sum(values) / len(values) if values else 0

                        # Trend direction: compare last 2 days vs previous
                        if len(values) >= 4:
                            recent = sum(values[-2:]) / 2
                            earlier = sum(values[-4:-2]) / 2
                            direction = "rising" if recent > earlier * 1.2 else (
                                "falling" if recent < earlier * 0.8 else "stable"
                            )
                        else:
                            direction = "insufficient_data"

                        results[kw] = {
                            "current": current,
                            "peak": peak,
                            "average": round(avg, 1),
                            "direction": direction,
                            "score": min(round(current / max(avg, 1) * 100, 1), 100.0),
                        }
            time.sleep(1)  # Rate limit
        except Exception as e:
            logger.warning("Failed to check interest for %s: %s", batch, e)
            for kw in batch:
                results[kw] = {"error": str(e)[:100]}

    return results


def score_story_topics(topics: list[str]) -> list[dict[str, Any]]:
    """Score a list of story topics by their trend potential."""
    interest = check_topic_interest(topics, timeframe="now 7-d")

    scored = []
    for topic in topics:
        data = interest.get(topic, {})
        if "error" in data:
            scored.append({"topic": topic, "trend_score": 0, "error": data["error"]})
            continue

        # Composite score: current interest + direction bonus
        base_score = data.get("score", 0)
        direction = data.get("direction", "stable")
        direction_bonus = {"rising": 30, "stable": 0, "falling": -20}.get(direction, 0)

        scored.append({
            "topic": topic,
            "trend_score": min(round(base_score + direction_bonus, 1), 100.0),
            "current_interest": data.get("current", 0),
            "direction": direction,
            "peak": data.get("peak", 0),
        })

    return sorted(scored, key=lambda x: x["trend_score"], reverse=True)


def get_niche_trends(niche: str) -> dict[str, Any]:
    """Get trend analysis for a specific niche."""
    seeds = NICHE_SEEDS.get(niche)
    if not seeds:
        return {"error": f"Unknown niche: {niche}. Options: {list(NICHE_SEEDS.keys())}"}

    interest = check_topic_interest(seeds, timeframe="now 7-d")

    # Sort by score
    ranked = sorted(
        [(kw, data) for kw, data in interest.items() if "error" not in data],
        key=lambda x: x[1].get("score", 0),
        reverse=True,
    )

    return {
        "niche": niche,
        "checked_at": datetime.now(UTC).isoformat(),
        "trending_topics": [
            {"keyword": kw, **data}
            for kw, data in ranked
            if data.get("direction") == "rising"
        ],
        "hot_topics": [
            {"keyword": kw, **data}
            for kw, data in ranked[:5]
        ],
        "cold_topics": [
            {"keyword": kw, **data}
            for kw, data in ranked
            if data.get("direction") == "falling"
        ],
    }


def build_full_report() -> dict[str, Any]:
    """Build a full trend report across all niches."""
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "daily_trending": get_trending_searches(),
        "niches": {},
    }

    for niche in NICHE_SEEDS:
        try:
            report["niches"][niche] = get_niche_trends(niche)
        except Exception as e:
            report["niches"][niche] = {"error": str(e)[:200]}
        time.sleep(2)  # Rate limit between niches

    return report


def print_report(report: dict[str, Any]) -> None:
    """Print human-readable trend report."""
    print(f"\n{'=' * 60}")
    print("  Gen Lab — Trend Signals")
    print(f"  {report['generated_at'][:19]}Z")
    print(f"{'=' * 60}\n")

    # Daily trending
    daily = report.get("daily_trending", [])
    if daily:
        print("  📈 TRENDING NOW (Google)")
        for i, t in enumerate(daily[:10], 1):
            print(f"    {i:2d}. {t['query']}")
        print()

    # Per-niche
    for niche, data in report.get("niches", {}).items():
        if "error" in data:
            print(f"  {niche.upper():12s}  ⚠ {data['error'][:60]}")
            continue

        rising = data.get("trending_topics", [])
        hot = data.get("hot_topics", [])

        print(f"  {niche.upper()}")
        if rising:
            print("    🔥 Rising:")
            for t in rising[:3]:
                print(f"       {t['keyword']} — score {t['score']:.0f}, interest {t['current']}")
        if hot:
            print("    🎯 Hottest:")
            for t in hot[:3]:
                print(f"       {t['keyword']} — score {t['score']:.0f} ({t['direction']})")
        if not rising and not hot:
            print("    No significant trends detected")
        print()

    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Trends Signal Engine")
    parser.add_argument("--query", type=str, help="Check specific topic(s), comma-separated")
    parser.add_argument("--niche", type=str, help="Check niche-specific trends")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--full", action="store_true", help="Full report across all niches")
    args = parser.parse_args()

    if args.query:
        topics = [t.strip() for t in args.query.split(",")]
        scored = score_story_topics(topics)
        if args.json:
            print(json.dumps(scored, indent=2))
        else:
            print("\nTrend scores for queried topics:")
            for s in scored:
                arrow = {"rising": "↑", "falling": "↓", "stable": "→"}.get(s.get("direction", ""), "?")
                print(f"  {arrow} {s['topic']:30s}  score: {s['trend_score']:>6.1f}  interest: {s.get('current_interest', '?')}")

    elif args.niche:
        data = get_niche_trends(args.niche)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print_report({"generated_at": datetime.now(UTC).isoformat(), "niches": {args.niche: data}})

    elif args.full:
        report = build_full_report()
        os.makedirs(os.path.dirname(TRENDS_CACHE), exist_ok=True)
        with open(TRENDS_CACHE, "w") as f:
            json.dump(report, f, indent=2, default=str)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print_report(report)
            print(f"  Saved to: {TRENDS_CACHE}")

    else:
        # Default: show daily trending + ai_creators niche
        trending = get_trending_searches()
        print("\n  📈 Top 10 Trending Searches:")
        for i, t in enumerate(trending[:10], 1):
            print(f"    {i:2d}. {t['query']}")

        print("\n  Checking AI/Tech niche trends...")
        ai_data = get_niche_trends("ai_creators")
        hot = ai_data.get("hot_topics", [])
        if hot:
            print("\n  🎯 AI/Tech Hottest:")
            for t in hot[:5]:
                arrow = {"rising": "↑", "falling": "↓", "stable": "→"}.get(t.get("direction", ""), "?")
                print(f"    {arrow} {t['keyword']:20s}  score: {t['score']:.0f}")
