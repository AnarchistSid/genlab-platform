"""Shared Publishing_Analytics recorder for all niches.

Writes a publish record to the unified Publishing_Analytics SharePoint list
so cross-channel dashboard queries and DailyCapEnforcer work correctly.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# List ID resolved by BacklogClient from config — this constant is unused.
_LEGACY_LIST_ID = "ea0c759a-1d9c-4aea-84ee-45cd2b5deb42"  # kept for reference


def record_publish(
    client,
    niche_id: str,
    platform: str,
    status: str,
    post_url: str = "",
    blueprint_id: str = "",
    candidate_id: str = "",
    error_message: str = "",
    published_at: str = "",
) -> None:
    """Write a publish record to the shared Publishing_Analytics list.

    Safe to call from any niche's publish stage. Failures are logged but
    never block the publish flow.

    Args:
        client: BacklogClient instance (must have publishing_analytics proxy).
        niche_id: Channel identifier (e.g. "gaming", "ai_creators").
        platform: Platform name (e.g. "youtube", "instagram").
        status: "SUCCESS" or "FAILED".
        post_url: URL of published post (if available).
        blueprint_id: Backlog blueprint ID.
        candidate_id: Stable candidate ID.
        error_message: Error details on failure.
        published_at: ISO timestamp. Defaults to now.
    """
    if not published_at:
        published_at = datetime.now(UTC).isoformat()

    # Derive post_id from post_url (platform-specific ID extraction)
    post_id = ""
    if post_url:
        if "youtube.com" in post_url:
            post_id = post_url.rstrip("/").split("/")[-1]
        elif "instagram.com" in post_url:
            post_id = post_url.rstrip("/").split("/")[-1]
        elif "facebook.com" in post_url:
            post_id = post_url.rstrip("/").split("/")[-1]
        else:
            post_id = post_url.rstrip("/").split("/")[-1]

    fields = {
        "Title": f"{niche_id}:{platform}:{published_at[:10]}",
        "niche_id": niche_id,
        "post_id": f"{platform}:{post_id}" if post_id else "",
        "platform": platform,
        "status": status,
        "published_at": published_at,
        "post_url": post_url,
        "blueprint_id": blueprint_id,
        "candidate_id": candidate_id,
        "error_message": error_message,
    }

    try:
        proxy = getattr(client, "publishing_analytics", None)
        if proxy is None:
            logger.debug(
                "No publishing_analytics proxy on client — skipping analytics record."
            )
            return
        proxy.create(fields)
        logger.info(
            "Recorded to Publishing_Analytics: %s/%s/%s → %s",
            niche_id, platform, status, post_url[:60] if post_url else "(no url)",
        )
    except Exception as e:
        logger.warning("Failed to record to Publishing_Analytics: %s", e)
