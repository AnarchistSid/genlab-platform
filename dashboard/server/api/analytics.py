"""Analytics API endpoints."""
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from datetime import timezone as dt_timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("analytics_api", __name__, url_prefix="/api/v1/analytics")

import time as _time

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DAILY_INTEL_RE = re.compile(r'^\d{8}_\d{6}$')
# Match all pipeline runs: daily intel (YYYYMMDD_HHMMSS or YYYYMMDD_HHMMSS_PID) + pub runs
RUN_DIR_RE = re.compile(r'^(\d{8})_(\d{6})(?:_\w+)?$')

# Overview cache (60s TTL)
_overview_cache: dict = {"data": None, "ts": 0.0, "key": ""}
_OVERVIEW_TTL = 60.0

# Table-level caches — prevents redundant unfiltered SharePoint queries
# across multiple endpoints within the same time window.
_TABLE_CACHE_TTL = 60.0
_table_cache: dict = {}  # {"analytics": (records, ts), "pub_analytics": ...}


def _cached_analytics_all():
    """Fetch Analytics table with 60s cache. Capped at 500 records for dashboard perf."""
    entry = _table_cache.get("analytics")
    now = _time.time()
    if entry and (now - entry[1]) < _TABLE_CACHE_TTL:
        return entry[0]
    records = _get_client().analytics.all(max_records=500)
    _table_cache["analytics"] = (records, now)
    return records


def _cached_pub_analytics_all():
    """Fetch Publishing_Analytics table with 60s cache. Capped at 500 records."""
    entry = _table_cache.get("pub_analytics")
    now = _time.time()
    if entry and (now - entry[1]) < _TABLE_CACHE_TTL:
        return entry[0]
    records = _get_client().publishing_analytics.all(max_records=500)
    _table_cache["pub_analytics"] = (records, now)
    return records



def _get_client():
    from server.core.graph_sync import get_sync_client
    return get_sync_client()


def _filter_by_date_range(records: list, date_field: str = "published_at",
                          fallback_field: str = "fetched_at") -> list:
    """Filter records by ?from= and ?to= query params. Returns all if params missing.

    When ``date_field`` is NULL on a record, falls back to ``fallback_field``
    (typically ``fetched_at``) so records missing ``published_at`` aren't silently dropped.
    """
    from_str = request.args.get("from")
    to_str = request.args.get("to")
    if not from_str and not to_str:
        return records
    from_dt = _parse_datetime(from_str) if from_str else None
    to_dt = _parse_datetime(to_str) if to_str else None
    # Make to_dt end-of-day inclusive
    if to_dt and not to_dt.hour and not to_dt.minute:
        to_dt = to_dt.replace(hour=23, minute=59, second=59)
    filtered = []
    for r in records:
        fields = r.get("fields", {})
        dt = _parse_datetime(fields.get(date_field, ""))
        if dt is None and fallback_field:
            dt = _parse_datetime(fields.get(fallback_field, ""))
        if dt is None:
            continue
        if from_dt and dt < from_dt:
            continue
        if to_dt and dt > to_dt:
            continue
        filtered.append(r)
    return filtered


def _parse_datetime(value) -> datetime | None:
    """Parse datetime from various formats Microsoft Lists may return.

    Always returns timezone-aware (UTC) datetimes to avoid naive vs aware
    comparison errors when filtering records against date-only query params.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        pass
    return None


@bp.route("/top-posts", methods=["GET"])
def top_posts():
    """Return top 20 posts sorted by likes descending.

    Queries the Analytics table via the sync Graph client and returns
    the highest-performing posts across all niches.

    Returns a list of up to 20 records, each containing:
        post_id, platform, niche_id, likes, comments, reach, collected_at
    """
    try:
        records = _cached_analytics_all()
    except Exception as exc:
        logger.error("top-posts: analytics fetch failed: %s", exc)
        return api_error(error="analytics_unavailable", code=502)

    posts = []
    for r in records:
        f = r.get("fields", {})
        likes = int(float(f.get("likes", 0) or 0))
        posts.append({
            "post_id": f.get("post_id") or f.get("id") or r.get("id", ""),
            "platform": f.get("platform", ""),
            "niche_id": f.get("niche_id") or "",
            "likes": likes,
            "comments": int(float(f.get("comments", 0) or 0)),
            "reach": int(float(f.get("reach", 0) or 0)),
            "collected_at": f.get("collected_at") or f.get("fetched_at") or "",
        })

    posts.sort(key=lambda p: p["likes"], reverse=True)
    top = posts[:20]

    # Enrich top posts with hook text from blueprints via publishing_analytics
    _dsn = os.environ.get("DATABASE_URL", "")
    if _dsn and top:
        try:
            import psycopg
            from psycopg.rows import dict_row
            post_ids = [p["post_id"] for p in top]
            with psycopg.connect(_dsn, row_factory=dict_row) as _conn:
                rows = _conn.execute(
                    "SELECT pa.post_id, b.hook, b.hook_text, b.title "
                    "FROM publishing_analytics pa "
                    "JOIN blueprints b ON pa.blueprint_id = b.id "
                    "WHERE pa.post_id = ANY(%s)",
                    (post_ids,),
                ).fetchall()
                hook_map = {r["post_id"]: r for r in rows}
                for p in top:
                    linked = hook_map.get(p["post_id"], {})
                    p["hook_text"] = linked.get("hook") or linked.get("hook_text") or ""
                    p["title"] = linked.get("title") or ""
        except Exception as exc:
            logger.debug("top-posts hook enrichment failed: %s", exc)
            for p in top:
                p.setdefault("hook_text", "")
                p.setdefault("title", "")

    return api_success(data={"posts": top, "total": len(top)})


@bp.route("/overview", methods=["GET"])
def overview():
    """Unified analytics overview for the new Analytics view."""
    niche_id = request.args.get("niche_id", "all")
    window = request.args.get("window", "7d")
    days = {"7d": 7, "14d": 14, "30d": 30}.get(window, 7)

    cache_key = f"{niche_id}:{window}"
    now = _time.time()
    if _overview_cache["data"] and _overview_cache["key"] == cache_key and (now - _overview_cache["ts"]) < _OVERVIEW_TTL:
        return api_success(data=_overview_cache["data"])

    try:
        result = _build_overview(niche_id, days, window)
        _overview_cache["data"] = result
        _overview_cache["key"] = cache_key
        _overview_cache["ts"] = now
        return api_success(data=result)
    except Exception as e:
        logger.error("Analytics overview error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


def _build_overview(niche_id: str, days: int, window: str) -> dict:
    client = _get_client()
    now_dt = datetime.now(UTC)
    cutoff = now_dt - timedelta(days=days)

    # --- Fetch analytics records (engagement data from Analytics table) ---
    analytics_records = _cached_analytics_all()
    filtered_analytics = []
    for r in analytics_records:
        f = r.get("fields", {})
        # Use collected_at (when metric was fetched) instead of published_at
        # (when post went live). Engagement metrics are cumulative — a post
        # published 3 weeks ago with fresh metrics is relevant to the window.
        dt = _parse_datetime(f.get("collected_at") or f.get("fetched_at") or f.get("published_at", ""))
        if not (dt and dt >= cutoff):
            continue
        # Apply niche filter at the source — prevents cross-niche leakage
        if niche_id != "all":
            r_niche = f.get("niche_id") or ""
            if r_niche == "ai_news":
                r_niche = "ai_creators"
            if r_niche and r_niche != niche_id:
                continue
        filtered_analytics.append(r)

    # --- Fetch Publishing_Analytics (source of truth for actual publishes) ---
    pub_analytics = _cached_pub_analytics_all()
    SUCCESS_STATUSES = {"SUCCESS", "INSIGHTS_6H", "INSIGHTS_24H", "INSIGHTS_48H", "INSIGHTS_168H"}
    ATTEMPT_STATUSES = SUCCESS_STATUSES | {"FAILED", "SKIPPED", "DELETED"}

    # Filter pub_analytics by window + niche
    filtered_pub = []
    filtered_attempts = []
    for r in pub_analytics:
        f = r.get("fields", {})
        dt = _parse_datetime(f.get("published_at", ""))
        status = (f.get("status") or "").upper()
        r_niche = f.get("niche_id") or ""
        # Normalize ai_news → ai_creators
        if r_niche == "ai_news":
            r_niche = "ai_creators"
        if niche_id != "all" and r_niche and r_niche != niche_id:
            continue
        if dt and dt >= cutoff:
            if status in ATTEMPT_STATUSES:
                filtered_attempts.append(r)
            if status in SUCCESS_STATUSES:
                filtered_pub.append(r)

    # --- Fetch PUBLISHED + ARCHIVED blueprints for bp_map (engagement niche lookup) ---
    all_bps = client.blueprints.all(
        formula="OR({status}='PUBLISHED',{status}='ARCHIVED')",
        max_records=1000,
    )
    bp_map = {}  # id -> fields
    niche_bp_map = {}  # niche_id -> list of (hook, title) for fallback
    for b in all_bps:
        fields = b.get("fields", {})
        bp_map[str(b.get("id", ""))] = fields
        n = fields.get("niche_id", "")
        if n not in niche_bp_map:
            niche_bp_map[n] = []
        niche_bp_map[n].append(fields)

    # --- Determine if we have real insights or need estimates ---
    has_real_insights = len(filtered_analytics) > 0
    is_estimated = not has_real_insights

    # --- Deduplicate analytics by post_id (keep highest reach per post) ---
    # Multiple insight windows (6h, 24h, 48h, 168h) create duplicate records
    # with cumulative metrics. Using all of them inflates totals.
    _dedup_analytics = {}
    for r in filtered_analytics:
        pid = r.get("fields", {}).get("post_id", str(r.get("id", "")))
        cur_reach = int(float(r.get("fields", {}).get("reach", 0) or 0))
        prev = _dedup_analytics.get(pid)
        if not prev or cur_reach > int(float(prev.get("fields", {}).get("reach", 0) or 0)):
            _dedup_analytics[pid] = r
    deduped_analytics = list(_dedup_analytics.values())

    # --- Build per-platform metrics from Analytics (engagement) ---
    platform_metrics = {}
    for r in deduped_analytics:
        f = r.get("fields", {})
        plat = _norm_platform(f.get("platform", "unknown"))
        if niche_id != "all":
            # Use niche_id from record first, then blueprint lookup
            record_niche = f.get("niche_id") or ""
            if record_niche == "ai_news":
                record_niche = "ai_creators"
            if not record_niche:
                bl = str(f.get("blueprint_link", ""))
                if bl and bl in bp_map:
                    record_niche = _bp_niche(bp_map[bl])
            if record_niche and record_niche != niche_id:
                continue
        if plat not in platform_metrics:
            platform_metrics[plat] = {
                "reach": 0, "posts": 0, "likes": 0, "comments": 0, "shares": 0,
                "impressions": 0,
            }
        pm = platform_metrics[plat]
        pm["posts"] += 1
        pm["reach"] += int(float(f.get("reach", 0) or 0))
        pm["impressions"] += int(float(f.get("impressions", 0) or 0))
        pm["likes"] += int(float(f.get("likes", 0) or 0))
        pm["comments"] += int(float(f.get("comments", 0) or 0))
        pm["shares"] += int(float(f.get("shares", 0) or 0))

    # Supplement platform post counts from Publishing_Analytics when Analytics
    # doesn't have records for a platform (e.g. engagement hasn't been fetched yet)
    for r in filtered_pub:
        f = r.get("fields", {})
        plat = _norm_platform(f.get("platform", "unknown"))
        if plat not in platform_metrics:
            platform_metrics[plat] = {
                "reach": 0, "posts": 0, "likes": 0, "comments": 0, "shares": 0,
                "impressions": 0,
            }
        # Only add post count if Analytics doesn't already track this platform
        if not has_real_insights or platform_metrics[plat]["posts"] == 0:
            platform_metrics[plat]["posts"] += 1

    # --- Summary: use Publishing_Analytics for post counts ---
    total_reach = sum(p["reach"] for p in platform_metrics.values())
    # Count unique candidate_ids from successful publishes (1 blueprint = 1 post even if multi-platform)
    published_candidates = set()
    for r in filtered_pub:
        f = r.get("fields", {})
        cid = f.get("candidate_id") or f.get("blueprint_id") or str(r.get("id", ""))
        published_candidates.add(cid)
    total_posts = len(published_candidates) if published_candidates else len(filtered_pub)
    total_interactions = sum(p["likes"] + p["comments"] + p["shares"] for p in platform_metrics.values())
    avg_eng = round((total_interactions / total_reach * 100), 2) if total_reach > 0 else None

    # Publish success rate from Publishing_Analytics (actual attempts vs successes)
    total_attempted = len(filtered_attempts)
    total_succeeded = len(filtered_pub)
    pub_success = round(total_succeeded / total_attempted * 100, 1) if total_attempted > 0 else 100.0

    # Best post
    best_post = None
    if filtered_analytics:
        best_r = max(filtered_analytics, key=lambda r: int(float(r.get("fields", {}).get("reach", 0) or 0)))
        bf = best_r.get("fields", {})
        best_post = {
            "id": str(best_r.get("id", "")),
            "title": bf.get("story_title", ""),
            "hook_text": bf.get("story_title", "")[:60],
            "total_reach": int(float(bf.get("reach", 0) or 0)),
            "engagement_rate": float(bf.get("engagement_rate", 0) or 0),
            "platform": bf.get("platform", ""),
            "published_at": bf.get("published_at", ""),
        }
    elif filtered_pub:
        # Use the most recent successful publish as "best"
        best_pub = filtered_pub[0]  # already sorted by published_at desc from SP
        pf = best_pub.get("fields", {})
        best_post = {
            "id": str(best_pub.get("id", "")),
            "title": pf.get("candidate_id", ""),
            "hook_text": pf.get("candidate_id", "")[:60],
            "total_reach": 0,
            "engagement_rate": None,
            "platform": pf.get("platform", "instagram"),
            "published_at": pf.get("published_at", ""),
        }

    # --- per-platform data availability ---
    EXPECTED_PLATFORMS = ["instagram", "youtube", "x_twitter", "facebook"]
    platform_data_status = {}
    for plat in EXPECTED_PLATFORMS:
        pm = platform_metrics.get(plat)
        if pm and (pm["reach"] > 0 or pm["impressions"] > 0 or pm["likes"] > 0):
            platform_data_status[plat] = "available"
        elif pm and pm["posts"] > 0:
            platform_data_status[plat] = "no_metrics"  # posts exist but no engagement data
        else:
            platform_data_status[plat] = "no_data"

    # --- by_platform ---
    metric_labels = {"instagram": "Reach", "youtube": "Views", "x_twitter": "Impressions", "facebook": "Views", "threads": "Reach"}
    by_platform = {}
    for plat, pm in platform_metrics.items():
        eng_rate = round((pm["likes"] + pm["comments"] + pm["shares"]) / pm["reach"] * 100, 2) if pm["reach"] > 0 else None
        by_platform[plat] = {
            "reach": pm["reach"],
            "posts": pm["posts"],
            "avg_engagement_rate": eng_rate,
            "metric_label": metric_labels.get(plat, "Reach"),
            "data_status": platform_data_status.get(plat, "no_data"),
        }

    # --- time_series ---
    ts_buckets = {}
    for d in range(days):
        dt = now_dt - timedelta(days=days - 1 - d)
        date_key = dt.strftime("%Y-%m-%d")
        ts_buckets[date_key] = {
            "date": date_key, "total_reach": 0, "posts": 0,
            "instagram_reach": 0, "youtube_reach": 0, "x_twitter_reach": 0, "facebook_reach": 0,
            "ai_creators_reach": 0, "gaming_reach": 0,
            "sports_reach": 0, "movies_reach": 0, "anime_reach": 0,
        }

    for r in filtered_analytics:
        f = r.get("fields", {})
        # Use collected_at for time series bucketing — published_at is when
        # the post went live (often weeks ago), but we want to show when
        # the reach/engagement was measured so the chart reflects recent activity.
        dt = _parse_datetime(f.get("collected_at") or f.get("fetched_at") or f.get("published_at", ""))
        if not dt:
            continue
        date_key = dt.strftime("%Y-%m-%d")
        if date_key not in ts_buckets:
            continue
        # Determine niche for this record — check explicit niche_id first (most
        # reliable), then fall back to blueprint_link lookup, then template heuristic.
        niche = f.get("niche_id") or None
        if niche == "ai_news":
            niche = "ai_creators"
        if not niche:
            bl = str(f.get("blueprint_link", "") or "")
            if bl and bl in bp_map:
                niche = _bp_niche(bp_map[bl])
        # Apply niche filter (same as platform_metrics)
        if niche_id != "all" and niche and niche != niche_id:
            continue
        reach = int(float(f.get("reach", 0) or 0))
        plat = _norm_platform(f.get("platform", ""))
        ts_buckets[date_key]["total_reach"] += reach
        ts_buckets[date_key]["posts"] += 1
        plat_key = f"{plat}_reach"
        if plat_key in ts_buckets[date_key]:
            ts_buckets[date_key][plat_key] += reach
        # Attribute to niche bucket
        if niche:
            niche_key = f"{niche}_reach"
            if niche_key in ts_buckets[date_key]:
                ts_buckets[date_key][niche_key] += reach

    # When no real analytics, populate time_series post counts from Publishing_Analytics.
    # Also attribute to niche buckets so chart lines appear for CW/SR/FD.
    if not has_real_insights and filtered_pub:
        for r in filtered_pub:
            f = r.get("fields", {})
            dt = _parse_datetime(f.get("published_at", ""))
            if not dt:
                continue
            date_key = dt.strftime("%Y-%m-%d")
            if date_key not in ts_buckets:
                continue
            ts_buckets[date_key]["posts"] += 1
            # Attribute post count to niche bucket for chart lines
            pub_niche = f.get("niche_id") or ""
            if pub_niche == "ai_news":
                pub_niche = "ai_creators"
            if pub_niche:
                niche_key = f"{pub_niche}_reach"
                if niche_key in ts_buckets[date_key]:
                    # Use 1 as a placeholder reach to make the line visible
                    ts_buckets[date_key][niche_key] += 1

    time_series = sorted(ts_buckets.values(), key=lambda x: x["date"])

    # --- funnel ---
    funnel = _compute_funnel(client)

    # --- top_performers ---
    top_performers = []
    if filtered_analytics:
        # Deduplicate by post_id — keep highest reach per post
        _seen_posts = {}
        for r in filtered_analytics:
            pid = r.get("fields", {}).get("post_id", str(r.get("id", "")))
            reach = int(float(r.get("fields", {}).get("reach", 0) or 0))
            if pid not in _seen_posts or reach > int(float(_seen_posts[pid].get("fields", {}).get("reach", 0) or 0)):
                _seen_posts[pid] = r
        sorted_analytics = sorted(_seen_posts.values(), key=lambda r: int(float(r.get("fields", {}).get("reach", 0) or 0)), reverse=True)
        for r in sorted_analytics[:10]:
            f = r.get("fields", {})
            bl = str(f.get("blueprint_link", "") or "")
            bp_fields = bp_map.get(bl, {})
            record_niche = f.get("niche_id") or (_bp_niche(bp_fields) if bp_fields else "ai_creators")

            # Resolve hook text: try blueprint lookup first, then match by post_id
            hook = bp_fields.get("hook") or bp_fields.get("hook_text") or bp_fields.get("title") or ""
            if not hook:
                # Try to find blueprint via post_id match in publishing_analytics
                post_id = f.get("post_id", "")
                # Strip platform prefix (e.g. "instagram:12345" → "12345")
                post_id.split(":", 1)[-1] if ":" in post_id else post_id
                # Search for matching blueprint via niche's recent published posts
                for bp_f in niche_bp_map.get(record_niche, []):
                    if bp_f.get("hook"):
                        hook = bp_f["hook"]
                        break

            top_performers.append({
                "id": str(r.get("id", "")),
                "title": f.get("story_title", bp_fields.get("topic", "")),
                "hook_text": (hook or f.get("story_title") or f.get("post_id", ""))[:60],
                "niche_id": record_niche,
                "platform": f.get("platform", ""),
                "total_reach": int(float(f.get("reach", 0) or 0)),
                "engagement_rate": float(f.get("engagement_rate", 0) or 0),
                "published_at": f.get("published_at", ""),
            })
        # Second dedup pass: same hook_text across platforms → keep highest reach
        _seen_hooks: dict[str, dict] = {}
        for tp in top_performers:
            key = (tp.get("hook_text", "").lower().strip(), tp.get("niche_id", ""))
            if key not in _seen_hooks or tp["total_reach"] > _seen_hooks[key]["total_reach"]:
                _seen_hooks[key] = tp
        top_performers = sorted(_seen_hooks.values(), key=lambda x: x["total_reach"], reverse=True)[:10]
    elif filtered_pub:
        # Show recent publishes as top performers when no engagement data yet
        for r in filtered_pub[:10]:
            f = r.get("fields", {})
            r_niche = f.get("niche_id") or "ai_creators"
            if r_niche == "ai_news":
                r_niche = "ai_creators"
            top_performers.append({
                "id": str(r.get("id", "")),
                "title": f.get("candidate_id", ""),
                "hook_text": f.get("candidate_id", "")[:60],
                "niche_id": r_niche,
                "platform": f.get("platform", "instagram"),
                "total_reach": 0,
                "engagement_rate": None,
                "published_at": f.get("published_at", ""),
            })

    return {
        "window": f"{days}d",
        "niche_id": niche_id,
        "is_estimated": is_estimated,
        "platform_data_status": platform_data_status,
        "summary": {
            "total_reach": total_reach,
            "total_posts": total_posts,
            "total_likes": sum(p.get("likes", 0) for p in platform_metrics.values()),
            "total_comments": sum(p.get("comments", 0) for p in platform_metrics.values()),
            "avg_engagement_rate": avg_eng,
            "publish_success_rate": pub_success,
            "best_post": best_post,
        },
        "by_platform": by_platform,
        "time_series": time_series,
        "funnel": funnel,
        "top_performers": top_performers,
    }


def _bp_niche(fields: dict) -> str:
    """Determine niche from blueprint fields.

    Checks explicit niche_id first, then falls back to template_id heuristics.
    """
    # Explicit niche_id takes priority
    niche = fields.get("niche_id", "")
    if niche:
        # Normalize ai_tech ↔ ai_creators
        return "ai_creators" if niche == "ai_tech" else niche

    # Heuristic: template_id prefix
    tid = str(fields.get("template_id", "")).lower()
    if tid.startswith("tpl_gam") or "gaming" in tid:
        return "gaming"
    if tid.startswith("tpl_spo") or "sports" in tid:
        return "sports"
    if tid.startswith("tpl_mov") or "movies" in tid or "film" in tid:
        return "movies"
    if tid.startswith("tpl_ani") or "anime" in tid:
        return "anime"
    return "ai_creators"


def _norm_platform(plat: str) -> str:
    """Normalize platform names."""
    plat = plat.lower().strip()
    if plat in ("twitter", "x", "x_twitter"):
        return "x_twitter"
    if plat in ("ig", "instagram"):
        return "instagram"
    if plat in ("yt", "youtube"):
        return "youtube"
    if plat in ("fb", "facebook"):
        return "facebook"
    return plat


def _compute_funnel(client) -> dict:
    """Compute pipeline funnel from blueprint status counts.

    Includes ARCHIVED blueprints — they passed through the full pipeline
    before being archived (published then auto-archived, or auto-archived
    at an earlier stage). Excluding them makes the funnel look artificially
    narrow and undercounts published posts.
    """
    try:
        records = client.blueprints.all(
            formula="OR({status}='INTEL_READY',{status}='DRAFTED',"
            "{status}='VISUAL_READY',{status}='PUBLISHED',{status}='ARCHIVED')",
            max_records=2000,
        )
        counts = {"INTEL_READY": 0, "DRAFTED": 0, "VISUAL_READY": 0, "PUBLISHED": 0, "ARCHIVED": 0}
        # Track archived items that reached each stage based on action_taken
        archived_rendered = 0  # had visuals before archival
        archived_published = 0  # were published before archival
        for r in records:
            fields = r.get("fields", {})
            status = fields.get("status", "")
            if status in counts:
                counts[status] += 1
            if status == "ARCHIVED":
                action = (fields.get("action_taken") or "").lower()
                # Posts that were published then archived
                if action in ("approved", "published"):
                    archived_published += 1
                    archived_rendered += 1
                # Posts that had visuals but were archived before publish
                elif "no_video" not in action and "stale" not in action and "duplicate" not in action:
                    archived_rendered += 1

        # Fetch total stories (source items before blueprint creation)
        total_bp = sum(counts.values())
        stories_count = total_bp
        try:
            import psycopg as _pg
            from psycopg.rows import dict_row as _dr
            _dsn = os.environ.get("DATABASE_URL", "")
            if _dsn:
                with _pg.connect(_dsn, row_factory=_dr) as _c:
                    _c.execute("SELECT set_config('app.niche_id', 'all', true)")
                    stories_count = _c.execute("SELECT COUNT(*) as cnt FROM stories").fetchone()["cnt"]
        except Exception:
            pass

        # Items that passed filtering (blueprints created from stories)
        filtered = total_bp
        # Items with content written (DRAFTED or beyond — exclude archived-before-write)
        archived_before_write = sum(
            1 for r in records
            if r.get("fields", {}).get("status") == "ARCHIVED"
            and any(tag in (r.get("fields", {}).get("action_taken") or "").lower()
                    for tag in ("no_video", "stale", "duplicate", "weak_hook", "source_not_downloadable"))
        )
        written = total_bp - archived_before_write
        # Items that were rendered (VISUAL_READY + PUBLISHED + archived that had renders)
        rendered = counts["VISUAL_READY"] + counts["PUBLISHED"] + archived_rendered
        # Items that were actually published
        published = counts["PUBLISHED"] + archived_published

        return {
            "fetched": stories_count or total_bp,
            "filtered": filtered,
            "written": written,
            "rendered": rendered,
            "published": published,
        }
    except Exception:
        return {"fetched": 0, "filtered": 0, "written": 0, "rendered": 0, "published": 0}


@bp.route("/publishing", methods=["GET"])
def publishing():
    try:
        records = _cached_pub_analytics_all()
        return api_success(data={"data": [{"id": r["id"], **r.get("fields", {})} for r in records]})
    except Exception as e:
        logger.error("Publishing analytics error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/content", methods=["GET"])
def content():
    niche_id = request.args.get("niche_id", "all")
    try:
        records = _get_client().blueprints.all(
            formula="OR({status}='PUBLISHED',{status}='VISUAL_READY')"
        )
        if niche_id and niche_id != "all":
            records = [
                r for r in records
                if _bp_niche(r.get("fields", {})) == niche_id
            ]
        # Group by template — check template_id (text), then template (linked record)
        templates = {}
        for r in records:
            fields = r.get("fields", {})
            tmpl = fields.get("template_id") or ""
            if not tmpl:
                tmpl_link = fields.get("template", [])
                tmpl = str(tmpl_link[0]) if isinstance(tmpl_link, list) and tmpl_link else "unknown"
            if tmpl not in templates:
                templates[tmpl] = {"template_id": tmpl, "total": 0, "published": 0}
            templates[tmpl]["total"] += 1
            if r.get("fields", {}).get("status") == "PUBLISHED":
                templates[tmpl]["published"] += 1
        return api_success(data={"data": list(templates.values())})
    except Exception as e:
        logger.error("Content analytics error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


def _extract_run_cost(run_dir: Path) -> dict:
    """Extract cost from ALL available sources in a run directory.

    Reads run_report.json (metrics.estimated_cost_usd, metrics.cost_breakdown),
    plus standalone summaries (platform_adapt_summary, publish_all_summary,
    content_generation_summary, render_summary) to capture costs from all run types.
    """
    cost = 0.0
    breakdown = {}
    run_type = "unknown"
    duration = 0.0
    status = "unknown"
    errors_count = 0

    # 1. Run report (primary source — daily_intel + pub runs that ran write_run_report)
    report = run_dir / "run_report.json"
    if report.exists():
        try:
            data = json.loads(report.read_text())
            run_type = data.get("run_type", "unknown")
            duration = float(data.get("duration_seconds", 0) or 0)
            status = data.get("status", "unknown")
            errors_count = len(data.get("errors", []))
            metrics = data.get("metrics", {})
            cost = float(metrics.get("estimated_cost_usd", 0) or 0)
            breakdown = metrics.get("cost_breakdown", {}) or {}
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # 2. If no run_report or cost is zero, scan standalone summaries
    #    (pub/finalize runs may not always run write_run_report)
    if cost == 0:
        # Content generation
        cg = run_dir / "content_generation_summary.json"
        if cg.exists():
            try:
                d = json.loads(cg.read_text())
                c = float(d.get("estimated_cost_usd", 0) or 0)
                if c > 0:
                    breakdown["content_llm"] = c
                    cost += c
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        # Platform adaptation
        pa = run_dir / "platform_adapt_summary.json"
        if pa.exists():
            try:
                d = json.loads(pa.read_text())
                c = float(d.get("estimated_cost_usd", 0) or 0)
                if c > 0:
                    breakdown["platform_adapt_llm"] = c
                    cost += c
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        # Render summary (image gen + audio)
        rs = run_dir / "render_summary.json"
        if rs.exists():
            try:
                d = json.loads(rs.read_text())
                ai_count = int(d.get("ai_hero_count", 0) or 0)
                if ai_count > 0:
                    img_cost = round(ai_count * 0.02, 4)
                    breakdown["image_generation"] = img_cost
                    cost += img_cost
                audio = float(d.get("audio_cost", 0) or 0)
                if audio > 0:
                    breakdown["audio_generation"] = round(audio, 4)
                    cost += audio
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

    # Classify run type from dir name if not from report
    run_name = run_dir.name
    if run_type == "unknown":
        if "_pub" in run_name:
            run_type = "publish"
        elif run_name.startswith("render_"):
            run_type = "render"
        elif run_name.startswith("finalize_"):
            run_type = "finalize"
        elif run_name.startswith("full_rerender_"):
            run_type = "rerender"
        elif RUN_DIR_RE.match(run_name):
            run_type = "daily_intel"

    return {
        "cost": round(cost, 6),
        "breakdown": breakdown,
        "run_type": run_type,
        "duration": duration,
        "status": status,
        "errors": errors_count,
    }


def _parse_run_date(run_name: str) -> str | None:
    """Extract YYYY-MM-DD date from run directory name."""
    for part in run_name.replace("-", "_").split("_"):
        if len(part) == 8 and part.isdigit():
            try:
                dt = datetime.strptime(part, "%Y%m%d")
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                pass
    return None


@bp.route("/pipeline", methods=["GET"])
def pipeline_analytics():
    """Return per-run and per-day cost data for ALL pipeline activity.

    Query params:
      ?aggregate=day  — return daily aggregates instead of per-run data
      (default: per-run)
    """
    runs_dir = PROJECT_ROOT / ".tmp" / "runs"
    aggregate = request.args.get("aggregate", "")
    runs = []

    if runs_dir.exists():
        for run_dir in sorted(runs_dir.iterdir(), reverse=True)[:200]:
            if not run_dir.is_dir():
                continue
            run_name = run_dir.name
            # Skip non-pipeline dirs (e.g. cache, temp)
            date_str = _parse_run_date(run_name)
            if not date_str:
                continue

            cost_info = _extract_run_cost(run_dir)
            runs.append({
                "run_id": run_name,
                "date": date_str,
                "run_type": cost_info["run_type"],
                "duration_seconds": cost_info["duration"],
                "errors": cost_info["errors"],
                "status": cost_info["status"],
                "cost_estimate": cost_info["cost"],
                "cost_breakdown": cost_info["breakdown"],
            })

    if aggregate == "day":
        # Aggregate costs by day for the cost tracker chart
        daily: dict[str, dict] = {}
        for r in runs:
            d = r["date"]
            if d not in daily:
                daily[d] = {
                    "date": d,
                    "total_cost": 0.0,
                    "run_count": 0,
                    "breakdown": {},
                    "run_types": {},
                }
            day = daily[d]
            day["total_cost"] = round(day["total_cost"] + r["cost_estimate"], 6)
            day["run_count"] += 1
            # Merge breakdown
            for k, v in r.get("cost_breakdown", {}).items():
                if k == "total":
                    continue
                day["breakdown"][k] = round(
                    day["breakdown"].get(k, 0) + float(v or 0), 6
                )
            # Count run types
            rt = r["run_type"]
            day["run_types"][rt] = day["run_types"].get(rt, 0) + 1

        return api_success(data={
            "data": sorted(daily.values(), key=lambda x: x["date"]),
        })

    return api_success(data={"data": runs})


@bp.route("/funnel", methods=["GET"])
def funnel():
    """Status funnel: count blueprints at each pipeline stage + total cost."""
    niche_id = request.args.get("niche_id", "all")
    STAGES = ["INTEL_READY", "DRAFTED", "VISUAL_READY", "PUBLISHED"]
    try:
        records = _get_client().blueprints.all(
            formula="OR({status}='INTEL_READY',{status}='DRAFTED',"
            "{status}='VISUAL_READY',{status}='PUBLISHED')"
        )
        if niche_id and niche_id != "all":
            records = [
                r for r in records
                if _bp_niche(r.get("fields", {})) == niche_id
            ]
        counts = {s: 0 for s in STAGES}
        total_cost = 0.0
        for r in records:
            fields = r.get("fields", {})
            status = fields.get("status", "")
            if status in counts:
                counts[status] += 1
            total_cost += float(fields.get("generation_cost_usd", 0) or 0)
        data = [{"stage": s, "count": counts[s]} for s in STAGES]
        return api_success(data={"data": data, "total_cost": round(total_cost, 4)})
    except Exception as e:
        logger.error("Funnel analytics error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/engagement", methods=["GET"])
def engagement():
    """Per-post engagement metrics from the Analytics table."""
    try:
        records = _cached_analytics_all()
        records = _filter_by_date_range(records, date_field="published_at")
        data = []
        for r in records:
            fields = r.get("fields", {})
            data.append({"id": r["id"], **fields})
        return api_success(data={"data": data})
    except Exception as e:
        logger.error("Engagement analytics error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/engagement/summary", methods=["GET"])
def engagement_summary():
    """Aggregated per-platform engagement stats."""
    try:
        records = _cached_analytics_all()
        records = _filter_by_date_range(records, date_field="published_at")
        # Filter out records with zero data (likely fetch failures, not genuine zeros)
        valid_records = []
        excluded_count = 0
        for r in records:
            fields = r.get("fields", {})
            impressions = float(fields.get("impressions", 0) or 0)
            likes = float(fields.get("likes", 0) or 0)
            engagement_rate = float(fields.get("engagement_rate", 0) or 0)
            if impressions > 0 or likes > 0 or engagement_rate > 0:
                valid_records.append(r)
            else:
                excluded_count += 1

        platforms: dict[str, dict] = {}
        for r in valid_records:
            fields = r.get("fields", {})
            plat = fields.get("platform", "unknown")
            if plat not in platforms:
                platforms[plat] = {
                    "platform": plat,
                    "total_posts": 0,
                    "sum_engagement_rate": 0.0,
                    "sum_viral_score": 0.0,
                    "total_impressions": 0,
                    "total_reach": 0,
                    "total_likes": 0,
                    "total_comments": 0,
                    "total_shares": 0,
                    "total_saves": 0,
                }
            p = platforms[plat]
            p["total_posts"] += 1
            p["sum_engagement_rate"] += float(fields.get("engagement_rate", 0) or 0)
            p["sum_viral_score"] += float(fields.get("viral_score", 0) or 0)
            p["total_impressions"] += int(float(fields.get("impressions", 0) or 0))
            p["total_reach"] += int(float(fields.get("reach", 0) or 0))
            p["total_likes"] += int(float(fields.get("likes", 0) or 0))
            p["total_comments"] += int(float(fields.get("comments", 0) or 0))
            p["total_shares"] += int(float(fields.get("shares", 0) or 0))
            p["total_saves"] += int(float(fields.get("saved", 0) or 0))

        data = []
        for p in platforms.values():
            n = p["total_posts"]
            data.append({
                "platform": p["platform"],
                "total_posts": n,
                "avg_engagement_rate": round(p["sum_engagement_rate"] / n, 4) if n else 0,
                "avg_viral_score": round(p["sum_viral_score"] / n, 2) if n else 0,
                "total_impressions": p["total_impressions"],
                "total_reach": p["total_reach"],
                "total_likes": p["total_likes"],
                "total_comments": p["total_comments"],
                "total_shares": p["total_shares"],
                "total_saves": p["total_saves"],
            })
        return api_success(data={
            "data": data,
            "excluded_count": excluded_count,
            "total_records": len(records),
            "data_quality": "partial" if excluded_count > len(records) * 0.2 else "good",
        })
    except Exception as e:
        logger.error("Engagement summary error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/heatmap", methods=["GET"])
def heatmap():
    try:
        records = _cached_pub_analytics_all()
        matrix = {}
        for r in records:
            published_at = r.get("fields", {}).get("published_at", "")
            if not published_at:
                continue
            dt = _parse_datetime(published_at)
            if dt is None:
                continue
            try:
                IST = dt_timezone(timedelta(hours=5, minutes=30))
                # Convert to IST: if naive assume UTC, if aware convert
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                dt_ist = dt.astimezone(IST)
                key = (dt_ist.strftime("%A"), dt_ist.hour)
                if key not in matrix:
                    matrix[key] = {"day": key[0], "hour": key[1], "count": 0}
                matrix[key]["count"] += 1
            except ValueError:
                continue
        return api_success(data={"data": list(matrix.values())})
    except Exception as e:
        logger.error("Heatmap analytics error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/content-performance", methods=["GET"])
def content_performance():
    """Content performance grouped by format, template, and topic."""
    try:
        client = _get_client()
        analytics_records = _cached_analytics_all()
        analytics_records = _filter_by_date_range(analytics_records, "published_at")

        # Also fetch blueprints to join format/template/topic data
        blueprint_ids = set()
        for r in analytics_records:
            bl = r.get("fields", {}).get("blueprint_link", "")
            if bl:
                blueprint_ids.add(str(bl))

        bp_map = {}
        if blueprint_ids:
            all_bps = client.blueprints.all(max_records=500)
            for b in all_bps:
                bp_map[str(b["id"])] = b.get("fields", {})

        # Group by format
        by_format = {}
        by_template = {}
        by_topic = {}

        for r in analytics_records:
            f = r.get("fields", {})
            engagement_rate = float(f.get("engagement_rate", 0) or 0)
            viral_score = float(f.get("viral_score", 0) or 0)
            impressions = int(float(f.get("impressions", 0) or 0))

            # Use format from Analytics (new field) or fall back to Blueprint join
            fmt = f.get("format", "")
            template_id = ""
            topic = ""

            bl = f.get("blueprint_link", "")
            if bl and str(bl) in bp_map:
                bp_f = bp_map[str(bl)]
                if not fmt:
                    fmt = bp_f.get("format", "unknown")
                template_id = bp_f.get("template_id", "unknown")
                topic = bp_f.get("topic_category", "") or bp_f.get("topic", "unknown")

            if not fmt:
                fmt = "unknown"

            # Format aggregation
            if fmt not in by_format:
                by_format[fmt] = {"format": fmt, "count": 0, "sum_eng": 0, "sum_viral": 0, "sum_imp": 0}
            by_format[fmt]["count"] += 1
            by_format[fmt]["sum_eng"] += engagement_rate
            by_format[fmt]["sum_viral"] += viral_score
            by_format[fmt]["sum_imp"] += impressions

            # Template aggregation
            if template_id:
                if template_id not in by_template:
                    by_template[template_id] = {"template_id": template_id, "count": 0, "sum_eng": 0, "sum_viral": 0}
                by_template[template_id]["count"] += 1
                by_template[template_id]["sum_eng"] += engagement_rate
                by_template[template_id]["sum_viral"] += viral_score

            # Topic aggregation
            if topic:
                if topic not in by_topic:
                    by_topic[topic] = {"topic": topic, "count": 0, "sum_eng": 0, "sum_viral": 0}
                by_topic[topic]["count"] += 1
                by_topic[topic]["sum_eng"] += engagement_rate
                by_topic[topic]["sum_viral"] += viral_score

        def avg_dict(groups):
            result = []
            for g in groups.values():
                n = g["count"]
                result.append({
                    **{k: v for k, v in g.items() if k not in ("sum_eng", "sum_viral", "sum_imp")},
                    "avg_engagement_rate": round(g["sum_eng"] / n, 4) if n else 0,
                    "avg_viral_score": round(g["sum_viral"] / n, 2) if n else 0,
                    "total_impressions": g.get("sum_imp", 0),
                })
            return sorted(result, key=lambda x: x.get("avg_engagement_rate", 0), reverse=True)

        return api_success(data={
            "by_format": avg_dict(by_format),
            "by_template": avg_dict(by_template),
            "by_topic": avg_dict(by_topic),
        })
    except Exception as e:
        logger.error("Content performance error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/trends", methods=["GET"])
def trends():
    """Time-series engagement metrics, grouped by date and platform."""
    try:
        records = _cached_analytics_all()
        records = _filter_by_date_range(records, "published_at")
        granularity = request.args.get("granularity", "daily")

        buckets = {}  # date_key -> platform -> metrics
        for r in records:
            f = r.get("fields", {})
            pub = f.get("published_at", "") or f.get("fetched_at", "")
            dt = _parse_datetime(pub)
            if dt is None:
                continue

            if granularity == "weekly":
                # ISO week start (Monday)
                from datetime import timedelta as _td
                day = dt - _td(days=dt.weekday())
                date_key = day.strftime("%Y-%m-%d")
            else:
                date_key = dt.strftime("%Y-%m-%d")

            platform = f.get("platform", "all")
            key = (date_key, platform)
            if key not in buckets:
                buckets[key] = {
                    "date": date_key, "platform": platform,
                    "count": 0, "sum_eng_rate": 0, "sum_impressions": 0,
                    "sum_reach": 0, "sum_likes": 0, "sum_shares": 0,
                    "sum_viral": 0,
                }
            b = buckets[key]
            b["count"] += 1
            b["sum_eng_rate"] += float(f.get("engagement_rate", 0) or 0)
            b["sum_impressions"] += int(float(f.get("impressions", 0) or 0))
            b["sum_reach"] += int(float(f.get("reach", 0) or 0))
            b["sum_likes"] += int(float(f.get("likes", 0) or 0))
            b["sum_shares"] += int(float(f.get("shares", 0) or 0))
            b["sum_viral"] += float(f.get("viral_score", 0) or 0)

        data = []
        for b in buckets.values():
            n = b["count"]
            data.append({
                "date": b["date"],
                "platform": b["platform"],
                "posts": n,
                "avg_engagement_rate": round(b["sum_eng_rate"] / n, 4) if n else 0,
                "total_impressions": b["sum_impressions"],
                "total_reach": b["sum_reach"],
                "total_likes": b["sum_likes"],
                "total_shares": b["sum_shares"],
                "avg_viral_score": round(b["sum_viral"] / n, 2) if n else 0,
            })

        data.sort(key=lambda x: (x["date"], x["platform"]))
        return api_success(data={"data": data})
    except Exception as e:
        logger.error("Trends error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/audience", methods=["GET"])
def audience():
    """Follower count snapshots over time."""
    try:
        client = _get_client()
        if not hasattr(client, 'audience_snapshots') or client.audience_snapshots is None:
            return api_success(data={"data": [], "message": "Audience tracking not configured. Run setup/migrate_analytics_columns.py to create the table."})
        records = client.audience_snapshots.all(max_records=200)
        data = []
        for r in records:
            f = r.get("fields", {})
            data.append({"id": r["id"], **f})
        data.sort(key=lambda x: str(x.get("date", "")))
        return api_success(data={"data": data})
    except Exception as e:
        logger.error("Audience error: %s", e, exc_info=True)
        return api_success(data={"data": [], "message": "Audience tracking not yet available"})


@bp.route("/virality-breakdown", methods=["GET"])
def virality_breakdown():
    """Per-platform virality signal weights and top viral posts."""
    try:
        import yaml
        config_path = PROJECT_ROOT / "config" / "virality_scoring.yaml"
        weights = {}
        if config_path.exists():
            with open(config_path) as fh:
                cfg = yaml.safe_load(fh) or {}
            weights = cfg.get("platform_weights", {})

        # Get top viral posts
        records = _cached_analytics_all()
        records = _filter_by_date_range(records, "published_at")

        posts = []
        for r in records:
            f = r.get("fields", {})
            vs = float(f.get("viral_score", 0) or 0)
            if vs > 0:
                posts.append({
                    "id": r["id"],
                    "platform": f.get("platform", ""),
                    "viral_score": vs,
                    "engagement_rate": float(f.get("engagement_rate", 0) or 0),
                    "impressions": int(float(f.get("impressions", 0) or 0)),
                    "likes": int(float(f.get("likes", 0) or 0)),
                    "shares": int(float(f.get("shares", 0) or 0)),
                    "saves": int(float(f.get("saved", 0) or 0)),
                    "story_title": f.get("story_title", ""),
                    "format": f.get("format", ""),
                    "published_at": f.get("published_at", ""),
                })

        posts.sort(key=lambda x: x["viral_score"], reverse=True)

        return api_success(data={
            "platform_weights": weights,
            "top_posts": posts[:10],
        })
    except Exception as e:
        logger.error("Virality breakdown error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/monetization", methods=["GET"])
def monetization():
    """Monetization metrics from Postgres promoted columns.

    Returns affiliate network breakdown, post counts, and program list
    derived entirely from the blueprints table (affiliate_product,
    affiliate_network columns).
    """
    try:
        dsn = os.environ.get("DATABASE_URL", "")
        affiliate_count = 0
        total_published = 0
        catalog_networks: dict[str, int] = {}

        if dsn:
            import psycopg
            from psycopg.rows import dict_row
            try:
                with psycopg.connect(dsn, row_factory=dict_row) as conn:
                    total_published = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM blueprints WHERE status = 'PUBLISHED'"
                    ).fetchone()["cnt"]

                    affiliate_count = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM blueprints "
                        "WHERE status IN ('PUBLISHED', 'VISUAL_READY') "
                        "AND affiliate_product IS NOT NULL"
                    ).fetchone()["cnt"]

                    # Network breakdown
                    net_rows = conn.execute(
                        "SELECT affiliate_network, COUNT(*) AS cnt "
                        "FROM blueprints "
                        "WHERE affiliate_product IS NOT NULL "
                        "AND affiliate_network IS NOT NULL "
                        "GROUP BY affiliate_network ORDER BY cnt DESC"
                    ).fetchall()
                    catalog_networks = {r["affiliate_network"]: r["cnt"] for r in net_rows}
            except Exception as e:
                logger.warning("[Monetization] Postgres query failed: %s", e)

        # Build program list from network breakdown
        programs = []
        for net_name, count in catalog_networks.items():
            programs.append({
                "name": net_name.replace("_", " ").title(),
                "slug": net_name,
                "commission": f"{count} posts matched",
                "cta_text": "",
            })

        return api_success(data={
            "active_programs": programs,
            "total_programs": len(programs),
            "posts_with_affiliate_links": affiliate_count,
            "posts_with_newsletter_cta": 0,
            "total_published": total_published,
            "catalog_networks": catalog_networks,
        })
    except Exception as e:
        logger.error("Monetization error: %s", e, exc_info=True)
        return api_error(error="Service temporarily unavailable", code=502)


@bp.route("/demographics", methods=["GET"])
def demographics():
    """Serve YouTube demographics from cached JSON file."""
    import json as _json
    demo_path = Path(__file__).resolve().parent.parent.parent / ".tmp" / "youtube_demographics.json"
    if not demo_path.exists():
        return api_success(data={
            "data": None,
            "message": "No demographics data available. Run fetch_youtube_demographics.py to fetch.",
        })
    try:
        with open(demo_path) as f:
            data = _json.load(f)
        return api_success(data={"data": data})
    except Exception as e:
        logger.error("Demographics read error: %s", e, exc_info=True)
        return api_success(data={"data": None, "message": "Failed to read demographics data"})


@bp.route("/cross-niche", methods=["GET"])
def cross_niche_analytics():
    """Per-niche analytics comparison — reach, likes, posts, publish rate, affiliate clicks."""
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(message="DATABASE_URL not configured")

    result = {}
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            # Engagement aggregates per niche
            rows = conn.execute(
                "SELECT niche_id, COUNT(*) AS records, "
                "COALESCE(SUM(COALESCE((extra->>'reach')::numeric, 0)), 0)::bigint AS total_reach, "
                "COALESCE(SUM(COALESCE((extra->>'likes')::numeric, 0)), 0)::bigint AS total_likes, "
                "COALESCE(SUM(COALESCE((extra->>'comments')::numeric, 0)), 0)::bigint AS total_comments "
                "FROM analytics "
                "WHERE niche_id NOT LIKE 'rls_test%%' AND niche_id NOT LIKE 'test_%%' "
                "GROUP BY niche_id ORDER BY total_reach DESC"
            ).fetchall()
            for r in rows:
                result[r["niche_id"]] = {
                    "total_reach": int(r["total_reach"]),
                    "total_likes": int(r["total_likes"]),
                    "total_comments": int(r["total_comments"]),
                    "analytics_records": r["records"],
                }

            # Publishing success rates per niche
            pub_rows = conn.execute(
                "SELECT niche_id, "
                "COUNT(*) FILTER (WHERE status = 'SUCCESS') AS success, "
                "COUNT(*) FILTER (WHERE status IN ('FAILED', 'SKIPPED')) AS failed "
                "FROM publishing_analytics WHERE niche_id IS NOT NULL GROUP BY niche_id"
            ).fetchall()
            for r in pub_rows:
                niche = r["niche_id"]
                result.setdefault(niche, {})
                total = r["success"] + r["failed"]
                result[niche]["publish_success"] = r["success"]
                result[niche]["publish_failed"] = r["failed"]
                result[niche]["publish_rate"] = round(r["success"] / total * 100) if total > 0 else 0

            # Affiliate clicks per niche (30d)
            click_rows = conn.execute(
                "SELECT niche_id, COUNT(*) AS clicks FROM affiliate_clicks "
                "WHERE created_at >= NOW() - INTERVAL '30 days' GROUP BY niche_id"
            ).fetchall()
            for r in click_rows:
                niche = r["niche_id"]
                result.setdefault(niche, {})
                result[niche]["affiliate_clicks_30d"] = r["clicks"]

    except Exception as exc:
        logger.error("cross-niche analytics failed: %s", exc)
        return api_error(error=str(exc))

    return api_success(data=result)
