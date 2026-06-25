"""Story resource API endpoints."""

import logging

from flask import Blueprint, request

from server.core.responses import api_error, api_not_found, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("stories_api", __name__, url_prefix="/api/v1/stories")


def _get_client():
    from server.core.graph_sync import get_sync_client

    return get_sync_client()


@bp.route("", methods=["GET"])
def list_stories():
    niche_id = request.args.get("niche_id", "all")
    source = request.args.get("source")
    search = request.args.get("search", "").lower()
    try:
        page = max(1, int(request.args.get("page", 1)))
        per_page = min(int(request.args.get("per_page", 25)), 100)
    except (ValueError, TypeError):
        return api_error(error="Invalid page or per_page parameter")

    # Allowlist of valid source values to prevent formula injection
    _VALID_SOURCES = frozenset(
        {
            "youtube",
            "youtube_rss",
            "youtube_playlist",
            "youtube_search",
            "rss",
            "google_trends",
            "manual",
            "reddit",
            "twitter",
        }
    )

    filters = []
    if source:
        if source.lower() not in _VALID_SOURCES:
            return api_error(error="Invalid source parameter")
        filters.append(f"{{source}}='{source}'")
    formula = "AND(" + ",".join(filters) + ")" if filters else ""

    try:
        records = (
            _get_client().stories.all(formula=formula) if formula else _get_client().stories.all()
        )
    except Exception as e:
        logger.error("Failed to fetch stories: %s", e, exc_info=True)
        return api_error(error="Failed to fetch stories", code=502)

    # Filter by niche_id client-side
    if niche_id and niche_id != "all":
        records = [
            r for r in records if (r.get("fields", {}).get("niche_id") or "ai_creators") == niche_id
        ]
    # PR #551 (2026-06-25, SR-F wire pass 12): per-user allowlist
    # filter. Same shape as blueprints + queue list filters
    # (#540, #543, #549). Restricted operator sees only stories
    # from niches in their allowlist; unrestricted admin sees all.
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None:
        records = [
            r for r in records if (r.get("fields", {}).get("niche_id") or "ai_creators") in _allowed
        ]

    if search:
        records = [
            r for r in records if search in (r.get("fields", {}).get("title", "") or "").lower()
        ]

    records.sort(key=lambda r: str(r.get("fields", {}).get("published_at", "") or ""), reverse=True)

    total = len(records)
    start = (page - 1) * per_page
    page_data = records[start : start + per_page]

    def _normalize(fields: dict) -> dict:
        """Map Microsoft Lists field names to frontend-expected names."""
        out = dict(fields)
        # published_at → published_date (frontend expects published_date)
        if "published_at" in out and "published_date" not in out:
            out["published_date"] = out["published_at"]
        # priority → score (frontend expects score)
        if "priority" in out and "score" not in out:
            out["score"] = out["priority"]
        return out

    return api_success(
        data={
            "data": [{"id": r["id"], **_normalize(r.get("fields", {}))} for r in page_data],
            "meta": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": max(1, (total + per_page - 1) // per_page),
            },
        }
    )


@bp.route("/<record_id>", methods=["GET"])
def get_story(record_id):
    import re

    if not re.match(r"^[\w-]+$", record_id):
        return api_error(error="Invalid record ID")
    try:
        r = _get_client().stories.get(record_id)
    except Exception:
        return api_not_found(message="Not found")
    # PR #551 (SR-F wire pass 12): per-record tenant guard. Story has
    # a niche_id field — restricted operator outside the story's niche
    # gets 403 (mirrors GET /blueprints/<id> from PR #550). The fetch
    # happens BEFORE the guard so we can read the story's niche; on
    # missing-story we let the existing 404 path fire.
    if r:
        from server.auth.niche_allowlist import get_allowed_niches

        _allowed = get_allowed_niches()
        if _allowed is not None:
            _story_niche = r.get("fields", {}).get("niche_id") or "ai_creators"
            if _story_niche not in _allowed:
                return api_error(
                    error=(
                        f"Story belongs to niche '{_story_niche}' which is "
                        f"not in your allowlist (allowed: {sorted(_allowed)}). "
                        f"See SR-F (PR #551)."
                    ),
                    code=403,
                )
    return api_success(data={"data": {"id": r["id"], **r.get("fields", {})}})
