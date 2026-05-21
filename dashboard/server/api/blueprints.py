"""Blueprint resource API endpoints."""

import json
import logging
import os
import re
import subprocess
import time as _time
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint, request

from server.core.responses import api_error, api_not_found, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("blueprints_api", __name__, url_prefix="/api/v1/blueprints")

# ── Review queue cache (stale-while-revalidate) ──────────────────
_review_queue_cache: dict[str, dict] = {}  # niche_id → {data, ts}
_REVIEW_QUEUE_TTL = 30  # seconds

# ── Story thumbnail cache (avoids repeated SharePoint lookups) ────
# Capped at 500 entries to prevent unbounded growth (D19)
_story_thumbnail_cache: dict[str, str | None] = {}  # story_link ID → thumbnail URL or None
_STORY_CACHE_MAX = 500

# ── Video duration cache (avoids repeated ffprobe subprocess calls) ────
# Capped at 200 entries (D20)
_duration_cache: dict[str, float] = {}  # file path → duration in seconds
_DURATION_CACHE_MAX = 200

RECORD_RE = re.compile(r"^[\w-]+$")  # Integer (SharePoint) or UUID (Postgres)
_YT_URL_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)"
    r"([a-zA-Z0-9_-]{11})"
)
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent.parent
# GENLAB_PROJECT_ROOT defaults to the GenLab workspace root (parent of dashboard/)
PROJECT_ROOT = Path(
    os.environ.get(
        "GENLAB_PROJECT_ROOT",
        str(_DASHBOARD_ROOT.parent),
    )
)


def _get_client():
    from server.core.graph_sync import get_sync_client

    return get_sync_client()


def _extract_youtube_id(url: str) -> str:
    """Extract an 11-char YouTube video ID from a URL, or return empty string."""
    if not url:
        return ""
    m = _YT_URL_RE.search(url)
    return m.group(1) if m else ""


def _local_path_to_media_url(filepath: str) -> str | None:
    """Convert an absolute local path to a /api/media/ URL.

    Returns None if the file doesn't exist or is empty (0 bytes),
    preventing 404s for cleaned-up artifacts.

    Tries PROJECT_ROOT (GenLab workspace root) first, then the explicit
    GenLab root as a fallback (handles legacy absolute paths that predate
    the env-var-driven root).
    """
    p = Path(filepath)
    try:
        if not p.exists() or p.stat().st_size == 0:
            return None
        # Try PROJECT_ROOT (GenLab workspace root or env-var override)
        try:
            rel = p.relative_to(PROJECT_ROOT)
            return f"/api/media/{rel}"
        except ValueError:
            pass
        # Fall back to explicit GenLab workspace root
        genlab_root = _DASHBOARD_ROOT.parent
        rel = p.relative_to(genlab_root)
        return f"/api/media/{rel}"
    except (ValueError, OSError):
        return None


def _safe_json_parse(raw, expected_type=None, fallback=None):
    """Parse JSON robustly: handles whitespace, double-encoding, 'null' strings."""
    if not isinstance(raw, str):
        return raw if raw is not None else fallback
    s = raw.strip()
    if not s or s == "null":
        return fallback
    try:
        result = json.loads(s)
        # Handle double-encoded JSON strings
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                pass
        if expected_type and not isinstance(result, expected_type):
            return fallback
        return result
    except (json.JSONDecodeError, TypeError):
        return fallback


def _probe_video_duration(filepath: str) -> float | None:
    """Get video duration in seconds via ffprobe. Returns None on error.

    Results are cached in _duration_cache because rendered video files
    don't change after creation — safe to cache indefinitely.
    """
    if filepath in _duration_cache:
        return _duration_cache[filepath]
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                filepath,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            dur = float(result.stdout.strip())
            _duration_cache[filepath] = dur
            return dur
    except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
        pass
    return None


def _prefetch_story_thumbnails(records: list[dict]) -> None:
    """Batch-fetch story thumbnails for all blueprints that need them.

    Populates _story_thumbnail_cache so that _transform_media() never
    makes per-blueprint story lookups (eliminates N+1 queries).
    """
    # Collect story_ids that need lookup (no visual_paths AND have story_link AND not cached)
    needed_ids: set[str] = set()
    for r in records:
        fields = r.get("fields", {})
        # Mirror the condition in _transform_media: only look up story when
        # there are no visual_paths and no thumbnail_url
        vp_raw = fields.get("visual_paths")
        paths = _safe_json_parse(vp_raw, expected_type=list)
        has_media = False
        if paths:
            for p in paths:
                pp = Path(p)
                try:
                    if pp.exists() and pp.stat().st_size > 0:
                        has_media = True
                        break
                except OSError:
                    pass
        if has_media:
            continue
        thumb = fields.get("thumbnail_url")
        if thumb and isinstance(thumb, str) and thumb.startswith("http"):
            continue
        story_id = fields.get("story_link")
        if story_id:
            sid = str(story_id)
            if sid not in _story_thumbnail_cache:
                needed_ids.add(sid)

    if not needed_ids:
        return

    # Batch-fetch all needed stories in one query
    try:
        client = _get_client()
        # Build OR formula: OR({story_id}='id1',{story_id}='id2',...)
        # Cap at 50 to avoid overly long formulas
        id_list = list(needed_ids)[:50]
        or_parts = ",".join(f"{{story_id}}='{sid}'" for sid in id_list)
        formula = f"OR({or_parts})" if len(id_list) > 1 else f"{{story_id}}='{id_list[0]}'"
        story_records = client.stories.all(formula=formula)

        # Index by story_id for fast lookup
        story_map: dict[str, dict] = {}
        for sr in story_records:
            sf = sr.get("fields", {})
            sid = str(sr.get("id", ""))
            story_map[sid] = sf
            # Also index by story_id field value if present
            sf_story_id = sf.get("story_id")
            if sf_story_id:
                story_map[str(sf_story_id)] = sf

        # Evict oldest entries if cache exceeds max size
        if len(_story_thumbnail_cache) > _STORY_CACHE_MAX:
            keys = list(_story_thumbnail_cache.keys())
            for k in keys[: len(keys) // 2]:
                del _story_thumbnail_cache[k]

        # Populate cache for each needed ID
        for sid in needed_ids:
            if sid in _story_thumbnail_cache:
                continue  # already cached
            story_fields = story_map.get(sid)
            if story_fields:
                story_thumb = story_fields.get("thumbnail_url", "")
                if story_thumb and isinstance(story_thumb, str) and story_thumb.startswith("http"):
                    _story_thumbnail_cache[sid] = story_thumb
                else:
                    vid = story_fields.get("video_id", "")
                    if not vid:
                        vid = _extract_youtube_id(story_fields.get("url", ""))
                    if vid:
                        _story_thumbnail_cache[sid] = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                    else:
                        _story_thumbnail_cache[sid] = None
            else:
                _story_thumbnail_cache[sid] = None
    except Exception:
        logger.debug("Batch story prefetch failed, falling back to per-item lookup")
        # Don't populate cache on failure — _transform_media will do individual lookups


def _transform_media(fields: dict, *, lite: bool = False) -> dict:
    """Parse JSON media fields and convert local paths to media URLs.

    Args:
        lite: When True, only resolve visual_paths to URLs — skip expensive
              operations (ffprobe, review_server import, landscape siblings,
              slide_previews rebuild). Used by the publishing queue endpoint.
    """
    # Parse visual_paths: JSON array of local paths → media URLs list
    all_media_urls: list[str] = []
    vp_raw = fields.get("visual_paths")
    paths = _safe_json_parse(vp_raw, expected_type=list)
    if paths:
        for p in paths:
            url = _local_path_to_media_url(p)
            if url:
                all_media_urls.append(url)
        fields["visual_paths"] = all_media_urls[0] if all_media_urls else None

        if not lite:
            # Expose landscape video URL separately for Twitter/Facebook previews
            landscape_urls = [u for u in all_media_urls if "_landscape" in u]
            # B2 disk fallback: if no landscape URL found, check if _landscape.mp4 sibling exists on disk
            if not landscape_urls:
                for p in paths:
                    if isinstance(p, str) and p.endswith(".mp4") and "_landscape" not in p:
                        sibling = Path(p).parent / (Path(p).stem + "_landscape.mp4")
                        if sibling.exists():
                            sib_url = _local_path_to_media_url(str(sibling))
                            if sib_url:
                                landscape_urls = [sib_url]
                                all_media_urls.append(sib_url)
                            break
            fields["landscape_video_url"] = landscape_urls[0] if landscape_urls else None

    if not all_media_urls:
        # Prefer thumbnail_url (YouTube CDN) — works even after local files are
        # cleaned up.  /api/video/<id> triggers a SharePoint lookup + file check
        # that also 404s when renders are purged, so thumbnail is more reliable.
        thumb = fields.get("thumbnail_url")
        if thumb and isinstance(thumb, str) and thumb.startswith("http"):
            all_media_urls.append(thumb)
            fields["visual_paths"] = thumb

        # Story-linked thumbnail fallback: look up the linked story's thumbnail
        # or construct one from its YouTube video_id or URL.
        if not all_media_urls and fields.get("story_link"):
            story_id = str(fields["story_link"])
            if story_id not in _story_thumbnail_cache:
                try:
                    story_rec = _get_client().stories.get(story_id)
                    story_fields = story_rec.get("fields", {})
                    story_thumb = story_fields.get("thumbnail_url", "")
                    if (
                        story_thumb
                        and isinstance(story_thumb, str)
                        and story_thumb.startswith("http")
                    ):
                        _story_thumbnail_cache[story_id] = story_thumb
                    else:
                        # Try explicit video_id first, then extract from URL
                        vid = story_fields.get("video_id", "")
                        if not vid:
                            vid = _extract_youtube_id(story_fields.get("url", ""))
                        if vid:
                            _story_thumbnail_cache[story_id] = (
                                f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                            )
                        else:
                            _story_thumbnail_cache[story_id] = None
                except Exception:
                    logger.debug("Story lookup failed for story_link=%s", story_id)
                    _story_thumbnail_cache[story_id] = None

            cached_thumb = _story_thumbnail_cache.get(story_id)
            if cached_thumb:
                all_media_urls.append(cached_thumb)
                fields["visual_paths"] = cached_thumb

        if not all_media_urls and fields.get("id"):
            if lite:
                fallback_url = f"/api/video/{fields['id']}"
                all_media_urls.append(fallback_url)
                fields["visual_paths"] = fallback_url
            else:
                try:
                    from server.review_server import _resolve_video_url

                    fallback_url = _resolve_video_url(fields)
                    if fallback_url:
                        all_media_urls.append(fallback_url)
                        fields["visual_paths"] = fallback_url
                except ImportError:
                    pass

    # Preserve all media URLs for components that need the full set (Fix B4)
    fields["all_media_urls"] = all_media_urls

    if not lite:
        # Parse slide_previews: JSON array of attachment objects
        sp_raw = fields.get("slide_previews")
        needs_rebuild = False
        slides = _safe_json_parse(sp_raw, expected_type=list)
        if slides:
            fields["slide_previews"] = slides
        elif not sp_raw and all_media_urls:
            needs_rebuild = True
        else:
            needs_rebuild = not sp_raw and bool(all_media_urls)

        # Rebuild slide_previews from visual_paths when CDN is expired or missing
        if needs_rebuild and all_media_urls:
            fields["slide_previews"] = [{"url": u} for u in all_media_urls]

    # Parse platform_publish_status: JSON string → dict
    pps_raw = fields.get("platform_publish_status")
    pps = _safe_json_parse(pps_raw, expected_type=dict)
    if pps:
        # Normalize: some values are strings, some are {"status": ..., "post_id": ...}
        normalized = {}
        details = {}
        for platform, val in pps.items():
            if isinstance(val, dict):
                normalized[platform] = val.get("status", "UNKNOWN")
                details[platform] = val
            else:
                normalized[platform] = val
                details[platform] = {"status": val}
        fields["platform_publish_status"] = normalized
        fields["platform_publish_details"] = details

    if not lite:
        # Parse twitter_content / youtube_content / facebook_content: JSON strings → dicts
        for json_field in ("twitter_content", "youtube_content", "facebook_content"):
            raw = fields.get(json_field)
            parsed = _safe_json_parse(raw, expected_type=dict)
            if parsed is not None:
                fields[json_field] = parsed

        # Extract video_duration from the primary video file if not already set
        if not fields.get("video_duration") and paths:
            for p in paths:
                if (
                    isinstance(p, str)
                    and p.endswith((".mp4", ".mov", ".webm"))
                    and "_landscape" not in p
                ):
                    dur = _probe_video_duration(p)
                    if dur is not None and dur > 0:
                        fields["video_duration"] = round(dur, 1)
                    break

    return fields


def _paginate(records, page, per_page):
    total = len(records)
    start = (page - 1) * per_page
    return records[start : start + per_page], {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": max(1, (total + per_page - 1) // per_page),
    }


# Frontend sort values → (field_name, descending)
SORT_MAP = {
    "newest": ("id", True),
    "priority": ("priority_score", True),
    "cost": ("generation_cost_usd", True),
    "scheduled": ("scheduled_for", True),
}


@bp.route("/review-queue", methods=["GET"])
def review_queue():
    """Dedicated review queue: VISUAL_READY items sorted by priority_score desc.

    When niche_id is "all" or omitted, returns items from ALL niches.
    When no pending items exist, falls back to the last 5 published items
    so the panel is never blank.

    Uses a short-lived server-side cache (30s) so transient MS Graph
    failures return stale data instead of 502.
    """
    niche_id = request.args.get("niche_id", "all")
    try:
        limit = min(int(request.args.get("limit", 50)), 100)
    except (ValueError, TypeError):
        limit = 50

    client = _get_client()

    # Use OR to match both empty string and BLANK() for unreviewed items
    formula = "AND({status}='VISUAL_READY',OR({action_taken}='',{action_taken}=BLANK()))"
    try:
        records = client.blueprints.all(formula=formula)
        # Deduplicate by record ID — Graph API pagination can return duplicates
        seen_ids: set[str] = set()
        unique_records = []
        for r in records:
            rid = str(r.get("id", ""))
            if rid not in seen_ids:
                seen_ids.add(rid)
                unique_records.append(r)
        records = unique_records
        # Filter by niche_id client-side (OData on SharePoint Lists is unreliable)
        # Skip filter when niche_id is "all" — show everything
        if niche_id != "all":
            records = [
                r
                for r in records
                if (r.get("fields", {}).get("niche_id") or "ai_creators") == niche_id
            ]
    except Exception as e:
        logger.error("Failed to fetch review queue: %s", e)
        # Serve stale cache instead of 502
        cached = _review_queue_cache.get(niche_id)
        if cached:
            logger.info("Serving stale review queue cache for niche_id=%s", niche_id)
            return api_success(data=cached["data"])
        return api_error(error="Failed to fetch review queue", code=502)

    # Sort by priority_score descending
    def _priority(r):
        try:
            return float(r.get("fields", {}).get("priority_score", 0) or 0)
        except (ValueError, TypeError):
            return 0.0

    records.sort(key=_priority, reverse=True)
    records = records[:limit]

    # Batch-prefetch story thumbnails to avoid N+1 per-blueprint lookups
    _prefetch_story_thumbnails(records)

    items = [_transform_media({"id": r["id"], **r.get("fields", {})}) for r in records]

    # Fallback: when no pending items, return last 5 published items
    fallback = False
    if not items:
        try:
            published = client.get_blueprints_by_status("PUBLISHED")
            if niche_id != "all":
                published = [
                    r
                    for r in published
                    if (r.get("fields", {}).get("niche_id") or "ai_creators") == niche_id
                ]
            published.sort(key=_priority, reverse=True)
            _prefetch_story_thumbnails(published[:5])
            items = [
                _transform_media({"id": r["id"], **r.get("fields", {})}) for r in published[:5]
            ]
            fallback = len(items) > 0
        except Exception as e:
            logger.warning("Failed to fetch published fallback: %s", e)

    response_body = {
        "data": items,
        "meta": {
            "total": len(items),
            "niche_id": niche_id,
            "fallback": fallback,
        },
    }

    # Update cache
    _review_queue_cache[niche_id] = {"data": response_body, "ts": _time.time()}

    return api_success(data=response_body)


@bp.route("/<record_id>/review-action", methods=["PATCH"])
def review_action(record_id):
    """Focus Review action endpoint — same logic as review but PATCH semantics."""
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    data = request.json or {}
    action = data.get("action", "")
    VALID_ACTIONS = {
        "approve": "approved",
        "approved": "approved",
        "reject": "rejected",
        "rejected": "rejected",
        "revise": "revised",
        "revised": "revised",
        "skip": "skipped",
        "skipped": "skipped",
    }
    if action not in VALID_ACTIONS:
        return api_error(error=f"Invalid action: {action}")
    action_taken = VALID_ACTIONS[action]
    try:
        from server.review_server import _execute_review_action

        _execute_review_action(
            record_id,
            action_taken,
            notes=data.get("notes", ""),
            feedback_issue=data.get("issue", ""),
            feedback_notes=data.get("notes", ""),
            feedback_fix=data.get("fix", ""),
        )

        # Store editorial metadata for approved blueprints (YouTube AI policy compliance)
        if action_taken == "approved":
            import json as _json
            from datetime import datetime

            editorial = {
                "reviewed_by": "editorial",
                "reviewed_at": datetime.now(UTC).isoformat(),
                "editorial_note": data.get("notes", "") or data.get("editorial_note", ""),
                "hook_modified": bool(data.get("hook_override")),
            }
            if data.get("hook_override"):
                editorial["hook_override"] = data["hook_override"]
            try:
                client = _get_client()
                bp = client.blueprints.get(record_id)
                if bp:
                    existing_extra = bp.get("fields", bp).get("extra", {})
                    if isinstance(existing_extra, str):
                        existing_extra = _json.loads(existing_extra) if existing_extra else {}
                    existing_extra["editorial"] = editorial
                    client.blueprints.update(record_id, {"extra": _json.dumps(existing_extra)})
            except Exception:
                logger.debug("Editorial metadata write failed for %s (non-blocking)", record_id)

        # Emit socket event for live badge updates
        try:
            from server.review_server import socketio

            socketio.emit("blueprint_updated", {"id": record_id, "action": action_taken})
        except Exception:
            pass
        return api_success(data={"status": "ok", "action": action_taken, "id": record_id})
    except Exception as exc:
        logger.error("Review action failed for %s: %s", record_id, exc)
        return api_error(error=f"Failed: {exc}", code=500)


@bp.route("", methods=["GET"])
def list_blueprints():
    niche_id = request.args.get("niche_id", "all")
    status = request.args.get("status")
    search = (request.args.get("search") or request.args.get("q") or "").lower()
    sort_param = request.args.get("sort", "newest")
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(int(request.args.get("per_page", 25)), 100)
    except (ValueError, TypeError):
        return api_error(error="Invalid page or per_page parameter")

    action_taken = request.args.get("action_taken")
    unscheduled = request.args.get("unscheduled")

    # SCHEDULED is a virtual status — the pipeline never sets status=SCHEDULED.
    # Posts are "scheduled" when they have a scheduled_for date while in
    # VISUAL_READY or DRAFTED status.  We translate the virtual filter into
    # a real status query + post-fetch filter on scheduled_for.
    filter_scheduled = False

    filters = []
    if status:
        statuses = [s.strip() for s in status.split(",") if s.strip()]

        if "SCHEDULED" in statuses:
            filter_scheduled = True
            # Replace virtual SCHEDULED with VISUAL_READY (only render-complete posts are truly scheduled)
            statuses = [s for s in statuses if s != "SCHEDULED"]
            statuses.append("VISUAL_READY")
            # Deduplicate while preserving order
            seen = set()
            statuses = [s for s in statuses if not (s in seen or seen.add(s))]  # type: ignore[func-returns-value]

        if len(statuses) == 1:
            filters.append(f"{{status}}='{statuses[0]}'")
        else:
            or_parts = ",".join(f"{{status}}='{s}'" for s in statuses)
            filters.append(f"OR({or_parts})")
    else:
        include_archived = request.args.get("include_archived", "").lower() in ("true", "1")
        include_published = request.args.get("include_published", "").lower() in ("true", "1")
        if include_archived:
            filters.append(
                "OR({status}='INTEL_READY',{status}='DRAFTED',"
                "{status}='VISUAL_READY',{status}='PUBLISHED',{status}='ARCHIVED')"
            )
        elif include_published:
            filters.append(
                "OR({status}='INTEL_READY',{status}='DRAFTED',"
                "{status}='VISUAL_READY',{status}='PUBLISHED')"
            )
        else:
            # Default: exclude PUBLISHED from content review (not actionable)
            filters.append("OR({status}='INTEL_READY',{status}='DRAFTED',{status}='VISUAL_READY')")

    # P3.12: Validate action_taken against allowlist to prevent OData injection
    ALLOWED_ACTIONS = {"approved", "rejected", "revised", "skipped"}
    AUTO_ARCHIVE_RE = re.compile(r"^auto_archived_[a-z_]+$")

    if action_taken:
        if action_taken not in ALLOWED_ACTIONS and not AUTO_ARCHIVE_RE.match(action_taken):
            return api_error(error=f"Invalid action_taken: {action_taken}")
        filters.append(f"{{action_taken}}='{action_taken}'")

    formula = "AND(" + ",".join(filters) + ")" if filters else ""
    try:
        records = _get_client().blueprints.all(formula=formula)
        # Filter by niche_id client-side (OData on SharePoint Lists is unreliable)
        # Skip filter when niche_id is "all" or empty — show everything
        if niche_id and niche_id != "all":
            records = [
                r
                for r in records
                if (r.get("fields", {}).get("niche_id") or "ai_creators") == niche_id
            ]
    except Exception as e:
        logger.error("Failed to fetch blueprints: %s", e)
        return api_error(error="Failed to fetch blueprints", code=502)

    # Filter unscheduled: only blueprints without a scheduled_for value
    if unscheduled and unscheduled.lower() in ("true", "1"):
        records = [r for r in records if not r.get("fields", {}).get("scheduled_for")]

    # Virtual SCHEDULED filter: only keep approved posts with a scheduled_for date
    if filter_scheduled:
        records = [
            r
            for r in records
            if r.get("fields", {}).get("scheduled_for")
            and r.get("fields", {}).get("action_taken") == "approved"
        ]

    # Exclude approved+scheduled posts ONLY from the default "no status filter"
    # view — they belong under the SCHEDULED tab. When an explicit status is
    # requested (e.g. VISUAL_READY, DRAFTED), show ALL matching items so the
    # review queue isn't misleadingly empty.
    if not filter_scheduled and not status:
        records = [
            r
            for r in records
            if not (
                r.get("fields", {}).get("status") == "VISUAL_READY"
                and r.get("fields", {}).get("action_taken") == "approved"
                and r.get("fields", {}).get("scheduled_for")
            )
        ]

    if search:
        records = [
            r
            for r in records
            if search in (r.get("fields", {}).get("hook_text", "") or "").lower()
            or search in (r.get("fields", {}).get("caption", "") or "").lower()
        ]

    # Map frontend sort values to actual field names
    if sort_param in SORT_MAP:
        sort_field, desc = SORT_MAP[sort_param]
    else:
        desc = sort_param.startswith("-")
        sort_field = sort_param.lstrip("-")

    def _sort_key(r):
        if sort_field == "id":
            return str(r.get("id", ""))
        if sort_field in ("priority_score", "generation_cost_usd"):
            try:
                return float(r.get("fields", {}).get(sort_field, 0) or 0)
            except (ValueError, TypeError):
                return 0.0
        return str(r.get("fields", {}).get(sort_field, "") or "")

    records.sort(key=_sort_key, reverse=desc)

    page_data, meta = _paginate(records, page, per_page)
    # Batch-prefetch story thumbnails for only the current page (not all records)
    _prefetch_story_thumbnails(page_data)
    return api_success(
        data={
            "data": [_transform_media({"id": r["id"], **r.get("fields", {})}) for r in page_data],
            "meta": meta,
        }
    )


@bp.route("/<record_id>", methods=["GET"])
def get_blueprint(record_id):
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    try:
        r = _get_client().blueprints.get(record_id)
        return api_success(data={"data": _transform_media({"id": r["id"], **r.get("fields", {})})})
    except Exception:
        return api_not_found(message="Not found")


@bp.route("/<record_id>/review", methods=["POST"])
def review_blueprint(record_id):
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    data = request.json or {}
    action = data.get("action", "")
    # Accept both imperative ("approve") and past tense ("approved") forms
    VALID_ACTIONS = {
        "approve": "approved",
        "approved": "approved",
        "reject": "rejected",
        "rejected": "rejected",
        "revise": "revised",
        "revised": "revised",
        "skip": "skipped",
        "skipped": "skipped",
    }
    if action not in VALID_ACTIONS:
        return api_error(error=f"Invalid action: {action}")
    action_taken = VALID_ACTIONS[action]
    # Persist the review decision to backlog — uses shared helper from review_server
    try:
        from server.review_server import _execute_review_action

        _execute_review_action(
            record_id,
            action_taken,
            notes=data.get("notes", ""),
            feedback_issue=data.get("issue", ""),
            feedback_notes=data.get("notes", ""),
            feedback_fix=data.get("fix", ""),
        )
        logger.info("Review action '%s' persisted for blueprint %s", action_taken, record_id)
        return api_success(data={"status": "ok", "action": action_taken, "record_id": record_id})
    except Exception as exc:
        logger.error("Failed to persist review action for %s: %s", record_id, exc)
        return api_error(error=f"Failed to persist review: {exc}", code=500)


@bp.route("/batch-review", methods=["POST"])
def batch_review():
    data = request.json or {}
    ids = data.get("ids", [])
    action = data.get("action", "")
    if not ids:
        return api_error(error="No blueprint IDs provided")
    VALID_BATCH = {
        "approve": "approved",
        "approved": "approved",
        "reject": "rejected",
        "rejected": "rejected",
    }
    if action not in VALID_BATCH:
        return api_error(error=f"Invalid action: {action}")
    action_taken = VALID_BATCH[action]

    client = _get_client()
    results = []
    for rid in ids:
        if not RECORD_RE.match(str(rid)):
            results.append({"id": rid, "status": "error", "error": "Invalid ID"})
            continue
        try:
            fields = {
                "action_taken": action_taken,
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
            # Auto-schedule approved blueprints
            if action_taken == "approved":
                from server.core.publishing_queue import _next_available_slot

                try:
                    bp_data = client.blueprints.get(str(rid))
                    existing_sched = bp_data.get("fields", {}).get("scheduled_for") or ""
                    niche_id = (bp_data.get("fields", {}).get("niche_id") or "").strip()
                except Exception:
                    existing_sched, niche_id = "", ""
                if not existing_sched:
                    slot = _next_available_slot(niche_id=niche_id)
                    if slot:
                        fields["scheduled_for"] = slot
            client.blueprints.update(str(rid), fields, typecast=True)
            results.append({"id": rid, "action": action_taken, "status": "ok"})
        except Exception as exc:
            logger.error("Batch review failed for %s: %s", rid, exc)
            results.append({"id": rid, "status": "error", "error": str(exc)})
    return api_success(data={"data": results})


@bp.route("/<blueprint_id>/approve-and-schedule", methods=["POST"])
def approve_and_schedule(blueprint_id):
    """Approve a blueprint and auto-schedule to the next available slot."""
    if not RECORD_RE.match(str(blueprint_id)):
        return api_error(error="Invalid blueprint ID")
    try:
        client = _get_client()
        record = client.blueprints.get(blueprint_id)
        if not record:
            return api_not_found(message=f"Blueprint {blueprint_id} not found")

        fields = record.get("fields", {})
        niche_id = (fields.get("niche_id") or "").strip()

        # Find next available slot (reuse existing scheduling logic)
        from server.core.publishing_queue import _next_available_slot

        scheduled_for = _next_available_slot(niche_id=niche_id)

        update_fields = {
            "action_taken": "approved",
            "reviewed_at": datetime.now(UTC).isoformat(),
        }
        if scheduled_for:
            update_fields["scheduled_for"] = scheduled_for

        client.blueprints.update(blueprint_id, update_fields, typecast=True)

        return api_success(
            data={
                "id": blueprint_id,
                "status": "ok",
                "scheduled_for": scheduled_for,
            }
        )
    except Exception as e:
        logger.error("approve-and-schedule failed for %s: %s", blueprint_id, e)
        return api_error(error=str(e), code=500)


@bp.route("/batch-approve-schedule", methods=["POST"])
def batch_approve_schedule():
    """Approve and auto-schedule multiple blueprints."""
    body = request.get_json(silent=True) or {}
    ids = body.get("ids", [])
    if not ids:
        return api_error(error="No blueprint IDs provided")

    from server.core.publishing_queue import _next_available_slot

    client = _get_client()
    results = []
    for bp_id in ids:
        if not RECORD_RE.match(str(bp_id)):
            results.append({"id": bp_id, "status": "error", "error": "Invalid ID"})
            continue
        try:
            record = client.blueprints.get(str(bp_id))
            if not record:
                results.append({"id": bp_id, "status": "error", "error": "Not found"})
                continue
            fields = record.get("fields", {})
            niche_id = (fields.get("niche_id") or "").strip()
            scheduled_for = _next_available_slot(niche_id=niche_id)

            update_fields = {
                "action_taken": "approved",
                "reviewed_at": datetime.now(UTC).isoformat(),
            }
            if scheduled_for:
                update_fields["scheduled_for"] = scheduled_for

            client.blueprints.update(str(bp_id), update_fields, typecast=True)
            results.append({"id": bp_id, "status": "ok", "scheduled_for": scheduled_for})
        except Exception as e:
            logger.warning("batch approve-schedule failed for %s: %s", bp_id, e)
            results.append({"id": bp_id, "status": "error", "error": str(e)})

    return api_success(data={"data": results})


@bp.route("/<record_id>/schedule", methods=["PATCH"])
def reschedule(record_id):
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    data = request.json or {}
    scheduled_for = data.get("scheduled_for")
    if not scheduled_for:
        return api_error(error="scheduled_for is required")

    # Collision check: parse scheduled_for into date+slot and check occupancy (per-niche)
    from server.api.schedule import IST, _check_slot_collision, _parse_datetime

    dt = _parse_datetime(scheduled_for)
    if dt is not None:
        if dt.tzinfo is not None:
            dt_ist = dt.astimezone(IST)
        else:
            dt_ist = dt.replace(tzinfo=UTC).astimezone(IST)
        target_date = dt_ist.strftime("%Y-%m-%d")
        target_slot = dt_ist.strftime("%H:%M")
        # Look up the blueprint's niche_id for niche-scoped collision check
        try:
            bp_record = _get_client().blueprints.get(record_id)
            bp_niche = (
                (bp_record.get("fields", {}).get("niche_id", "") or "").strip() if bp_record else ""
            )
        except Exception:
            bp_niche = ""
        occupant = _check_slot_collision(
            _get_client(),
            target_date,
            target_slot,
            exclude_blueprint_id=record_id,
            niche_id=bp_niche,
        )
        if occupant:
            return api_error(
                error=f"Slot {target_date} {target_slot} IST is already occupied by blueprint {occupant['id']}",
                code=409,
            )

    try:
        _get_client().blueprints.update(record_id, {"scheduled_for": scheduled_for})
        return api_success(data={"status": "ok", "scheduled_for": scheduled_for})
    except Exception as e:
        logger.error("Blueprint schedule update failed: %s", e, exc_info=True)
        return api_error(error="Failed to update blueprint", code=500)


@bp.route("/<record_id>/content", methods=["PATCH"])
def update_content(record_id):
    if not RECORD_RE.match(record_id):
        return api_error(error="Invalid record ID")
    data = request.json or {}
    allowed = {"hook_text", "caption", "hashtags"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return api_error(error="No valid fields to update")
    try:
        _get_client().blueprints.update(record_id, updates)
        return api_success(data={"status": "ok", "updated_fields": list(updates.keys())})
    except Exception as e:
        logger.error("Blueprint content update failed: %s", e, exc_info=True)
        return api_error(error="Failed to update blueprint", code=500)


# ── Health check ──────────────────────────────────────────────────

# Separate blueprint for health so it lives at /api/health/focus-review
health_bp = Blueprint("health_focus_review", __name__, url_prefix="/api/health")


@health_bp.route("/focus-review", methods=["GET"])
def focus_review_health():
    """Health check for Focus Review data flow.

    Returns pending_review_count, list_id, and status so operators
    can diagnose empty panels without reading code.
    """
    try:
        client = _get_client()
        formula = "AND({status}='VISUAL_READY',OR({action_taken}='',{action_taken}=BLANK()))"
        records = client.blueprints.all(formula=formula)
        count = len(records)

        # Get list_id from the underlying proxy
        proxy = client.blueprints
        # ScheduleGuardedProxy wraps a GraphTableProxy
        inner = getattr(proxy, "_proxy", proxy)
        list_id = getattr(inner, "_list_id", "unknown")

        return api_success(
            data={
                "status": "ok" if count > 0 else "empty",
                "pending_review_count": count,
                "last_checked": datetime.now(UTC).isoformat(),
                "list_id": list_id,
            }
        )
    except Exception as e:
        logger.error("Focus review health check failed: %s", e)
        return api_error(error=str(e), code=502)
