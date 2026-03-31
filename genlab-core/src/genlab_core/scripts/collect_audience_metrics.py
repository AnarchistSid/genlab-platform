#!/usr/bin/env python3
"""Daily audience metrics collector — follower/subscriber counts across all platforms.

Snapshots follower counts from Instagram, YouTube, Facebook, and Threads
for all 5 niches into the `audience_snapshots` table. Also updates
`monetisationprogress` with current values and 7-day deltas.

Designed to run once daily via launchd (after pipeline + publisher).

Usage:
    uv run python -m genlab_core.scripts.collect_audience_metrics
    uv run python -m genlab_core.scripts.collect_audience_metrics --dry-run
"""
from __future__ import annotations

import argparse
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("genlab.audience")

NICHE_ENVS: dict[str, tuple[str, str]] = {
    "ai_creators": ("BlackboxBrief", ""),
    "gaming": ("CriticalRush", "CRITICALRUSH"),
    "sports": ("ClutchWire", "CLUTCHWIRE"),
    "movies": ("SpliceReel", "SPLICEREEL"),
    "anime": ("FrameDrift", "FRAMEDRIFT"),
}

GENLAB_ROOT = Path(__file__).resolve().parents[4]

# Monetisation thresholds (platform requirements for monetisation)
MONETISATION_TARGETS: dict[str, dict[str, float]] = {
    "instagram": {"followers": 10_000},
    "youtube": {"subscribers": 1_000, "watch_hours": 4_000},
    "facebook": {"followers": 10_000},
    "threads": {"followers": 500},
}


def _load_env(niche_id: str) -> None:
    """Load root .env + niche-specific .env."""
    try:
        from dotenv import load_dotenv
        load_dotenv(GENLAB_ROOT / ".env", override=False)
        dir_name = NICHE_ENVS[niche_id][0]
        niche_env = GENLAB_ROOT / dir_name / ".env"
        if niche_env.exists():
            load_dotenv(niche_env, override=True)
    except ImportError:
        pass


def _env(prefix: str, key: str) -> str:
    """Get env var with niche prefix fallback."""
    if prefix:
        val = os.environ.get(f"{prefix}_{key}", "")
        if val:
            return val
    return os.environ.get(key, "")


def fetch_instagram(prefix: str) -> dict[str, Any]:
    """Fetch IG followers, media_count."""
    token = _env(prefix, "META_ACCESS_TOKEN")
    user_id = _env(prefix, "IG_USER_ID") or os.environ.get("META_IG_USER_ID", "")
    if not token or not user_id:
        return {}
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{user_id}",
            params={"fields": "followers_count,media_count,username", "access_token": token},
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning("IG %d: %s", r.status_code, r.text[:100])
            return {}
        d = r.json()
        return {
            "followers": d.get("followers_count", 0),
            "media_count": d.get("media_count", 0),
            "username": d.get("username", ""),
        }
    except Exception as e:
        logger.warning("IG fetch failed: %s", e)
        return {}


def fetch_youtube(prefix: str) -> dict[str, Any]:
    """Fetch YT subscribers, total views, video count."""
    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    channel_id = _env(prefix, "YT_CHANNEL_ID")
    if not api_key or not channel_id:
        return {}
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "statistics", "id": channel_id, "key": api_key},
            timeout=15,
        )
        if r.status_code != 200 or not r.json().get("items"):
            return {}
        s = r.json()["items"][0]["statistics"]
        return {
            "subscribers": int(s.get("subscriberCount", 0)),
            "total_views": int(s.get("viewCount", 0)),
            "video_count": int(s.get("videoCount", 0)),
        }
    except Exception as e:
        logger.warning("YT fetch failed: %s", e)
        return {}


def fetch_facebook(prefix: str) -> dict[str, Any]:
    """Fetch FB page followers/fans."""
    token = _env(prefix, "FB_PAGE_ACCESS_TOKEN")
    page_id = _env(prefix, "FB_PAGE_ID") or os.environ.get("META_FB_PAGE_ID", "")
    if not token or not page_id:
        return {}
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{page_id}",
            params={"fields": "followers_count,fan_count,name", "access_token": token},
            timeout=15,
        )
        if r.status_code != 200:
            return {}
        d = r.json()
        return {
            "followers": d.get("followers_count", 0),
            "fans": d.get("fan_count", 0),
        }
    except Exception as e:
        logger.warning("FB fetch failed: %s", e)
        return {}


def fetch_threads(prefix: str) -> dict[str, Any]:
    """Fetch Threads follower count (if API supports it)."""
    token = _env(prefix, "THREADS_ACCESS_TOKEN")
    user_id = _env(prefix, "THREADS_USER_ID")
    if not token or not user_id:
        return {}
    try:
        r = requests.get(
            f"https://graph.threads.net/v1.0/{user_id}",
            params={"fields": "username,threads_biography", "access_token": token},
            timeout=15,
        )
        if r.status_code != 200:
            return {}
        # Threads API doesn't expose follower_count yet — return username only
        return {"username": r.json().get("username", "")}
    except Exception as e:
        logger.warning("Threads fetch failed: %s", e)
        return {}


def save_snapshot(conn, niche_id: str, platform: str, metrics: dict[str, Any], today: date) -> None:
    """Insert one row per metric into audience_snapshots."""
    conn.execute("SELECT set_config('app.niche_id', %s, true)", (niche_id,))
    for metric_name, value in metrics.items():
        if isinstance(value, str):
            continue  # Skip non-numeric like username
        conn.execute(
            """INSERT INTO audience_snapshots (id, niche_id, platform, metric_name, metric_value, snapshot_date)
               VALUES (gen_random_uuid(), %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (niche_id, platform, metric_name, value, today),
        )


def update_monetisation_progress(conn, niche_id: str, platform: str, metric_name: str,
                                  current: float, today: date) -> None:
    """Upsert monetisationprogress with current value, 7d delta, days-to-threshold estimate."""
    conn.execute("SELECT set_config('app.niche_id', %s, true)", (niche_id,))
    target = MONETISATION_TARGETS.get(platform, {}).get(metric_name)
    if target is None:
        return

    pct = round(current / target, 4) if target > 0 else 0
    is_met = current >= target

    # Get value from 7 days ago for delta
    week_ago = today - timedelta(days=7)
    prev_row = conn.execute(
        """SELECT metric_value FROM audience_snapshots
           WHERE niche_id = %s AND platform = %s AND metric_name = %s AND snapshot_date = %s
           LIMIT 1""",
        (niche_id, platform, metric_name, week_ago),
    ).fetchone()
    delta_7d = round(current - float(prev_row["metric_value"]), 1) if prev_row else None

    # Estimate days to threshold
    days_est = None
    if delta_7d and delta_7d > 0 and current < target:
        remaining = target - current
        daily_rate = delta_7d / 7
        days_est = int(remaining / daily_rate) if daily_rate > 0 else None

    conn.execute(
        """INSERT INTO monetisationprogress
               (id, niche_id, platform, metric_name, current_value, target_value,
                pct_complete, delta_7d, days_to_threshold_est, is_threshold_met,
                data_source, as_of_date)
           VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (niche_id, platform, metric_name)
           DO UPDATE SET current_value = EXCLUDED.current_value,
                        pct_complete = EXCLUDED.pct_complete,
                        delta_7d = EXCLUDED.delta_7d,
                        days_to_threshold_est = EXCLUDED.days_to_threshold_est,
                        is_threshold_met = EXCLUDED.is_threshold_met,
                        as_of_date = EXCLUDED.as_of_date,
                        updated_at = NOW()""",
        (niche_id, platform, metric_name, current, target, pct, delta_7d, days_est, is_met,
         "api", str(today)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect audience metrics for all channels")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but don't write to DB")
    args = parser.parse_args()

    import psycopg
    from psycopg.rows import dict_row

    today = date.today()
    logger.info("Collecting audience metrics for %s", today)

    # Load root env first
    try:
        from dotenv import load_dotenv
        load_dotenv(GENLAB_ROOT / ".env", override=False)
    except ImportError:
        pass

    dsn = os.environ.get("DATABASE_URL", "postgresql://localhost/genlab")
    conn = psycopg.connect(dsn, row_factory=dict_row)

    # Add unique constraint if missing (for upsert)
    try:
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_audience_snap_unique
            ON audience_snapshots (niche_id, platform, metric_name, snapshot_date)
        """)
        conn.execute("""
            DO $$ BEGIN
                ALTER TABLE monetisationprogress
                    ADD CONSTRAINT uq_monetisation_niche_plat_metric
                    UNIQUE (niche_id, platform, metric_name);
            EXCEPTION WHEN duplicate_table THEN NULL;
            END $$;
        """)
        conn.commit()
    except Exception:
        conn.rollback()

    total_metrics = 0

    for niche_id, (dir_name, prefix) in NICHE_ENVS.items():
        _load_env(niche_id)
        logger.info("Fetching %s...", niche_id)

        # Instagram
        ig = fetch_instagram(prefix)
        if ig:
            logger.info("  IG @%s: %s followers, %s posts",
                        ig.get("username", "?"), ig.get("followers", 0), ig.get("media_count", 0))
            if not args.dry_run:
                save_snapshot(conn, niche_id, "instagram", ig, today)
                update_monetisation_progress(conn, niche_id, "instagram", "followers",
                                              ig.get("followers", 0), today)
            total_metrics += len([v for v in ig.values() if not isinstance(v, str)])

        # YouTube
        yt = fetch_youtube(prefix)
        if yt:
            logger.info("  YT: %s subs, %s views, %s videos",
                        yt.get("subscribers", 0), yt.get("total_views", 0), yt.get("video_count", 0))
            if not args.dry_run:
                save_snapshot(conn, niche_id, "youtube", yt, today)
                update_monetisation_progress(conn, niche_id, "youtube", "subscribers",
                                              yt.get("subscribers", 0), today)
            total_metrics += len(yt)

        # Facebook
        fb = fetch_facebook(prefix)
        if fb:
            logger.info("  FB: %s followers", fb.get("followers", 0))
            if not args.dry_run:
                save_snapshot(conn, niche_id, "facebook", fb, today)
                update_monetisation_progress(conn, niche_id, "facebook", "followers",
                                              fb.get("followers", 0), today)
            total_metrics += len(fb)

        # Threads
        th = fetch_threads(prefix)
        if th:
            logger.info("  Threads: @%s", th.get("username", "?"))

    if not args.dry_run:
        conn.commit()

    conn.close()
    logger.info("Done: %d metrics collected for %s", total_metrics, today)


if __name__ == "__main__":
    main()
