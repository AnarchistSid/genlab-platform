"""Platform-specific post listing endpoints.

Routes:
    GET /api/v1/platforms/tiktok/posts   — last 30 TikTok posts with metrics
    GET /api/v1/platforms/threads/posts  — last 30 Threads posts with metrics
"""

import logging
import os

from flask import Blueprint

from server.core.responses import api_success

logger = logging.getLogger(__name__)
bp = Blueprint("platform_posts_api", __name__, url_prefix="/api/v1/platforms")


def _get_client():
    from server.core.graph_sync import get_sync_client

    return get_sync_client()


@bp.route("/tiktok/posts", methods=["GET"])
def tiktok_posts():
    """Return last 30 TikTok posts with metrics from Publishing_Analytics."""
    try:
        client = _get_client()
        records = client.publishing_analytics.all(
            formula="AND({platform}='tiktok')",
        )
    except Exception as e:
        logger.warning("Failed to fetch TikTok posts: %s", e)
        records = []

    # Sort by published_at descending, take last 30
    records.sort(
        key=lambda r: r.get("fields", {}).get("published_at") or "",
        reverse=True,
    )
    records = records[:30]

    audit_raw = os.getenv("TIKTOK_AUDIT_APPROVED", "false")
    audit_approved = audit_raw.lower() == "true"

    posts = []
    for r in records:
        f = r.get("fields", {})
        posts.append(
            {
                "id": r.get("id"),
                "blueprint_id": f.get("blueprint_id"),
                "published_at": f.get("published_at"),
                "post_id": f.get("post_id"),
                "views": f.get("impressions") or f.get("views") or 0,
                "likes": f.get("likes", 0),
                "comments": f.get("comments", 0),
                "shares": f.get("shares", 0),
                "engagement_rate": f.get("engagement_rate"),
                "status": f.get("status"),
            }
        )

    return api_success(
        data={
            "data": posts,
            "meta": {
                "total": len(posts),
                "audit_approved": audit_approved,
                "note": None
                if audit_approved
                else "Posts published as SELF_ONLY (private) pending audit approval",
            },
        }
    )


@bp.route("/threads/posts", methods=["GET"])
def threads_posts():
    """Return last 30 Threads posts with metrics from Publishing_Analytics."""
    try:
        client = _get_client()
        records = client.publishing_analytics.all(
            formula="AND({platform}='threads')",
        )
    except Exception as e:
        logger.warning("Failed to fetch Threads posts: %s", e)
        records = []

    records.sort(
        key=lambda r: r.get("fields", {}).get("published_at") or "",
        reverse=True,
    )
    records = records[:30]

    posts = []
    for r in records:
        f = r.get("fields", {})
        posts.append(
            {
                "id": r.get("id"),
                "blueprint_id": f.get("blueprint_id"),
                "published_at": f.get("published_at"),
                "post_id": f.get("post_id"),
                "permalink": f.get("permalink"),
                "views": f.get("impressions") or f.get("views") or 0,
                "likes": f.get("likes", 0),
                "replies": f.get("comments", 0),
                "reposts": f.get("shares", 0),
                "quotes": f.get("quotes", 0),
                "engagement_rate": f.get("engagement_rate"),
            }
        )

    return api_success(
        data={
            "data": posts,
            "meta": {"total": len(posts)},
        }
    )
