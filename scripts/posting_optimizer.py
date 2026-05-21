#!/usr/bin/env python3
"""
Gen Lab — Posting Time Optimizer
Analyzes historical post performance to find optimal posting windows per platform.
Uses data from social_analytics.py to build a heatmap of engagement by day/hour.

Usage:
    python3 scripts/posting_optimizer.py
    python3 scripts/posting_optimizer.py --json
    python3 scripts/posting_optimizer.py --platform instagram
"""
import argparse
import json
import logging
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Run via: uv run --package genlab-core python scripts/posting_optimizer.py
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from social_analytics import (
    get_facebook_analytics,
    get_instagram_analytics,
    get_threads_analytics,
    get_x_analytics,
    get_youtube_analytics,
)

logger = logging.getLogger("posting_optimizer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SCHEDULE_FILE = os.path.expanduser("~/.genlab/optimal_schedule.json")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse various timestamp formats from platform APIs."""
    if not ts:
        return None
    for fmt in [
        "%Y-%m-%dT%H:%M:%S+0000",
        "%Y-%m-%dT%H:%M:%S.%f+0000",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
    ]:
        try:
            return datetime.strptime(ts, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(ts.replace("+0000", "+00:00"))
    except ValueError:
        return None


def _engagement_score(post: dict) -> float:
    """Delegate to canonical engagement formula in content_memory."""
    from content_memory import engagement_score
    return engagement_score(post)


def _ist_hour(utc_dt: datetime) -> tuple[int, int]:
    """Convert UTC datetime to IST day-of-week and hour."""
    ist = utc_dt + timedelta(hours=5, minutes=30)
    return ist.weekday(), ist.hour


def analyze_platform(platform: str, data: dict) -> dict[str, Any]:
    """Analyze posting patterns for a single platform."""
    posts = (
        data.get("recent_videos")
        or data.get("recent_posts")
        or data.get("recent_tweets")
        or []
    )

    if not posts:
        return {"platform": platform, "sample_size": 0, "best_windows": []}

    # Build engagement heatmap: (day, hour) -> [scores]
    heatmap: dict[tuple[int, int], list[float]] = defaultdict(list)

    for post in posts:
        ts_str = (
            post.get("published")
            or post.get("posted")
            or post.get("created_at")
        )
        ts = _parse_timestamp(ts_str)
        if not ts:
            continue

        score = _engagement_score(post)
        day, hour = _ist_hour(ts)
        heatmap[(day, hour)].append(score)

    if not heatmap:
        return {"platform": platform, "sample_size": len(posts), "best_windows": []}

    # Average engagement per slot
    avg_engagement = {}
    for (day, hour), scores in heatmap.items():
        avg_engagement[(day, hour)] = sum(scores) / len(scores)

    # Rank slots
    ranked = sorted(avg_engagement.items(), key=lambda x: x[1], reverse=True)

    best_windows = []
    for (day, hour), avg in ranked[:5]:
        best_windows.append({
            "day": DAY_NAMES[day],
            "hour_ist": f"{hour:02d}:00",
            "avg_engagement": round(avg, 1),
            "post_count": len(heatmap[(day, hour)]),
        })

    # Also compute hour-only aggregates (ignoring day)
    hour_totals: dict[int, list[float]] = defaultdict(list)
    for (_day, hour), scores in heatmap.items():
        hour_totals[hour].extend(scores)

    best_hours = sorted(
        [(h, sum(s) / len(s)) for h, s in hour_totals.items()],
        key=lambda x: x[1],
        reverse=True,
    )[:3]

    return {
        "platform": platform,
        "sample_size": len(posts),
        "best_windows": best_windows,
        "best_hours_ist": [{"hour": f"{h:02d}:00", "avg_engagement": round(a, 1)} for h, a in best_hours],
    }


def build_schedule(days: int = 90, niche_id: str | None = None) -> dict[str, Any]:
    """Build optimal posting schedule from all platform data.

    When niche_id is provided, the schedule is saved under that key in the
    schedule file (same nested pattern as engagement baselines).
    """
    platform_fns = {
        "youtube": get_youtube_analytics,
        "instagram": get_instagram_analytics,
        "facebook": get_facebook_analytics,
        "x_twitter": get_x_analytics,
        "threads": get_threads_analytics,
    }

    schedule = {"generated_at": datetime.now(UTC).isoformat(), "platforms": {}}
    if niche_id:
        schedule["niche_id"] = niche_id

    for name, fn in platform_fns.items():
        try:
            data = fn(days)
            if "error" not in data:
                analysis = analyze_platform(name, data)
                schedule["platforms"][name] = analysis
            else:
                schedule["platforms"][name] = {"platform": name, "error": data["error"]}
        except Exception as e:
            schedule["platforms"][name] = {"platform": name, "error": str(e)[:200]}

    # Cross-platform recommendation
    all_windows = []
    for pdata in schedule["platforms"].values():
        for w in pdata.get("best_windows", []):
            all_windows.append(w)

    if all_windows:
        # Find most common best hour across platforms
        from collections import Counter
        hour_counts = Counter(w["hour_ist"] for w in all_windows)
        schedule["recommended_posting_time_ist"] = hour_counts.most_common(1)[0][0]

    return schedule


def _load_schedule_file() -> dict:
    """Load the full schedule file (niche-keyed)."""
    if not os.path.exists(SCHEDULE_FILE):
        return {}
    try:
        with open(SCHEDULE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_schedule(schedule: dict[str, Any], niche_id: str | None = None):
    """Save schedule under a niche key (or _global)."""
    all_data = _load_schedule_file()

    # Auto-migrate flat (legacy) format → nested
    if all_data and "platforms" in all_data and "_global" not in all_data:
        all_data = {"_global": all_data}

    key = niche_id or "_global"
    all_data[key] = schedule

    os.makedirs(os.path.dirname(SCHEDULE_FILE), exist_ok=True)
    tmp = SCHEDULE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(all_data, f, indent=2, default=str)
    os.replace(tmp, SCHEDULE_FILE)


def load_schedule(niche_id: str | None = None) -> dict[str, Any]:
    """Load schedule for a specific niche (or _global)."""
    all_data = _load_schedule_file()

    # Handle legacy flat format
    if all_data and "platforms" in all_data and "_global" not in all_data:
        return all_data

    if niche_id and niche_id in all_data:
        return all_data[niche_id]
    return all_data.get("_global", {})


def print_schedule(schedule: dict[str, Any]) -> None:
    """Print human-readable schedule."""
    print(f"\n{'=' * 60}")
    print("  Gen Lab — Optimal Posting Schedule")
    print("  Based on historical engagement data")
    print(f"{'=' * 60}\n")

    for name, pdata in schedule.get("platforms", {}).items():
        if "error" in pdata:
            print(f"  {name.upper():12s}  ⚠ {pdata['error'][:60]}")
            continue

        sample = pdata.get("sample_size", 0)
        print(f"  {name.upper()} ({sample} posts analyzed)")

        for w in pdata.get("best_windows", [])[:3]:
            bar = "█" * min(int(w["avg_engagement"] / 100), 30)
            print(f"    {w['day']} {w['hour_ist']} IST  — avg engagement: {w['avg_engagement']:>8.0f}  {bar}")

        best_hours = pdata.get("best_hours_ist", [])
        if best_hours:
            hours_str = ", ".join(h["hour"] for h in best_hours)
            print(f"    → Best hours (any day): {hours_str}")
        print()

    rec = schedule.get("recommended_posting_time_ist")
    if rec:
        print(f"  {'─' * 50}")
        print(f"  RECOMMENDED: Post at {rec} IST across all platforms")
        print(f"  {'─' * 50}")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Posting Time Optimizer")
    parser.add_argument("--days", type=int, default=90, help="Lookback period")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    parser.add_argument("--platform", type=str, help="Single platform")
    parser.add_argument("--niche", type=str, default=None, help="Niche ID (e.g. gaming, ai_creators)")
    args = parser.parse_args()

    schedule = build_schedule(args.days, niche_id=args.niche)

    # Filter to single platform if specified
    if args.platform and args.platform in schedule.get("platforms", {}):
        schedule["platforms"] = {args.platform: schedule["platforms"][args.platform]}

    # Save under niche key
    _save_schedule(schedule, niche_id=args.niche)

    if args.json:
        print(json.dumps(schedule, indent=2, default=str))
    else:
        print_schedule(schedule)
        print(f"  Saved to: {SCHEDULE_FILE}")
