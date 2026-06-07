"""Schedule API endpoints."""

import json as _json
import logging
import os
import re
from datetime import UTC, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import yaml
from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("schedule_api", __name__, url_prefix="/api/v1/schedule")
RECORD_RE = re.compile(r"^[\w-]+$")  # Integer (SharePoint) or UUID (Postgres)
SLOT_RE = re.compile(r"^\d{1,2}:\d{2}$")  # HH:MM format

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent.parent
# GENLAB_PROJECT_ROOT defaults to the GenLab workspace root (parent of dashboard/)
PROJECT_ROOT = Path(
    os.environ.get(
        "GENLAB_PROJECT_ROOT",
        str(_DASHBOARD_ROOT.parent),
    )
)

# IST offset (UTC+5:30, no DST)
IST = timezone(timedelta(hours=5, minutes=30))


def _load_schedule_slots():
    """Read schedule slots from publishing.yaml (falls back to 1 default slot).

    ``publishing.yaml`` is one of the seven shared configs owned by
    ``genlab-core/config/`` — the previous ``BlackboxBrief/config/``
    candidate was a symlink to the same file and was dropped along with
    the rest of those symlinks. ``PROJECT_ROOT`` is left as a final
    fallback for unusual deploy layouts (e.g. a packaged install where
    publishing.yaml sits next to the dashboard).
    """
    _GENLAB_ROOT = _DASHBOARD_ROOT.parent
    try:
        for candidate in [
            _GENLAB_ROOT / "genlab-core" / "config" / "publishing.yaml",
            PROJECT_ROOT / "config" / "publishing.yaml",
        ]:
            if candidate.exists():
                with open(candidate) as f:
                    cfg = yaml.safe_load(f) or {}
                slots = cfg.get("instagram", {}).get("schedule_slots", ["12:00"])
                return [str(s) for s in slots]
        return ["12:00"]
    except Exception:
        return ["12:00"]


def _parse_datetime(value) -> datetime | None:
    """Parse a datetime from various formats Microsoft Lists may return."""
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    # Try ISO 8601 first
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    # Try RFC 2822 (e.g. "Thu, 27 Mar 2026 09:00:00 GMT")
    try:
        return parsedate_to_datetime(s)
    except Exception:
        pass
    return None


def _get_client():
    from server.core.graph_sync import get_sync_client

    return get_sync_client()


def _transform_blueprint_media(fields: dict) -> dict:
    """Parse visual_paths/slide_previews JSON and convert local paths to media URLs."""
    media_urls: list[str] = []
    vp_raw = fields.get("visual_paths")
    if isinstance(vp_raw, str) and vp_raw.startswith("["):
        try:
            paths = _json.loads(vp_raw)
            if isinstance(paths, list):
                for p in paths:
                    pp = Path(p)
                    # Only generate a media URL if the file actually exists on disk.
                    # Rendered videos may have been cleaned up by disk_quota eviction.
                    if not pp.exists():
                        continue
                    # Try PROJECT_ROOT first, then GENLAB_ROOT (parent) for non-BB niches
                    _GENLAB_ROOT = _DASHBOARD_ROOT.parent
                    try:
                        rel = pp.relative_to(PROJECT_ROOT)
                        media_urls.append(f"/api/media/{rel}")
                    except ValueError:
                        try:
                            rel = pp.relative_to(_GENLAB_ROOT)
                            media_urls.append(f"/api/media/{rel}")
                        except ValueError:
                            pass
                fields["visual_paths"] = media_urls[0] if media_urls else None
        except (_json.JSONDecodeError, TypeError):
            pass

    # Fallback to thumbnail_url (YouTube CDN) when local media is gone
    if not media_urls:
        thumb = fields.get("thumbnail_url")
        if thumb and isinstance(thumb, str) and thumb.startswith("http"):
            media_urls.append(thumb)
            fields["visual_paths"] = thumb

    sp_raw = fields.get("slide_previews")
    if isinstance(sp_raw, str) and sp_raw.startswith("["):
        try:
            slides = _json.loads(sp_raw)
            if isinstance(slides, list):
                fields["slide_previews"] = slides
        except (_json.JSONDecodeError, TypeError):
            pass
    elif not sp_raw and media_urls:
        fields["slide_previews"] = [{"url": u} for u in media_urls]

    # Parse JSON text fields used by the frontend
    for jf in ("platform_publish_status", "twitter_content", "youtube_content"):
        raw = fields.get(jf)
        if isinstance(raw, str) and raw.startswith("{"):
            try:
                fields[jf] = _json.loads(raw)
            except (_json.JSONDecodeError, TypeError):
                pass

    return fields


@bp.route("", methods=["GET"])
def get_schedule():
    from_date = request.args.get("from", datetime.now(UTC).strftime("%Y-%m-%d"))
    to_date = request.args.get("to", (datetime.now(UTC) + timedelta(days=7)).strftime("%Y-%m-%d"))
    schedule_slots = _load_schedule_slots()

    try:
        start_dt = datetime.strptime(from_date, "%Y-%m-%d")
    except ValueError:
        return api_error(error="Invalid date format. Use YYYY-MM-DD")
    try:
        end_dt_raw = datetime.strptime(to_date, "%Y-%m-%d")
    except ValueError:
        return api_error(error="Invalid date format. Use YYYY-MM-DD")

    try:
        # Fetch ALL scheduled VISUAL_READY + PUBLISHED posts, then filter by
        # date range in Python.  OData datetime comparisons are unreliable
        # when comparing DateTime fields against date-only strings — they can
        # miss valid records or include out-of-range ones.
        formula = "OR({status}='VISUAL_READY',{status}='PUBLISHED',{status}='PUBLISH_FAILED')"
        records = _get_client().blueprints.all(formula=formula)
    except Exception as e:
        logger.error("Failed to fetch schedule: %s", e)
        return api_error(error="Failed to fetch schedule", code=502)

    # Include VISUAL_READY posts with a scheduled_for value + all PUBLISHED.
    # Skip rejected items — they were explicitly declined and should not occupy
    # schedule slots (approved posts behind them would be hidden otherwise).
    # Deduplicate by record ID — Graph API pagination can return duplicates.
    seen_ids: set[str] = set()
    deduped = []
    for r in records:
        rid = str(r.get("id", ""))
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        fields = r.get("fields", {})
        action = (fields.get("action_taken") or "").lower().strip()
        status = (fields.get("status") or "").upper()
        if action == "rejected":
            continue  # rejected items don't belong on the schedule
        if not fields.get("scheduled_for"):
            continue  # skip posts without a schedule date
        # VISUAL_READY only shows on schedule if approved (pending review → Publishing Queue)
        if status == "VISUAL_READY" and action != "approved":
            continue
        deduped.append(r)
    records = deduped

    # Filter by date range in Python (replacing unreliable OData date filter)
    start_dt = start_dt.replace(tzinfo=UTC)
    end_dt = end_dt_raw.replace(hour=23, minute=59, second=59, tzinfo=UTC)
    filtered = []
    for r in records:
        sched_raw = r.get("fields", {}).get("scheduled_for", "")
        if not sched_raw:
            continue
        dt = _parse_datetime(sched_raw)
        if dt is None:
            continue
        # Normalize to UTC for comparison
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        else:
            dt = dt.astimezone(UTC)
        if start_dt <= dt <= end_dt:
            filtered.append(r)
    # Sort: PUBLISHED first so they get slot priority over VISUAL_READY
    _STATUS_PRIORITY = {"PUBLISHED": 0, "PUBLISH_FAILED": 1, "VISUAL_READY": 2}
    records = sorted(
        filtered,
        key=lambda r: _STATUS_PRIORITY.get(r.get("fields", {}).get("status", ""), 9),
    )

    # All 5 niches — each gets its own slot per day
    all_niches = ["ai_creators", "gaming", "sports", "movies", "anime"]

    # Group by date — one slot per niche per day
    days = {}
    start = start_dt.replace(tzinfo=None)
    end = end_dt_raw
    d = start
    while d <= end:
        date_str = d.strftime("%Y-%m-%d")
        days[date_str] = {
            "date": date_str,
            "slots": [
                {"time": schedule_slots[0], "niche_id": niche, "blueprint": None, "status": "empty"}
                for niche in all_niches
            ],
            "coverage": 0.0,
        }
        d += timedelta(days=1)

    # Use the full media transform from blueprints (includes story thumbnail fallback)
    from server.api.blueprints import _transform_media as _full_transform_media

    for r in records:
        sched = r.get("fields", {}).get("scheduled_for", "")
        if not sched:
            continue
        dt = _parse_datetime(sched)
        if dt is None:
            continue
        try:
            if dt.tzinfo is not None:
                dt_ist = dt.astimezone(IST)
            else:
                dt_ist = dt.replace(tzinfo=UTC).astimezone(IST)
            date_key = dt_ist.strftime("%Y-%m-%d")
            if date_key not in days:
                continue

            r_niche = (r.get("fields", {}).get("niche_id") or "").strip()
            status = r.get("fields", {}).get("status", "")
            bp_data = _full_transform_media({"id": r["id"], **r.get("fields", {})}, lite=True)
            slot_status = (
                "published"
                if status == "PUBLISHED"
                else "failed"
                if status == "PUBLISH_FAILED"
                else "scheduled"
            )

            # Find this niche's slot for this day
            # PUBLISHED posts take priority over VISUAL_READY/PUBLISH_FAILED
            for slot in days[date_key]["slots"]:
                if slot.get("niche_id") != r_niche:
                    continue
                if slot["blueprint"] is None:
                    # Empty slot — assign
                    slot["blueprint"] = bp_data
                    slot["status"] = slot_status
                    break
                elif slot_status == "published" and slot["status"] != "published":
                    # PUBLISHED overrides non-published
                    slot["blueprint"] = bp_data
                    slot["status"] = slot_status
                    break
        except (ValueError, KeyError):
            continue

    # Calculate coverage (filled niches / total niches)
    for day in days.values():
        filled = sum(1 for s in day["slots"] if s["status"] != "empty")
        day["coverage"] = filled / len(day["slots"]) if day["slots"] else 0.0

    return api_success(
        data={
            "data": list(days.values()),
            "schedule_slots": schedule_slots,
        }
    )


@bp.route("/slots", methods=["GET"])
def get_slots():
    """Return configured schedule slots for the frontend."""
    return api_success(data={"data": _load_schedule_slots()})


@bp.route("/coverage", methods=["GET"])
def coverage():
    resp = get_schedule()
    # api_success returns (response, status_code) tuple
    response_obj = resp[0] if isinstance(resp, tuple) else resp
    raw = response_obj.get_json()
    # Unwrap envelope if present
    if raw and "status" in raw and "data" in raw:
        raw = raw["data"]
    days_list = raw.get("data", []) if isinstance(raw, dict) else []
    coverage_data = [{"date": d["date"], "coverage": d["coverage"]} for d in days_list]
    return api_success(data={"data": coverage_data})


def _check_slot_collision(
    client,
    target_date: str,
    target_slot: str,
    exclude_blueprint_id: str | None = None,
    niche_id: str | None = None,
) -> dict | None:
    """Check if a schedule slot already has a post assigned.

    Returns the occupying blueprint dict if collision, None if slot is free.
    Collision is per-niche: different channels can publish at the same slot.
    """
    target_iso = f"{target_date}T{target_slot}:00+05:30"
    target_dt = _parse_datetime(target_iso)
    if target_dt is None:
        return None

    # Fetch VISUAL_READY + PUBLISHED posts scoped to ±1 day of target date
    # (avoids full table scan — only needs nearby records for collision check)
    try:
        from datetime import date as _date

        td = _date.fromisoformat(target_date)
        day_before = (td - timedelta(days=1)).isoformat()
        day_after = (td + timedelta(days=1)).isoformat()
        formula = (
            f"AND(OR({{status}}='VISUAL_READY',{{status}}='PUBLISHED'),"
            f"{{scheduled_for}}>='{day_before}',{{scheduled_for}}<='{day_after}T23:59:59')"
        )
        records = client.blueprints.all(formula=formula)
    except Exception:
        return None

    for r in records:
        rid = str(r.get("id", ""))
        if rid == str(exclude_blueprint_id):
            continue
        # Rejected items don't occupy slots — they were explicitly declined
        r_action = (r.get("fields", {}).get("action_taken") or "").lower().strip()
        if r_action == "rejected":
            continue
        # Skip blueprints from other niches — each channel schedules independently
        if niche_id:
            r_niche = (r.get("fields", {}).get("niche_id", "") or "").strip()
            if r_niche and r_niche != niche_id:
                continue
        sched_raw = r.get("fields", {}).get("scheduled_for", "")
        if not sched_raw:
            continue
        dt = _parse_datetime(sched_raw)
        if dt is None:
            continue
        # Normalize both to UTC for reliable comparison
        dt_utc = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
        target_utc = (
            target_dt.astimezone(UTC) if target_dt.tzinfo else target_dt.replace(tzinfo=UTC)
        )
        if dt_utc.strftime("%Y-%m-%d %H:%M") == target_utc.strftime("%Y-%m-%d %H:%M"):
            return {"id": rid, "title": r.get("fields", {}).get("title", "")}
    return None


@bp.route("/reorder", methods=["PATCH"])
def reorder():
    data = request.json or {}
    blueprint_id = data.get("blueprint_id")
    to_slot = data.get("to_slot")
    to_date = data.get("to_date")
    if not all([blueprint_id, to_slot]):
        return api_error(error="blueprint_id and to_slot required")
    if not RECORD_RE.match(str(blueprint_id)):
        return api_error(error="Invalid blueprint_id")
    if not SLOT_RE.match(str(to_slot)):
        return api_error(error="Invalid to_slot format")
    # Validate to_slot is a known schedule slot
    valid_slots = _load_schedule_slots()
    if str(to_slot) not in valid_slots:
        return api_error(error=f"to_slot must be one of {valid_slots}")
    if to_date:
        try:
            datetime.strptime(to_date, "%Y-%m-%d")
        except ValueError:
            return api_error(error="Invalid to_date format. Use YYYY-MM-DD")

    effective_date = to_date or datetime.now().strftime("%Y-%m-%d")
    client = _get_client()

    # Look up the blueprint's niche_id for niche-scoped collision check
    try:
        bp_record = client.blueprints.get(str(blueprint_id))
        bp_niche = (
            (bp_record.get("fields", {}).get("niche_id", "") or "").strip() if bp_record else ""
        )
    except Exception:
        bp_niche = ""

    # Collision check: reject if slot already occupied by another post in same niche
    occupant = _check_slot_collision(
        client, effective_date, to_slot, exclude_blueprint_id=blueprint_id, niche_id=bp_niche
    )
    if occupant:
        return api_error(
            error=f"Slot {effective_date} {to_slot} IST is already occupied by blueprint {occupant['id']}",
            code=409,
        )

    scheduled_for = f"{effective_date}T{to_slot}:00+05:30"
    try:
        client.blueprints.update(blueprint_id, {"scheduled_for": scheduled_for})
        return api_success(data={"status": "ok", "scheduled_for": scheduled_for})
    except Exception as e:
        logger.error("Schedule swap failed: %s", e, exc_info=True)
        return api_error(error="Failed to update schedule", code=500)
