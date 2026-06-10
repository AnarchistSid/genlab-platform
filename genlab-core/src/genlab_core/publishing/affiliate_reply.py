"""Post-publish affiliate link as first comment/reply.

Single public entry point :func:`post_affiliate_reply` runs after each
successful platform publish: it drops the affiliate URL into the first
comment/reply on the new post so the link is one tap away from the
viewer while keeping captions clean (and avoiding the algorithmic
downranking that captions-with-links receive on Facebook and X).

Per-platform branches:

* Instagram — Graph API ``POST /{media_id}/comments`` with the affiliate
  text (Instagram's pinned-first-comment convention).
* Facebook — ``FacebookClient.post_reply`` (delegates to the
  ``/{post_id}/comments`` Graph endpoint).
* X / Twitter — ``XTwitterClient.post_reply`` (in_reply_to tweet).
* Threads — ``ThreadsClient.post_reply`` (reply thread).
* YouTube — no-op; affiliate URL is already in the video description
  (injected upstream by ``cta_engine`` into ``youtube_content``).

Lives in its own module so the multi-platform publisher orchestrator
stays focused on the publish flow itself. Extracted in the refactor-#9
decomposition (PR 5/N). Re-exported from the orchestrator with the
``_post_affiliate_reply`` alias the orchestrator's own ``run_publish``
calls.

Strictly non-blocking: every error is caught and logged at WARN.
Affiliate-comment failures must NEVER take down the publish itself
(the main post has already shipped at this point).
"""

from __future__ import annotations

import logging

import requests

from genlab_core.platforms.facebook import FacebookClient
from genlab_core.platforms.meta_api import META_GRAPH_BASE_URL
from genlab_core.platforms.threads import ThreadsClient
from genlab_core.platforms.x_twitter import XTwitterClient
from genlab_core.publishing.niche_credentials import (
    resolve_fb_credentials,
    resolve_meta_credentials,
    resolve_threads_credentials,
    resolve_twitter_credentials,
)

logger = logging.getLogger(__name__)


def post_affiliate_reply(
    platform: str,
    post_id: str | None,
    fields: dict,
    niche_id: str,
) -> None:
    """Post the affiliate URL as the first comment/reply on a freshly-
    published post. No-op + early-return when there is nothing to post
    (missing post_id, missing affiliate fields, YouTube).

    Strictly non-blocking — any exception is caught and logged at WARN.
    The main post has already shipped before we get here; an affiliate-
    comment failure must NEVER unwind that.
    """
    if not post_id:
        return

    affiliate_url = fields.get("affiliate_url", "")
    affiliate_product = fields.get("affiliate_product", "")
    if not affiliate_url or not affiliate_product:
        return

    text = f"🔗 {affiliate_product}: {affiliate_url}"

    try:
        if platform == "instagram":
            # Instagram doesn't support bio-link updates via API, so the
            # standard convention is a pinned first comment.
            meta_creds = resolve_meta_credentials(niche_id)
            access_token = meta_creds.get("ig_access_token", "")
            if not access_token:
                return
            resp = requests.post(
                f"{META_GRAPH_BASE_URL}/{post_id}/comments",
                data={"message": text, "access_token": access_token},
                timeout=15,
            )
            if resp.status_code == 200:
                logger.info("[affiliate] Posted IG comment on %s", post_id)
            else:
                logger.debug(
                    "[affiliate] IG comment failed (HTTP %d): %s",
                    resp.status_code,
                    resp.text[:200],
                )

        elif platform == "facebook":
            access_token, _page_id = resolve_fb_credentials(niche_id)
            if not access_token:
                return
            fb = FacebookClient(access_token=access_token)
            fb.post_reply(post_id, text)
            logger.info("[affiliate] Posted FB comment on %s", post_id)

        elif platform in ("twitter", "x_twitter"):
            creds = resolve_twitter_credentials(niche_id)
            if not creds.get("api_key"):
                return
            x = XTwitterClient(**creds)
            x.post_reply(post_id, text)
            logger.info("[affiliate] Posted X reply to %s", post_id)

        elif platform == "threads":
            access_token, user_id = resolve_threads_credentials(niche_id)
            if not access_token or not user_id:
                return
            threads = ThreadsClient(access_token=access_token, user_id=user_id)
            threads.post_reply(post_id, text)
            logger.info("[affiliate] Posted Threads reply to %s", post_id)

        # YouTube: affiliate URL is already in the video description
        # (injected by cta_engine into ``youtube_content``). No-op.

    except Exception as exc:
        logger.warning(
            "[affiliate] Reply failed for %s/%s (non-blocking): %s",
            platform,
            post_id,
            exc,
        )
