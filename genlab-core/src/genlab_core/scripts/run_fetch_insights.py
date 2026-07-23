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
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genlab_core.http.backlog_client import BacklogClient
from genlab_core.pipeline.stages.fetch_insights import normalize_publishing_metrics
from genlab_core.platforms.metrics.legacy_aliases import add_legacy_aliases

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
    6: (4.0, 8760.0),  # Any post 4h+ old (effectively unlimited for backfill)
    24: (20.0, 8760.0),  # Any post 20h+ old
    48: (44.0, 8760.0),  # Any post 44h+ old (growth tracking)
    168: (164.0, 8760.0),  # Any post 164h+ old (final weekly snapshot)
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
        len(eligible),
        window,
        niche_id,
        len(records),
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
        elif platform == "threads":
            return _fetch_threads(raw_id, niche_id=niche_id)
        elif platform in ("x", "twitter", "x_twitter"):
            return _fetch_twitter(raw_id)
        else:
            logger.debug("No fetcher for platform: %s", platform)
            return None
    except Exception:
        logger.exception("Platform fetch failed: %s/%s", platform, raw_id)
        return None


def _resolve_ig_media_id(shortcode_or_id: str, token: str, ig_user_id: str) -> str | None:
    """Thin shim to the canonical IG media-id resolver.

    Kept so any existing in-script reference still works after the canonical
    implementation moved to :mod:`genlab_core.platforms.metrics.instagram`.
    """
    from genlab_core.platforms.metrics.instagram import _resolve_media_id

    return _resolve_media_id(shortcode_or_id, token=token, ig_user_id=ig_user_id)


def _fetch_instagram(post_id: str, niche_id: str = "") -> dict[str, Any] | None:
    """Fetch IG metrics — canonical fetch + legacy aliases via the shared
    :func:`add_legacy_aliases` helper. IG adds ``saved`` and
    ``watch_time_minutes`` aliases for SharePoint-column compatibility."""
    from genlab_core.platforms.metrics import fetch_instagram as _canonical

    return add_legacy_aliases(_canonical(post_id, niche_id=niche_id), "instagram")


def _fetch_youtube(post_id: str) -> dict[str, Any] | None:
    """Fetch YT metrics — thin delegate to the canonical implementation.
    YouTube has no legacy aliases; the canonical shape ships unchanged."""
    from genlab_core.platforms.metrics import fetch_youtube as _canonical

    return add_legacy_aliases(_canonical(post_id), "youtube")


def _fetch_facebook(post_id: str, niche_id: str = "") -> dict[str, Any] | None:
    """Fetch FB metrics — thin delegate to the canonical (Reels-specific)
    implementation. Facebook has no legacy aliases on the script path."""
    from genlab_core.platforms.metrics import fetch_facebook as _canonical

    return add_legacy_aliases(_canonical(post_id, niche_id=niche_id), "facebook")


def _fetch_threads(post_id: str, niche_id: str = "") -> dict[str, Any] | None:
    """Fetch Threads metrics — thin delegate to the canonical implementation.

    2026-07-22 wire-gap fix: prior to this, Threads was NOT wired into
    `_fetch_platform_insights` at all — every Threads SUCCESS row stayed
    at SUCCESS forever, denying the learning loop 40% of publish signal.
    """
    from genlab_core.platforms.metrics import fetch_threads as _canonical

    return add_legacy_aliases(_canonical(post_id, niche_id=niche_id), "threads")


def _fetch_twitter(post_id: str) -> dict[str, Any] | None:
    """Fetch X metrics — canonical fetch + legacy aliases. X adds
    ``retweets``/``replies`` (alias of ``shares``/``comments``) for the
    in-script ``_write_back_to_blueprint`` and any launchd-log readers
    that grep the older keys."""
    from genlab_core.platforms.metrics import fetch_twitter as _canonical

    return add_legacy_aliases(_canonical(post_id), "twitter")


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
        blueprint_record_id,
        platform,
        list(fields.keys()),
    )


def _mark_window_completed(
    client: Any,
    record_id: str,
    existing_windows: str,
    window: int,
    metrics: dict[str, Any] | None = None,
) -> None:
    """Mark a window as completed on the Publishing_Analytics record.

    Uses status field (SUCCESS → INSIGHTS_6H → INSIGHTS_24H) since
    insight_windows_completed column doesn't exist in SharePoint.

    When ``metrics`` is provided, also persists the raw per-platform
    views/likes/comments/shares/saves into publishing_analytics
    (normalised across platforms). Without this, the raw columns on
    publishing_analytics stay at 0 forever — PR #54's Gap-2 fix lived
    only in the pipeline-stage path; production uses THIS script via
    the insights-collector systemd timer, so the same write must
    happen here.
    """
    new_status = f"INSIGHTS_{window}H"
    update_fields: dict[str, Any] = {"status": new_status}
    if metrics:
        update_fields.update(normalize_publishing_metrics(metrics))
    # 2026-07-23: stamp when metrics were last fetched. Before this,
    # ``publishing_analytics.metrics_fetched`` was NEVER populated across
    # 406+ rows (all NULL). The column existed in the schema + on the
    # analytics_store sibling table, but the insights-collector timer
    # (this code path) never wrote it. Without a fetch timestamp,
    # freshness of the views/likes/... columns was invisible — operators
    # couldn't tell whether a "0 views" row was stale (never re-fetched)
    # or genuine (post got 0 engagement). Class-of-bug: schema-code
    # drift — column added but write path never wired (sibling of
    # 2026-07-23 action_taken_source fix in blueprints).
    update_fields["metrics_fetched"] = datetime.now(UTC).isoformat()
    try:
        client.publishing_analytics.update(
            record_id,
            update_fields,
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
        for r, _reason in eligible:
            f = r.get("fields", {})
            age = _post_age_hours(f.get("published_at", ""))
            logger.info(
                "  [DRY RUN] Would fetch: %s/%s (post_id=%s, age=%.0fh)",
                f.get("platform"),
                f.get("candidate_id", "?"),
                f.get("post_id", "?"),
                age or 0,
            )
        return stats

    for r, _reason in eligible:
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

            # Mark window completed + persist raw metrics (the Gap-2 fix —
            # PR #54 covered the pipeline-stage path; prod uses this script).
            existing_windows = f.get("insight_windows_completed", "")
            _mark_window_completed(client, r["id"], existing_windows, window, metrics=insights)

            # Write key metrics back to blueprint for dashboard display
            if blueprint_record_id:
                try:
                    _write_back_to_blueprint(
                        client,
                        blueprint_record_id,
                        platform,
                        insights,
                        window,
                    )
                except Exception as wb_exc:
                    logger.warning(
                        "Blueprint write-back failed for %s (non-fatal): %s",
                        blueprint_record_id,
                        wb_exc,
                    )

            logger.info(
                "Fetched %s/%s: engagement=%s (age=%.0fh, window=%sh)",
                platform,
                post_id[:15],
                insights.get("engagement", "?"),
                age_hours or 0,
                window,
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
        niche_id,
        window,
        stats["eligible"],
        stats["fetched"],
        stats["errors"],
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

        # Get recently published stories with engagement data.
        #
        # 2026-06-15: match ALL post-publish lifecycle states, not just
        # SUCCESS. The metric collector progressively flips
        # publishing_analytics.status from SUCCESS → INSIGHTS_6H →
        # INSIGHTS_24H → INSIGHTS_48H → INSIGHTS_168H as each metric
        # collection window fires (verified prod: a row published 06:48
        # UTC has status=INSIGHTS_6H by ~11:49 UTC, ~5h later — well
        # within the 0-48h age window this function targets).
        #
        # The previous ``{status}='SUCCESS'`` filter only matched posts
        # in their first ~5 hours after publish, missing the bulk of
        # the 5-48h engagement-data window the learner is supposed to
        # mine. PerformanceLearner therefore saw a fraction of available
        # signal on every fire — the learning loop was effectively dark
        # for most posts. Same bug pattern as PR #220's daily_cap
        # cap-loader fix.
        _status_or = (
            "OR("
            "{status}='SUCCESS',"
            "{status}='INSIGHTS_6H',"
            "{status}='INSIGHTS_24H',"
            "{status}='INSIGHTS_48H',"
            "{status}='INSIGHTS_168H'"
            ")"
        )
        if niche_id != "all":
            formula = f"AND({_status_or},{{niche_id}}='{niche_id}')"
        else:
            formula = _status_or

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
        "--niche-id",
        required=True,
        choices=ALL_NICHE_IDS + ["all"],
        help="Niche to fetch insights for, or 'all' for all niches",
    )
    parser.add_argument(
        "--window",
        required=True,
        type=int,
        choices=list(WINDOW_RANGES.keys()),
        help="Time window in hours (6 or 24)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be fetched without calling APIs or writing data",
    )
    args = parser.parse_args()

    logger.info(
        "Starting fetch_insights: niche=%s window=%sh dry_run=%s",
        args.niche_id,
        args.window,
        args.dry_run,
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
