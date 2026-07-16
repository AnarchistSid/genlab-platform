"""Shared Publishing_Analytics recorder for all niches.

Writes a publish record to the unified Publishing_Analytics SharePoint list
so cross-channel dashboard queries and DailyCapEnforcer work correctly.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from genlab_core.cache.post_id_norm import normalize_post_id

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
    post_id_override: str = "",
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
        post_id_override: 2026-07-14 — if provided, use this native
            platform ID directly instead of parsing the URL. Prefer this
            over URL parsing for platforms with URL/ID-shape divergence
            (Instagram: URL contains shortcode e.g. ``Daw4GQNiZLJ`` but
            Meta returns numeric media_id ``18121108351794421``; Threads
            has the same shape divergence). Passing the native ID here
            makes publishing_analytics.post_id match pending_feedback.
            post_id — closing the multi-identifier-drift class-of-bug
            that broke reward-loop JOINs for those two platforms.
    """
    if not published_at:
        published_at = datetime.now(UTC).isoformat()

    # Prefer post_id_override (native platform ID from PublishResult)
    # when provided — matches pending_feedback.post_id shape.
    post_id = post_id_override.strip() if post_id_override else ""
    _from_override = bool(post_id)
    if post_id:
        # Native platform ID provided directly — skip URL parsing.
        pass
    elif post_url:
        # Fallback: derive post_id from post_url (URL-tail extraction).
        # This is the legacy path — kept for retry_pass.py and any
        # future caller that doesn't have the native ID handy. For IG +
        # Threads this produces shortcode form which will NOT join to
        # pending_feedback's numeric form.
        if "youtube.com" in post_url:
            post_id = post_url.rstrip("/").split("/")[-1]
        elif "instagram.com" in post_url:
            post_id = post_url.rstrip("/").split("/")[-1]
        elif "facebook.com" in post_url:
            post_id = post_url.rstrip("/").split("/")[-1]
        else:
            post_id = post_url.rstrip("/").split("/")[-1]

        # 2026-07-14 audit: URL-tail extraction fails silently when
        # post_url is malformed (e.g. ``https://instagram.com`` with
        # no reel/post path). The tail becomes the domain itself
        # (``"instagram.com"``) which normalize_post_id then prefixes
        # to ``"instagram:instagram.com"`` — join with
        # pending_feedback.post_id silently fails at reward-fetch time.
        # Reject bare-domain tails so the failure surfaces at write
        # time (WARNING log) instead of silently corrupting analytics.
        # Only applied to URL-derived post_ids (not overrides — those
        # come from PublishResult.post_id which is trusted).
        if (
            not _from_override
            and post_id
            and (
                post_id.endswith(".com")
                or post_id.endswith(".net")
                or post_id.endswith(".org")
                or "/" not in post_url.rstrip("/")[len("https://") :]
            )
        ):
            logger.warning(
                "[analytics_recorder] malformed post_url=%r derived "
                "post_id=%r (looks like bare domain) — clearing to "
                "empty string; downstream reward-fetch will skip this "
                "record. niche=%s platform=%s bp=%s",
                post_url,
                post_id,
                niche_id,
                platform,
                blueprint_id,
            )
            post_id = ""

    fields = {
        "Title": f"{niche_id}:{platform}:{published_at[:10]}",
        "niche_id": niche_id,
        # Task #625 (2026-07-09) — audit follow-up to #624/#748.
        # Pre-#625 this was an inline ``f"{platform}:{post_id}"`` which
        # shares the same class of bug as #748: no idempotence check.
        # If ``post_id`` ever gets pre-prefixed upstream (currently
        # unlikely — URL-tail extraction produces bare ids — but the
        # invariant should not depend on that) the composite key
        # becomes ``platform:platform:...`` and won't join with
        # analytics.post_id. Canonical helper closes the class.
        "post_id": normalize_post_id(platform, post_id),
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
            logger.debug("No publishing_analytics proxy on client — skipping analytics record.")
            return
        proxy.create(fields)
        logger.info(
            "Recorded to Publishing_Analytics: %s/%s/%s → %s",
            niche_id,
            platform,
            status,
            post_url[:60] if post_url else "(no url)",
        )
    except Exception as e:
        logger.warning("Failed to record to Publishing_Analytics: %s", e)
