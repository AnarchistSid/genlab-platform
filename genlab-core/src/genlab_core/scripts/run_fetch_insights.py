#!/usr/bin/env python3
"""Deferred engagement insights fetcher — standalone script for launchd.

Fetches post-publish engagement metrics at configurable time windows
(6h, 24h, 48h, 168h) for any niche. Designed to be called by launchd plists
after the daily publish completes.

Usage:
    uv run python -m genlab_core.scripts.run_fetch_insights --niche-id anime --window 6
    uv run python -m genlab_core.scripts.run_fetch_insights --niche-id gaming --window 24
    uv run python -m genlab_core.scripts.run_fetch_insights --niche-id gaming --window 48
    uv run python -m genlab_core.scripts.run_fetch_insights --niche-id gaming --window 168
    uv run python -m genlab_core.scripts.run_fetch_insights --niche-id all --window 6
    uv run python -m genlab_core.scripts.run_fetch_insights --niche-id anime --window 6 --dry-run

Windows:
    6h:   Fetch posts published 5-7h ago (first engagement snapshot)
    24h:  Fetch posts published 23-25h ago + trigger performance_learner
    48h:  Fetch posts published 44-168h ago (growth tracking + bandit reward)
    168h: Fetch posts published 164-336h ago (final weekly snapshot)
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genlab_core.http.backlog_client import BacklogClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("genlab.fetch_insights")

# Niche-to-env mapping — each channel's .env has platform credentials
NICHE_ENV_DIRS: dict[str, str] = {
    "ai_creators": "BlackboxBrief",
    "gaming": "CriticalRush",
    "sports": "ClutchWire",
    "movies": "SpliceReel",
    "anime": "FrameDrift",
}

ALL_NICHE_IDS = list(NICHE_ENV_DIRS.keys())

# Window definitions: (min_age_hours, max_age_hours)
# Wide ranges: catch ALL posts that haven't been collected yet.
# Idempotency via insight_windows_completed prevents double-fetching.
WINDOW_RANGES: dict[int, tuple[float, float]] = {
    6: (4.0, 8760.0),     # Any post 4h+ old (effectively unlimited for backfill)
    24: (20.0, 8760.0),   # Any post 20h+ old
    48: (44.0, 8760.0),   # Any post 44h+ old (growth tracking)
    168: (164.0, 8760.0), # Any post 164h+ old (final weekly snapshot)
}


def _load_env_for_niche(niche_id: str) -> None:
    """Load the .env file for a given niche."""
    dir_name = NICHE_ENV_DIRS.get(niche_id)
    if not dir_name:
        return
    env_path = Path(__file__).resolve().parents[4] / dir_name / ".env"
    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path, override=True)
            logger.debug("Loaded env from %s", env_path)
        except ImportError:
            logger.warning("python-dotenv not installed — env not loaded")


def _post_age_hours(published_at: Any) -> float | None:
    """Calculate post age in hours from ISO timestamp."""
    if not published_at:
        return None
    try:
        if isinstance(published_at, datetime):
            pub_dt = published_at if published_at.tzinfo else published_at.replace(tzinfo=UTC)
        else:
            pub_dt = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        delta = datetime.now(UTC) - pub_dt
        return delta.total_seconds() / 3600
    except (ValueError, TypeError, AttributeError):
        return None


def _get_eligible_records(
    client: Any,
    niche_id: str,
    window: int,
) -> list[tuple[dict, str]]:
    """Query Publishing_Analytics for records eligible for insight fetch.

    Returns list of (record, reason) tuples.
    """
    min_age, max_age = WINDOW_RANGES[window]

    # Status progression: SUCCESS → INSIGHTS_6H → INSIGHTS_24H → INSIGHTS_48H → INSIGHTS_168H
    # Each window targets the status from the previous window.
    window_target_status = {
        6: "SUCCESS",
        24: "INSIGHTS_6H",
        48: "INSIGHTS_24H",
        168: "INSIGHTS_48H",
    }
    target_status = window_target_status.get(window, "SUCCESS")

    try:
        formula = f"{{status}}='{target_status}'"
        if niche_id != "all":
            formula = f"AND({{status}}='{target_status}',{{niche_id}}='{niche_id}')"
        records = client.publishing_analytics.all(
            formula=formula,
            max_records=200,
        )
    except Exception as exc:
        logger.warning("Failed to query Publishing_Analytics: %s", exc)
        return []

    eligible = []
    for r in records:
        f = r.get("fields", {})
        published_at = f.get("published_at", "")
        age = _post_age_hours(published_at)
        if age is None:
            continue
        if not (min_age <= age <= max_age):
            continue
        post_id = f.get("post_id", "")
        if not post_id:
            continue

        # Idempotency: check if already fetched at this window
        # Status progression: SUCCESS → INSIGHTS_6H → INSIGHTS_24H → INSIGHTS_48H → INSIGHTS_168H
        record_status = str(f.get("status", ""))
        window_tag = f"{window}H"
        if window_tag in record_status:
            continue  # Already collected at this window

        eligible.append((r, f"window_{window}h"))

    logger.info(
        "Found %d eligible records for %sh window (niche=%s, total queried=%d)",
        len(eligible), window, niche_id, len(records),
    )
    return eligible


def _strip_platform_prefix(post_id: str) -> str:
    """Strip the 'platform:' prefix from a post ID.

    Publishing stores IDs as 'instagram:DWigzIKDeR5' but platform APIs
    need the raw ID 'DWigzIKDeR5'.
    """
    if ":" in post_id:
        return post_id.split(":", 1)[1]
    return post_id


def _fetch_platform_insights(
    platform: str,
    post_id: str,
    niche_id: str = "",
) -> dict[str, Any] | None:
    """Fetch engagement metrics for a single post from its platform API.

    Uses per-niche credentials via niche_credentials to avoid cross-channel
    token leakage.
    """
    # Strip 'platform:' prefix — DB stores 'instagram:ABC' but APIs need 'ABC'
    raw_id = _strip_platform_prefix(post_id)
    if not raw_id:
        return None

    try:
        if platform == "instagram":
            return _fetch_instagram(raw_id, niche_id=niche_id)
        elif platform == "youtube":
            return _fetch_youtube(raw_id)
        elif platform == "facebook":
            return _fetch_facebook(raw_id, niche_id=niche_id)
        elif platform in ("x", "twitter", "x_twitter"):
            return _fetch_twitter(raw_id)
        else:
            logger.debug("No fetcher for platform: %s", platform)
            return None
    except Exception:
        logger.exception("Platform fetch failed: %s/%s", platform, raw_id)
        return None


def _resolve_ig_media_id(shortcode_or_id: str, token: str, ig_user_id: str) -> str | None:
    """Convert an IG shortcode to a numeric media ID via the user's /media endpoint.

    The publisher stores shortcodes (e.g. 'DWif8mKEjst') but the Graph API
    insights endpoints require numeric media IDs (e.g. '18054219725706846').
    If the input is already numeric, returns it as-is.
    """
    if shortcode_or_id.isdigit():
        return shortcode_or_id

    if not ig_user_id:
        return None

    import requests as _req
    # Search recent media for the matching shortcode
    resp = _req.get(
        f"https://graph.facebook.com/v21.0/{ig_user_id}/media",
        params={
            "fields": "id,shortcode",
            "limit": 50,
            "access_token": token,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        return None

    for item in resp.json().get("data", []):
        if item.get("shortcode") == shortcode_or_id:
            return item["id"]

    logger.debug("IG shortcode '%s' not found in recent media", shortcode_or_id)
    return None


def _fetch_instagram(post_id: str, niche_id: str = "") -> dict[str, Any] | None:
    """Fetch IG metrics via graph.facebook.com using per-niche credentials."""
    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    meta_creds = resolve_meta_credentials(niche_id)
    token = meta_creds.get("ig_access_token", "")
    ig_user_id = meta_creds.get("ig_user_id", "")
    if not token:
        logger.warning(
            "[fetch_insights] IG token not set for niche '%s' — "
            "analytics data for this platform will be missing",
            niche_id,
        )
        return None

    # Resolve shortcode to numeric media ID if needed
    media_id = _resolve_ig_media_id(post_id, token, ig_user_id)
    if not media_id:
        logger.debug("Could not resolve IG media ID for '%s'", post_id)
        return None

    import requests
    api_base = "https://graph.facebook.com/v21.0"

    # Basic metrics
    r = requests.get(
        f"{api_base}/{media_id}",
        params={"fields": "like_count,comments_count,media_type", "access_token": token},
        timeout=15,
    )
    if r.status_code != 200:
        logger.warning("IG basic %d: %s", r.status_code, r.text[:200])
        return None
    data = r.json()

    # Reels insights — v22.0+ deprecated plays; use full metric set
    insights_resp = requests.get(
        f"{api_base}/{media_id}/insights",
        params={
            "metric": "reach,saved,shares,likes,comments,total_interactions,ig_reels_video_view_total_time",
            "access_token": token,
        },
        timeout=15,
    )
    insights = {}
    if insights_resp.status_code == 200:
        for item in insights_resp.json().get("data", []):
            name = item.get("name", "")
            values = item.get("values", [{}])
            insights[name] = values[0].get("value", 0) if values else 0

    watch_time_ms = insights.get("ig_reels_video_view_total_time", 0)
    watch_time_min = round(watch_time_ms / 60000, 1) if watch_time_ms else 0

    return {
        "likes": insights.get("likes", data.get("like_count", 0)),
        "comments": insights.get("comments", data.get("comments_count", 0)),
        "reach": insights.get("reach", 0),
        "saved": insights.get("saved", 0),
        "shares": insights.get("shares", 0),
        "engagement": insights.get("total_interactions", 0),
        "watch_time_minutes": watch_time_min,
        "impressions": insights.get("reach", 0),
    }


def _fetch_youtube(post_id: str) -> dict[str, Any] | None:
    """Fetch YT metrics via Data API v3."""
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        logger.warning(
            "[fetch_insights] YOUTUBE_API_KEY not set — "
            "YouTube analytics data will be missing"
        )
        return None

    import requests
    resp = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"part": "statistics", "id": post_id, "key": api_key},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    items = resp.json().get("items", [])
    if not items:
        return None
    stats = items[0].get("statistics", {})
    views = int(stats.get("viewCount", 0))
    likes = int(stats.get("likeCount", 0))
    comments = int(stats.get("commentCount", 0))
    return {
        "views": views,
        "likes": likes,
        "comments": comments,
        "reach": views,
        "engagement": likes + comments,
    }


def _fetch_facebook(post_id: str, niche_id: str = "") -> dict[str, Any] | None:
    """Fetch FB metrics via Graph API using per-niche credentials."""
    from genlab_core.publishing.niche_credentials import resolve_fb_credentials

    token, _page_id = resolve_fb_credentials(niche_id)
    if not token:
        logger.warning(
            "[fetch_insights] FB token not set for niche '%s' — "
            "Facebook analytics data for this platform will be missing",
            niche_id,
        )
        return None

    import requests

    # FB Reels use /video_insights instead of /insights (which returns 400)
    resp = requests.get(
        f"https://graph.facebook.com/v21.0/{post_id}/video_insights",
        params={"access_token": token},
        timeout=15,
    )
    if resp.status_code != 200:
        logger.debug("FB video_insights %d: %s", resp.status_code, resp.text[:100])
        return None

    metrics: dict[str, Any] = {}
    likes = 0
    for item in resp.json().get("data", []):
        name = item.get("name", "")
        values = item.get("values", [{}])
        val = values[0].get("value", 0) if values else 0
        if name == "post_video_likes_by_reaction_type" and isinstance(val, dict):
            likes = sum(val.values())
        elif name == "post_video_views":
            metrics["views"] = val
        elif name == "post_video_view_time":
            metrics["watch_time_ms"] = val

    return {
        "likes": likes,
        "views": metrics.get("views", 0),
        "watch_time_ms": metrics.get("watch_time_ms", 0),
        "engagement": likes + metrics.get("views", 0),
        "reach": metrics.get("views", 0),
    }


def _fetch_twitter(post_id: str) -> dict[str, Any] | None:
    """Fetch X metrics via API v2."""
    bearer = os.getenv("X_BEARER_TOKEN", "")
    if not bearer:
        logger.warning(
            "[fetch_insights] X_BEARER_TOKEN not set — "
            "X/Twitter analytics data will be missing"
        )
        return None

    import requests
    resp = requests.get(
        f"https://api.twitter.com/2/tweets/{post_id}",
        params={"tweet.fields": "public_metrics"},
        headers={"Authorization": f"Bearer {bearer}"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    metrics = resp.json().get("data", {}).get("public_metrics", {})
    likes = metrics.get("like_count", 0)
    retweets = metrics.get("retweet_count", 0)
    replies = metrics.get("reply_count", 0)
    return {
        "likes": likes,
        "retweets": retweets,
        "replies": replies,
        "shares": retweets,
        "impressions": metrics.get("impression_count", 0),
        "engagement": likes + retweets + replies,
        "reach": metrics.get("impression_count", 0),
    }


def _write_back_to_blueprint(
    client: Any,
    blueprint_record_id: str,
    platform: str,
    insights: dict[str, Any],
    window: int,
) -> None:
    """Write key engagement metrics back to the blueprint record.

    Non-fatal: caller wraps in try/except. Analytics upsert has already
    succeeded at this point — a blueprint write-back failure is logged
    but never fails the whole insights run.
    """
    fields: dict[str, Any] = {}

    if platform == "instagram":
        fields["ig_reach"] = insights.get("reach", 0)
        fields["ig_likes"] = insights.get("likes", 0)
        fields["ig_comments"] = insights.get("comments", 0)
    elif platform == "youtube":
        fields["yt_views"] = insights.get("views", 0)
        fields["yt_likes"] = insights.get("likes", 0)
        fields["yt_comments"] = insights.get("comments", 0)

    # Compute engagement_rate
    ig_reach = fields.get("ig_reach", 0) or 0
    ig_likes = fields.get("ig_likes", 0) or 0
    ig_comments = fields.get("ig_comments", 0) or 0
    yt_views = fields.get("yt_views", 0) or 0
    yt_likes = fields.get("yt_likes", 0) or 0
    yt_comments = fields.get("yt_comments", 0) or 0

    if ig_reach > 0:
        fields["engagement_rate"] = round((ig_likes + ig_comments) / ig_reach, 4)
    elif yt_views > 0:
        fields["engagement_rate"] = round((yt_likes + yt_comments) / yt_views, 4)

    # Remove None/empty values
    fields = {k: v for k, v in fields.items() if v is not None}

    if not fields:
        return

    # Write fields one at a time — skip any that don't exist in SharePoint
    for field_name, field_value in fields.items():
        try:
            client.blueprints.update(blueprint_record_id, {field_name: field_value}, typecast=True)
        except Exception:
            logger.debug("Blueprint field '%s' not in schema — skipping", field_name)
    logger.info(
        "Blueprint %s write-back: platform=%s fields=%s",
        blueprint_record_id, platform, list(fields.keys()),
    )


def _mark_window_completed(
    client: Any,
    record_id: str,
    existing_windows: str,
    window: int,
) -> None:
    """Mark a window as completed on the Publishing_Analytics record.

    Uses status field (SUCCESS → INSIGHTS_6H → INSIGHTS_24H) since
    insight_windows_completed column doesn't exist in SharePoint.
    """
    new_status = f"INSIGHTS_{window}H"
    try:
        client.publishing_analytics.update(
            record_id,
            {"status": new_status},
            typecast=True,
        )
    except Exception as exc:
        logger.warning("Failed to mark window %sh completed: %s", window, exc)


def fetch_insights_for_window(
    niche_id: str,
    window: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Main entry: fetch insights for a niche at a given time window.

    Returns summary stats dict.
    """
    _load_env_for_niche(niche_id if niche_id != "all" else "ai_creators")
    client = BacklogClient()

    eligible = _get_eligible_records(client, niche_id, window)

    stats = {
        "niche_id": niche_id,
        "window": window,
        "eligible": len(eligible),
        "fetched": 0,
        "errors": 0,
        "skipped": 0,
        "dry_run": dry_run,
        "by_platform": {},
    }

    if dry_run:
        for r, reason in eligible:
            f = r.get("fields", {})
            age = _post_age_hours(f.get("published_at", ""))
            logger.info(
                "  [DRY RUN] Would fetch: %s/%s (post_id=%s, age=%.0fh)",
                f.get("platform"), f.get("candidate_id", "?"),
                f.get("post_id", "?"), age or 0,
            )
        return stats

    for r, reason in eligible:
        f = r.get("fields", {})
        post_id = f.get("post_id", "")
        platform = f.get("platform", "")
        candidate_id = f.get("candidate_id", "")

        p_stats = stats["by_platform"].setdefault(platform, {"fetched": 0, "errors": 0})

        # Load niche-specific env if processing "all"
        record_niche = f.get("niche_id", "")
        if niche_id == "all" and record_niche:
            _load_env_for_niche(record_niche)

        insights = _fetch_platform_insights(platform, post_id, niche_id=record_niche or niche_id)
        if not insights:
            p_stats["errors"] += 1
            stats["errors"] += 1
            continue

        # Write to Analytics table
        try:
            published_at = f.get("published_at", "")
            age_hours = _post_age_hours(published_at)
            fetch_window = f"{window}h"

            # Get blueprint link if available
            bp_link = f.get("blueprint", [])
            blueprint_record_id = ""
            if bp_link and isinstance(bp_link, list):
                blueprint_record_id = bp_link[0]
            elif isinstance(bp_link, str):
                blueprint_record_id = bp_link

            client.upsert_analytics(
                post_id=post_id,
                platform=platform,
                insights=insights,
                blueprint_record_id=blueprint_record_id,
                candidate_id=candidate_id,
                published_at=published_at or "",
                fetch_window=fetch_window,
                niche_id=record_niche or niche_id,
            )
            p_stats["fetched"] += 1
            stats["fetched"] += 1

            # Mark window completed
            existing_windows = f.get("insight_windows_completed", "")
            _mark_window_completed(client, r["id"], existing_windows, window)

            # Write key metrics back to blueprint for dashboard display
            if blueprint_record_id:
                try:
                    _write_back_to_blueprint(
                        client, blueprint_record_id, platform, insights, window,
                    )
                except Exception as wb_exc:
                    logger.warning(
                        "Blueprint write-back failed for %s (non-fatal): %s",
                        blueprint_record_id, wb_exc,
                    )

            logger.info(
                "Fetched %s/%s: engagement=%s (age=%.0fh, window=%sh)",
                platform, post_id[:15],
                insights.get("engagement", "?"),
                age_hours or 0, window,
            )
        except Exception as exc:
            logger.warning("Failed to write analytics for %s/%s: %s", platform, post_id, exc)
            p_stats["errors"] += 1
            stats["errors"] += 1

        time.sleep(0.5)

    # Summary
    logger.info("=" * 50)
    logger.info(
        "INSIGHTS SUMMARY: niche=%s window=%sh | eligible=%d fetched=%d errors=%d",
        niche_id, window, stats["eligible"], stats["fetched"], stats["errors"],
    )
    for p, ps in stats["by_platform"].items():
        logger.info("  %s: %d fetched, %d errors", p, ps["fetched"], ps["errors"])
    logger.info("=" * 50)

    return stats


def _trigger_performance_learner(niche_id: str) -> None:
    """Trigger PerformanceLearner for a niche after 24h insights."""
    logger.info("Triggering performance_learner for niche=%s", niche_id)
    try:
        from genlab_core.pipeline.stages.performance_learner import PerformanceLearner

        client = BacklogClient()

        # Get recently published stories with engagement data
        formula = "{status}='SUCCESS'"
        if niche_id != "all":
            formula = f"AND({{status}}='SUCCESS',{{niche_id}}='{niche_id}')"

        records = client.publishing_analytics.all(
            formula=formula,
            max_records=50,
        )

        stories = []
        for r in records:
            f = r.get("fields", {})
            age = _post_age_hours(f.get("published_at", ""))
            if age is None or age > 48:
                continue
            # Build a minimal story dict for PerformanceLearner
            story = {
                "story_id": f.get("candidate_id", ""),
                "hook_formula": f.get("hook_formula", ""),
                "template_id": f.get("template_id", ""),
                "scheduled_slot": f.get("scheduled_slot", ""),
                "engagement": {
                    f.get("platform", "unknown"): {
                        "metrics": {
                            "engagement": f.get("engagement", 0),
                            "reach": f.get("reach", 0),
                        },
                    },
                },
            }
            stories.append(story)

        if not stories:
            logger.info("No stories with engagement data for learner update")
            return

        niche_config = {"niche_id": niche_id if niche_id != "all" else "gaming"}
        context = {
            "stories": stories,
            "niche_config": niche_config,
            "run_stats": {},
        }

        learner = PerformanceLearner()
        result = learner.execute(context)
        learning_stats = result.get("run_stats", {}).get("learning", {})
        logger.info("PerformanceLearner: %s", learning_stats)
    except Exception:
        logger.exception("PerformanceLearner failed (non-fatal)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch post-publish engagement insights for a niche at a time window."
    )
    parser.add_argument(
        "--niche-id", required=True,
        choices=ALL_NICHE_IDS + ["all"],
        help="Niche to fetch insights for, or 'all' for all niches",
    )
    parser.add_argument(
        "--window", required=True, type=int,
        choices=list(WINDOW_RANGES.keys()),
        help="Time window in hours (6 or 24)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be fetched without calling APIs or writing data",
    )
    args = parser.parse_args()

    logger.info(
        "Starting fetch_insights: niche=%s window=%sh dry_run=%s",
        args.niche_id, args.window, args.dry_run,
    )

    if args.niche_id == "all":
        all_stats = []
        for nid in ALL_NICHE_IDS:
            _load_env_for_niche(nid)
            stats = fetch_insights_for_window(nid, args.window, args.dry_run)
            all_stats.append(stats)
        total_fetched = sum(s["fetched"] for s in all_stats)
        total_errors = sum(s["errors"] for s in all_stats)
    else:
        stats = fetch_insights_for_window(args.niche_id, args.window, args.dry_run)
        total_fetched = stats["fetched"]
        total_errors = stats["errors"]

    # 24h window: trigger performance_learner
    if args.window == 24 and not args.dry_run and total_fetched > 0:
        if args.niche_id == "all":
            for nid in ALL_NICHE_IDS:
                _load_env_for_niche(nid)
                _trigger_performance_learner(nid)
        else:
            _trigger_performance_learner(args.niche_id)

    logger.info("Done: %d fetched, %d errors", total_fetched, total_errors)


if __name__ == "__main__":
    main()
