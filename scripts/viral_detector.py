#!/usr/bin/env python3
"""
Gen Lab — Viral Detection & Alert System
Monitors recent posts for engagement velocity spikes.
Sends macOS notifications when a post is gaining traction.
Run via launchd every 2 hours or on-demand.

Usage:
    uv run --package genlab-core python scripts/viral_detector.py
    uv run --package genlab-core python scripts/viral_detector.py --json
    uv run --package genlab-core python scripts/viral_detector.py --threshold 5
"""

# NOTE: Run via: uv run --package genlab-core python scripts/viral_detector.py
import argparse
import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("viral_detector")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

VIRAL_LOG = Path.home() / ".genlab" / "viral_alerts.json"
BASELINE_FILE = Path.home() / ".genlab" / "engagement_baselines.json"

# Engagement thresholds (multiplier over baseline to trigger alert)
DEFAULT_VIRAL_THRESHOLD = 5.0  # 5x normal engagement = viral
TRENDING_THRESHOLD = 2.5  # 2.5x = trending


def _send_notification(title: str, message: str):
    """Send desktop notification (macOS) or log alert (Linux)."""
    if sys.platform == "darwin":
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{message}" with title "{title}" sound name "Glass"',
                ],
                capture_output=True,
                timeout=5,
            )
        except Exception as e:
            logger.warning("Failed to send notification: %s", e)
    else:
        logger.info("[VIRAL ALERT] %s — %s", title, message)


def _load_baselines(niche_id: str | None = None) -> dict[str, float]:
    """Load engagement baselines per platform, optionally scoped by niche.

    File format: {"_global": {"youtube": N, ...}, "ai_creators": {"youtube": N, ...}, ...}
    Legacy format (flat dict) is auto-migrated on first read.
    """
    if not BASELINE_FILE.exists():
        return {}
    try:
        data = json.loads(BASELINE_FILE.read_text())
    except json.JSONDecodeError:
        return {}

    # Auto-migrate flat (legacy) format → nested
    if data and not any(isinstance(v, dict) for v in data.values()):
        data = {"_global": data}
        _save_baselines_raw(data)

    if niche_id and niche_id in data:
        return data[niche_id]
    return data.get("_global", {})


def _save_baselines_raw(data: dict):
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(data, indent=2))


def _save_baselines(baselines: dict[str, float], niche_id: str | None = None):
    """Save baselines under a niche key (or _global)."""
    if BASELINE_FILE.exists():
        try:
            all_data = json.loads(BASELINE_FILE.read_text())
            if not any(isinstance(v, dict) for v in all_data.values()):
                all_data = {"_global": all_data}  # migrate
        except (json.JSONDecodeError, OSError):
            all_data = {}
    else:
        all_data = {}

    key = niche_id or "_global"
    all_data[key] = baselines
    _save_baselines_raw(all_data)


def _load_alert_log() -> list[dict]:
    if VIRAL_LOG.exists():
        try:
            return json.loads(VIRAL_LOG.read_text())
        except json.JSONDecodeError:
            pass
    return []


def _save_alert_log(alerts: list[dict]):
    VIRAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    VIRAL_LOG.write_text(json.dumps(alerts[-100:], indent=2, default=str))  # Keep last 100


def _already_alerted(content_hash: str, alerts: list[dict]) -> bool:
    """Check if we already sent an alert for this post."""
    all_hashes = {a.get("content_hash") for a in alerts}
    return content_hash in all_hashes


def compute_baselines(days: int = 30, niche_id: str | None = None) -> dict[str, float]:
    """Compute average engagement per platform from recent posts."""
    from content_memory import engagement_score as _eng_score
    from social_analytics import (
        get_facebook_analytics,
        get_instagram_analytics,
        get_threads_analytics,
        get_x_analytics,
        get_youtube_analytics,
    )

    baselines = {}
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
                continue

            posts = (
                data.get("recent_videos")
                or data.get("recent_posts")
                or data.get("recent_tweets")
                or []
            )

            if not posts:
                continue

            engagements = []
            for post in posts:
                engagements.append(_eng_score(post))

            if engagements:
                # Use median to avoid skew from one viral post
                sorted_eng = sorted(engagements)
                mid = len(sorted_eng) // 2
                median = (
                    sorted_eng[mid]
                    if len(sorted_eng) % 2
                    else ((sorted_eng[mid - 1] + sorted_eng[mid]) / 2)
                )
                baselines[platform] = max(median, 1)  # Floor at 1

        except Exception as e:
            logger.warning("Failed to compute baseline for %s: %s", platform, e)

    _save_baselines(baselines, niche_id)
    return baselines


def detect_viral(
    threshold: float = DEFAULT_VIRAL_THRESHOLD, days: int = 7, niche_id: str | None = None
) -> list[dict[str, Any]]:
    """Detect posts that are performing significantly above baseline."""
    import hashlib

    from content_memory import _content_hash as _make_hash
    from content_memory import engagement_score as _eng_score
    from content_memory import flag_viral as _flag_viral
    from social_analytics import (
        get_facebook_analytics,
        get_instagram_analytics,
        get_threads_analytics,
        get_x_analytics,
        get_youtube_analytics,
    )

    baselines = _load_baselines(niche_id)
    if not baselines:
        logger.info("No baselines found, computing...")
        baselines = compute_baselines(30, niche_id)

    alerts = _load_alert_log()
    new_alerts = []

    platform_fns = {
        "youtube": get_youtube_analytics,
        "instagram": get_instagram_analytics,
        "facebook": get_facebook_analytics,
        "x_twitter": get_x_analytics,
        "threads": get_threads_analytics,
    }

    for platform, fn in platform_fns.items():
        baseline = baselines.get(platform, 10)

        try:
            data = fn(days)
            if "error" in data:
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
                likes = post.get("likes", 0) or post.get("views", 0) or 0
                comments = post.get("comments", 0) or 0
                engagement = _eng_score(post)

                multiplier = engagement / max(baseline, 1)
                content_hash = hashlib.md5(f"{platform}:{text[:100]}".encode()).hexdigest()[:12]

                if multiplier >= threshold and not _already_alerted(content_hash, alerts):
                    level = "VIRAL"

                    alert = {
                        "content_hash": content_hash,
                        "platform": platform,
                        "text": text[:100],
                        "engagement": engagement,
                        "likes": likes,
                        "comments": comments,
                        "multiplier": round(multiplier, 1),
                        "baseline": round(baseline, 1),
                        "level": level,
                        "niche_id": niche_id,
                        "detected_at": datetime.now(UTC).isoformat(),
                        "posted_at": post.get("published")
                        or post.get("posted")
                        or post.get("created_at"),
                    }
                    new_alerts.append(alert)

                    # Flag in content memory so winning patterns update
                    try:
                        mem_hash = _make_hash(text)
                        _flag_viral(mem_hash, multiplier, platform)
                    except Exception as e:
                        logger.debug("Could not flag viral in memory: %s", e)

                    _send_notification(
                        f"VIRAL on {platform.upper()}",
                        f"{text[:60]}... — {likes} likes ({multiplier:.0f}x normal)",
                    )

                elif multiplier >= TRENDING_THRESHOLD and not _already_alerted(
                    content_hash, alerts
                ):
                    alert = {
                        "content_hash": content_hash,
                        "platform": platform,
                        "text": text[:100],
                        "engagement": engagement,
                        "likes": likes,
                        "comments": comments,
                        "multiplier": round(multiplier, 1),
                        "baseline": round(baseline, 1),
                        "level": "TRENDING",
                        "niche_id": niche_id,
                        "detected_at": datetime.now(UTC).isoformat(),
                    }
                    new_alerts.append(alert)

                    # Flag trending posts in content memory too
                    try:
                        mem_hash = _make_hash(text)
                        _flag_viral(mem_hash, multiplier, platform)
                    except Exception as e:
                        logger.debug("Could not flag trending in memory: %s", e)

                    _send_notification(
                        f"Trending on {platform.upper()}",
                        f"{text[:60]}... — {likes} likes ({multiplier:.0f}x normal)",
                    )

        except Exception as e:
            logger.warning("Failed to check %s: %s", platform, e)

    # Save new alerts
    if new_alerts:
        alerts.extend(new_alerts)
        _save_alert_log(alerts)

    return new_alerts


def print_alerts(alerts: list[dict]) -> None:
    """Print human-readable alert summary."""
    print(f"\n{'=' * 60}")
    print("  Gen Lab — Viral Detection Report")
    print(f"  {datetime.now(UTC).isoformat()[:19]}Z")
    print(f"{'=' * 60}\n")

    if not alerts:
        print("  No new viral or trending posts detected.")
        print("  (Baselines updated for next check)")
    else:
        for a in sorted(alerts, key=lambda x: x["multiplier"], reverse=True):
            print(f"  {a['level']}  [{a['platform'].upper()}]")
            print(f"    {a['text']}")
            print(f"    {a['likes']} likes — {a['multiplier']}x baseline ({a['baseline']} avg)")
            print()

    # Show current baselines
    baselines = _load_baselines()
    if baselines:
        print(f"  {'─' * 50}")
        print("  Current baselines (median engagement):")
        for p, b in baselines.items():
            print(f"    {p:12s} {b:>8.0f}")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Viral Detection & Alert System")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_VIRAL_THRESHOLD,
        help=f"Viral threshold multiplier (default: {DEFAULT_VIRAL_THRESHOLD}x)",
    )
    parser.add_argument("--baseline", action="store_true", help="Recompute baselines only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--days", type=int, default=7, help="Lookback for detection")
    parser.add_argument(
        "--niche", type=str, default=None, help="Niche ID (e.g. gaming, ai_creators)"
    )
    args = parser.parse_args()

    if args.baseline:
        baselines = compute_baselines(30, niche_id=args.niche)
        print(json.dumps(baselines, indent=2) if args.json else f"Baselines: {baselines}")
    else:
        alerts = detect_viral(threshold=args.threshold, days=args.days, niche_id=args.niche)
        if args.json:
            print(json.dumps(alerts, indent=2, default=str))
        else:
            print_alerts(alerts)
