"""Cross-niche overview API endpoint for Mission Control."""

import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import yaml
from flask import Blueprint

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("overview_api", __name__, url_prefix="/api/v1/cross-niche")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
# GENLAB_ROOT is the parent GenLab directory — pipeline .tmp/ lives there, not in dashboard/
GENLAB_ROOT = Path(os.environ.get("GENLAB_ROOT", PROJECT_ROOT.parent))
REGISTRY_PATH = PROJECT_ROOT / "configs" / "niches_registry.yaml"
TMP_DIR = GENLAB_ROOT / ".tmp"
RUNS_DIR = TMP_DIR / "runs"

IST = timezone(timedelta(hours=5, minutes=30))

# Module-level cache (120s TTL — dashboard data doesn't need sub-minute freshness)
_overview_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 120.0


def _get_client():
    from server.core.graph_sync import get_sync_client

    return get_sync_client()


def _load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("niches", [])


def _last_run_at(niche_id: str) -> str | None:
    """Find the most recent run report for a niche and extract completed_at."""
    # Look in .tmp/runs/ for directories, find newest json by mtime
    if not RUNS_DIR.exists():
        return None
    newest_time = 0.0
    newest_file = None
    for run_dir in RUNS_DIR.iterdir():
        if not run_dir.is_dir():
            continue
        if niche_id and not run_dir.name.startswith(f"{niche_id}_"):
            continue
        report = run_dir / "run_report.json"
        if report.exists():
            mtime = report.stat().st_mtime
            if mtime > newest_time:
                newest_time = mtime
                newest_file = report
    if not newest_file:
        return None
    try:
        data = json.loads(newest_file.read_text())
        return data.get("end_time") or data.get("completed_at") or data.get("timestamp")
    except (json.JSONDecodeError, KeyError):
        return None


def _pipeline_status(niche_id: str) -> tuple[str, str | None]:
    """Check pipeline lock file to determine status. Returns (status, current_stage)."""
    lock_file = TMP_DIR / f"{niche_id}_pipeline.lock"
    if not lock_file.exists():
        return "idle", None

    age_seconds = time.time() - lock_file.stat().st_mtime
    if age_seconds > 1800:  # 30 minutes = stale
        # Auto-clean stale lock instead of showing permanent "error"
        try:
            lock_file.unlink(missing_ok=True)
            logger.warning(
                "Auto-cleaned stale pipeline lock for %s (age: %dm)",
                niche_id,
                int(age_seconds / 60),
            )
        except OSError:
            pass
        return "idle", None

    # Running — try to get current stage
    progress_file = TMP_DIR / f"{niche_id}_progress.json"
    stage = None
    if progress_file.exists():
        try:
            data = json.loads(progress_file.read_text())
            stage = data.get("stage")
        except (json.JSONDecodeError, KeyError):
            pass
    return "running", stage


def _platform_health_from_reports() -> dict[str, str]:
    """Check platform health from credentials + recent run reports.

    Platforms without configured credentials are marked 'not_configured'.
    Configured platforms start at 'ok' and get downgraded from run errors.
    """
    import os as _os

    # Determine which platforms have credentials configured
    configured = {
        "instagram": bool(_os.environ.get("META_IG_USER_ID", "").strip()),
        "youtube": bool(_os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()),
        "x_twitter": False,  # X/Twitter publishing is paused (requires paid plan)
        "facebook": bool(_os.environ.get("FB_PAGE_ACCESS_TOKEN", "").strip()),
        "tiktok": bool(
            _os.environ.get("TIKTOK_ACCESS_TOKEN", "").strip()
            and str(_os.environ.get("TIKTOK_AUDIT_APPROVED", "")).lower() == "true"
        ),
        "threads": bool(_os.environ.get("THREADS_ACCESS_TOKEN", "").strip()),
    }

    health = {}
    for platform in ["instagram", "youtube", "x_twitter", "facebook", "tiktok", "threads"]:
        health[platform] = "ok" if configured[platform] else "not_configured"

    if not RUNS_DIR.exists():
        return health

    # Downgrade configured platforms based on recent publish failures
    run_dirs = sorted(
        [d for d in RUNS_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for run_dir in run_dirs[:5]:
        report = run_dir / "run_report.json"
        if not report.exists():
            continue
        try:
            data = json.loads(report.read_text())
            pub_results = data.get("publish_results", {})
            if not pub_results:
                pub_summary = run_dir / "publish_all_summary.json"
                if pub_summary.exists():
                    pub_results = json.loads(pub_summary.read_text()).get("results", {})
            if isinstance(pub_results, dict):
                for platform in health:
                    if health[platform] == "not_configured":
                        continue  # Don't upgrade unconfigured platforms
                    result = pub_results.get(platform, {})
                    if isinstance(result, dict):
                        status = result.get("status", "")
                        if status in ("failed", "error"):
                            health[platform] = "error"
                        elif status == "degraded":
                            health[platform] = "degraded"
            if pub_results:
                break  # Use the most recent run with publish data
        except (json.JSONDecodeError, KeyError):
            continue
    return health


def _today_midnight_ist() -> datetime:
    now_ist = datetime.now(IST)
    return now_ist.replace(hour=0, minute=0, second=0, microsecond=0)


def _bp_niche_overview(fields: dict) -> str:
    """Lazy import wrapper for _bp_niche from analytics."""
    from server.api.analytics import _bp_niche

    return _bp_niche(fields)


def _build_overview() -> dict:
    registry = _load_registry()
    active_niches = [n for n in registry if n.get("status") in ("active", "mvp")]

    try:
        client = _get_client()
    except Exception as e:
        logger.error("Failed to get backlog client: %s", e)
        client = None

    today_start_ist = _today_midnight_ist()
    today_start_utc = today_start_ist.astimezone(UTC)
    today_str = datetime.now(IST).strftime("%Y-%m-%d")

    # Single API call — DRAFTED + VISUAL_READY + PUBLISHED covers
    # pending-review, published-today, and schedule.
    # Limit to 500 records to avoid unbounded fetches on large backlogs.
    all_records: list[dict] = []
    if client:
        try:
            all_records = client.blueprints.all(
                formula="OR({status}='DRAFTED',{status}='VISUAL_READY',{status}='PUBLISHED',{status}='ARCHIVED')",
                max_records=500,
            )
        except Exception as e:
            logger.warning("Failed to fetch blueprints for overview: %s", e)

    # Derive metrics from single result set
    pending_records = []
    today_records = []
    archived_records = []
    for r in all_records:
        fields = r.get("fields", {})
        status = fields.get("status", "")
        if status == "ARCHIVED":
            archived_records.append(r)
        elif status in ("DRAFTED", "VISUAL_READY"):
            action = fields.get("action_taken", "") or ""
            if not action.strip():
                pending_records.append(r)
        elif status == "PUBLISHED":
            # Check multiple date sources: published_at, reviewed_at, updated_at, created_at
            pub_at = (
                fields.get("published_at")
                or fields.get("reviewed_at")
                or fields.get("updated_at")
                or ""
            )
            if isinstance(pub_at, datetime):
                if pub_at >= today_start_utc:
                    today_records.append(r)
            elif isinstance(pub_at, str) and pub_at:
                try:
                    dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                    if dt >= today_start_utc:
                        today_records.append(r)
                except ValueError:
                    pass

    total_pending = len(pending_records)
    total_published_today = len(today_records)
    total_archived = len(archived_records)

    # Count all-time published from the fetched records (for pass_rate)
    total_published_all = sum(
        1 for r in all_records if r.get("fields", {}).get("status") == "PUBLISHED"
    )

    # Auto-archive breakdown by action_taken reason
    archive_by_reason: dict[str, int] = {}
    niche_archived: dict[str, int] = {}
    for r in archived_records:
        fields = r.get("fields", {})
        reason = (fields.get("action_taken") or "unknown").strip()
        archive_by_reason[reason] = archive_by_reason.get(reason, 0) + 1
        n = _bp_niche_overview(fields)
        niche_archived[n] = niche_archived.get(n, 0) + 1

    # Pass rate: all-time published / (published + archived) from same query window
    # Both counts come from the same result set so the ratio is meaningful.
    pass_rate = (
        round(total_published_all / (total_published_all + total_archived), 2)
        if (total_published_all + total_archived) > 0
        else None
    )

    # Supplement published_today from publishing_analytics (more accurate)
    if client:
        try:
            pub_analytics = client.publishing_analytics.all(
                formula="AND({status}='SUCCESS')",
                max_records=200,
            )
            for pa in pub_analytics:
                pa_fields = pa.get("fields", {})
                pa_created = pa_fields.get("created_at") or pa_fields.get("published_at")
                if isinstance(pa_created, datetime):
                    if pa_created >= today_start_utc:
                        # Count this as a today publish
                        n = pa_fields.get("niche_id", "")
                        if n:
                            found = False
                            for r in today_records:
                                if _bp_niche_overview(r.get("fields", {})) == n:
                                    found = True
                                    break
                            if not found:
                                # Create a synthetic record so the niche gets counted
                                today_records.append(
                                    {"fields": {"niche_id": n, "status": "PUBLISHED"}}
                                )
                elif isinstance(pa_created, str) and pa_created:
                    try:
                        dt = datetime.fromisoformat(pa_created.replace("Z", "+00:00"))
                        if dt >= today_start_utc:
                            n = pa_fields.get("niche_id", "")
                            if n:
                                found = False
                                for r in today_records:
                                    if _bp_niche_overview(r.get("fields", {})) == n:
                                        found = True
                                        break
                                if not found:
                                    today_records.append(
                                        {"fields": {"niche_id": n, "status": "PUBLISHED"}}
                                    )
                    except ValueError:
                        pass
        except Exception as e:
            logger.debug("publishing_analytics check failed: %s", e)

    total_published_today = len(today_records)

    # Build per-niche pending/published counts and best performer
    niche_pending: dict[str, int] = {}
    niche_published: dict[str, int] = {}
    niche_best: dict[str, dict] = {}
    for r in pending_records:
        n = _bp_niche_overview(r.get("fields", {}))
        niche_pending[n] = niche_pending.get(n, 0) + 1
    for r in today_records:
        fields = r.get("fields", {})
        n = _bp_niche_overview(fields)
        niche_published[n] = niche_published.get(n, 0) + 1
        try:
            score = float(fields.get("priority_score", 0) or 0)
        except (ValueError, TypeError):
            score = 0.0
        prev = niche_best.get(n)
        if prev is None or score > prev["score"]:
            niche_best[n] = {
                "title": (
                    fields.get("hook_text")
                    or fields.get("hook")
                    or fields.get("title")
                    or (fields.get("caption") or "")[:60]
                    or "Untitled"
                )[:60],
                "score": round(score, 2),
                "platform": "instagram",
            }

    # Build per-niche data (pipeline status is local — no API call)
    niches_data = []
    agents_running = 0
    for niche in active_niches:
        niche_id = niche["id"]
        pipe_status, current_stage = _pipeline_status(niche_id)
        last_run = _last_run_at(niche_id)
        if pipe_status == "running":
            agents_running += 1

        niches_data.append(
            {
                "id": niche_id,
                "display_name": niche.get("display_name", niche_id),
                "accent_hex": niche.get("accent_hex", "#6366f1"),
                "pipeline_status": pipe_status,
                "current_stage": current_stage,
                "last_run_at": last_run,
                "pending_review": niche_pending.get(niche_id, 0),
                "published_today": niche_published.get(niche_id, 0),
                "archived_today": niche_archived.get(niche_id, 0),
                "target_posts_per_day": niche.get("target_posts_per_day", 4),
                "best_performer_today": niche_best.get(niche_id),
            }
        )

    # Schedule today — derived from the same single query (dedup by record ID)
    schedule_today = []
    seen_schedule_ids: set[str] = set()
    for r in all_records:
        record_id = str(r.get("id", ""))
        if record_id in seen_schedule_ids:
            continue
        fields = r.get("fields", {})
        sched = fields.get("scheduled_for", "")
        if not sched or not isinstance(sched, str):
            continue
        if today_str not in sched:
            continue

        seen_schedule_ids.add(record_id)
        try:
            dt = datetime.fromisoformat(sched.replace("Z", "+00:00"))
            dt_ist = dt.astimezone(IST)
            slot_time = dt_ist.strftime("%H:%M")
        except ValueError:
            slot_time = "00:00"

        bp_status = fields.get("status", "")
        pub_status_raw = fields.get("platform_publish_status", "")
        item_status = "published" if bp_status == "PUBLISHED" else "scheduled"

        # Parse per-platform status (e.g., {"instagram": "PUBLISHED", "youtube": "FAILED"})
        platform_results: dict[str, str] = {}
        if isinstance(pub_status_raw, str) and pub_status_raw.strip():
            try:
                parsed = json.loads(pub_status_raw)
                if isinstance(parsed, dict):
                    platform_results = parsed
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(pub_status_raw, dict):
            platform_results = pub_status_raw

        platforms = (
            list(platform_results.keys())
            if platform_results
            else [
                "instagram",
                "youtube",
                "facebook",
            ]
        )

        item_niche = _bp_niche_overview(fields)

        schedule_today.append(
            {
                "id": record_id,
                "slot_time_ist": slot_time,
                "niche_id": item_niche,
                "title": (fields.get("hook") or fields.get("hook_text") or "Untitled")[:60],
                "status": item_status,
                "platforms": platforms,
                "platform_results": platform_results,
            }
        )
    schedule_today.sort(key=lambda x: x["slot_time_ist"])

    platform_health = _platform_health_from_reports()

    # Overlay Prefect-sourced pipeline statuses when available
    from server.api.pipeline import _merge_prefect_status, _prefect_healthy

    niches_data = _merge_prefect_status(niches_data)
    prefect_connected = _prefect_healthy()

    # Recount running agents after Prefect overlay may have changed statuses
    agents_running = sum(1 for n in niches_data if n.get("pipeline_status") == "running")

    # ── Aggregate engagement from analytics table ──
    total_reach = 0
    total_likes = 0
    total_comments = 0
    niche_daily_reach: dict[str, list[dict]] = {}
    try:
        import psycopg
        from psycopg.rows import dict_row

        _dsn = os.environ.get("DATABASE_URL", "")
        if _dsn:
            with psycopg.connect(_dsn, row_factory=dict_row) as _conn:
                # Aggregate totals
                agg = _conn.execute(
                    "SELECT "
                    "COALESCE(SUM(COALESCE((extra->>'reach')::numeric, 0)), 0)::bigint AS total_reach, "
                    "COALESCE(SUM(COALESCE((extra->>'likes')::numeric, 0)), 0)::bigint AS total_likes, "
                    "COALESCE(SUM(COALESCE((extra->>'comments')::numeric, 0)), 0)::bigint AS total_comments "
                    "FROM analytics "
                    "WHERE niche_id NOT LIKE 'rls_test%%' AND niche_id NOT LIKE 'test_%%' "
                    "AND collected_at > NOW() - INTERVAL '30 days'"
                ).fetchone()
                if agg:
                    total_reach = agg["total_reach"]
                    total_likes = agg["total_likes"]
                    total_comments = agg["total_comments"]

                # Per-niche daily reach (last 14 days) for sparklines
                daily_rows = _conn.execute(
                    "SELECT niche_id, collected_at::date AS day, "
                    "COALESCE(SUM(COALESCE((extra->>'reach')::numeric, 0)), 0)::bigint AS daily_reach "
                    "FROM analytics "
                    "WHERE niche_id NOT LIKE 'rls_test%%' AND niche_id NOT LIKE 'test_%%' "
                    "AND collected_at >= NOW() - INTERVAL '14 days' "
                    "GROUP BY niche_id, day ORDER BY day"
                ).fetchall()
                for row in daily_rows:
                    nid = row["niche_id"]
                    niche_daily_reach.setdefault(nid, []).append(
                        {
                            "date": row["day"].isoformat(),
                            "reach": int(row["daily_reach"]),
                        }
                    )
    except Exception as exc:
        logger.debug("analytics aggregate for overview failed: %s", exc)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "prefect_connected": prefect_connected,
        "global": {
            "total_pending_review": total_pending,
            "total_published_today": total_published_today,
            "total_archived": total_archived,
            "auto_archive_today": {
                "total": total_archived,
                "by_reason": archive_by_reason,
                "pass_rate": pass_rate,
                "pass_rate_note": "all-time published / (published + archived) from bounded query",
            },
            "agents_running": agents_running,
            "platform_health": platform_health,
            "total_reach": total_reach,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "configured_platform_count": sum(
                1 for s in platform_health.values() if s != "not_configured"
            ),
        },
        "niches": niches_data,
        "schedule_today": schedule_today,
        "niche_daily_reach": niche_daily_reach,
    }


@bp.route("/overview", methods=["GET"])
def cross_niche_overview():
    now = time.time()
    if _overview_cache["data"] and (now - _overview_cache["ts"]) < _CACHE_TTL:
        return api_success(data=_overview_cache["data"])

    try:
        data = _build_overview()
        _overview_cache["data"] = data
        _overview_cache["ts"] = now
        return api_success(data=data)
    except Exception as e:
        logger.error("Cross-niche overview error: %s", e, exc_info=True)
        return api_error(error="Failed to build overview", code=502)
