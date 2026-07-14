"""Facebook Graph API platform client.

Auth: META_ACCESS_TOKEN (EAA Page Token — permanent, never refresh).
ALWAYS use graph.facebook.com — NEVER graph.instagram.com.
EAA Page Tokens do NOT use ig_refresh_token — they are permanent.

Implements Publisher + Engageable + Trackable + HealthCheckable protocols.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

import requests

from genlab_core.platforms.meta_api import META_GRAPH_API_VERSION
from genlab_core.platforms.models import (
    PlatformMetrics,
    PublishPayload,
    PublishResult,
    TokenStatus,
)
from genlab_core.platforms.models import (
    safe_json as _safe_json,
)

logger = logging.getLogger(__name__)

# Facebook Graph API post insight metric names
#
# 2026-07-14 (X audit F3): added ``fb_reels_total_plays`` alongside
# ``post_video_views``. Per meta_metric_deprecation registry,
# ``post_video_views`` is RETIRED for FB Reels in Graph API v23.0
# (replaced by ``fb_reels_total_plays``). We're on v22.0 today;
# the moment Meta cuts over, post_video_views returns 0 for Reels →
# reward=0 → bandit death spiral (identical shape to the YT #578
# class-of-bug). Requesting BOTH is migration-friendly:
#   - v22: post_video_views returns; fb_reels_total_plays may also
#     return for Reels. Parsing prefers fb_reels_total_plays when
#     present (see :500) so migration is gradual.
#   - v23+: post_video_views returns 0; fb_reels_total_plays is the
#     only signal. Cutover is seamless.
# Non-Reels videos return fb_reels_total_plays=0 → falls back to
# post_video_views correctly for feed videos.
_VIDEO_INSIGHTS_METRICS = [
    "post_video_views",
    "fb_reels_total_plays",
    "post_video_views_organic",
    "post_reactions_like_total",
    "post_comments",
    "post_shares",
]

_FEED_INSIGHTS_METRICS = [
    "post_impressions",
    "post_reactions_like_total",
    "post_comments",
    "post_shares",
]


class FacebookClient:
    """Meta Graph API client for Facebook Page publishing and engagement.

    All HTTP calls go to ``graph.facebook.com`` (never ``graph.instagram.com``).
    The EAA Page Token must never be refreshed via ``ig_refresh_token``.

    Args:
        access_token: Meta EAA Page Token. Defaults to ``META_ACCESS_TOKEN`` env var.
        page_id: Facebook Page ID. Defaults to ``META_FB_PAGE_ID`` env var.
        api_version: Graph API version string. Defaults to
            ``META_GRAPH_API_VERSION`` from
            :mod:`genlab_core.platforms.meta_api` — the single source
            of truth bumped centrally (audit R-44).
    """

    platform_id = "facebook"

    def __init__(
        self,
        access_token: str | None = None,
        page_id: str | None = None,
        api_version: str = META_GRAPH_API_VERSION,
        *,
        niche_id: str = "",
    ) -> None:
        """3-tier auth lookup matching InstagramClient (P2 phase 1, PR #377):
        1. Explicit kwarg
        2. Per-niche via ``resolve_fb_credentials(niche_id)``
        3. Global env fallback ``META_ACCESS_TOKEN`` / ``META_FB_PAGE_ID``
        """
        self.niche_id = niche_id
        self._access_token: str = self._resolve_access_token(access_token, niche_id)
        self._page_id: str = self._resolve_page_id(page_id, niche_id)
        self._api_version = api_version
        self._base_url = f"https://graph.facebook.com/{api_version}"
        # P2 phase 2: per-niche LoggerAdapter (mirrors IG pattern from PR #382).
        self._log: logging.LoggerAdapter | logging.Logger = (
            logging.LoggerAdapter(logger, {"niche_id": self.niche_id, "platform": self.platform_id})
            if self.niche_id
            else logger
        )

    @staticmethod
    def _resolve_access_token(explicit: str | None, niche_id: str) -> str:
        if explicit:
            return explicit
        if niche_id:
            from genlab_core.publishing.niche_credentials import resolve_fb_credentials

            tok, _ = resolve_fb_credentials(niche_id)
            if tok:
                return tok
        return os.environ.get("META_ACCESS_TOKEN", "")

    @staticmethod
    def _resolve_page_id(explicit: str | None, niche_id: str) -> str:
        if explicit:
            return explicit
        if niche_id:
            from genlab_core.publishing.niche_credentials import resolve_fb_credentials

            _, pid = resolve_fb_credentials(niche_id)
            if pid:
                return pid
        return os.environ.get("META_FB_PAGE_ID", "")

    # ------------------------------------------------------------------
    # Publisher protocol
    # ------------------------------------------------------------------

    def publish(self, payload: PublishPayload) -> PublishResult:
        """Publish a video or text/link post to a Facebook Page.

        Routing:
          - media_paths with a video → POST ``/{page_id}/videos``
          - no media → POST ``/{page_id}/feed`` (text/link post)

        Args:
            payload: Unified publish payload.

        Returns:
            :class:`~genlab_core.platforms.models.PublishResult`
        """
        # Pre-flight: validate token before attempting publish
        if not self._validate_token_preflight():
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="Facebook token invalid or missing — skipped publish",
            )

        # PR #Layer4 (2026-07-11, post-Markanimation): publisher-side
        # attribution validation. Sits at the API-POST boundary as the
        # last line of defense for the attribution-safety stack. If
        # the caption reaches here without a recognisable credit
        # signal, log a compliance event; if enforcement is on
        # (``GENLAB_ATTRIBUTION_LAYER4_BLOCK=1``), refuse to publish.
        from genlab_core.platforms.caption_validation import (
            layer4_block_enabled,
            validate_caption_has_attribution,
        )

        _l4_ok, _l4_reason = validate_caption_has_attribution(
            payload.caption,
            source_url=getattr(payload, "source_url", None),
        )
        if not _l4_ok:
            self._log.warning(
                "[layer4] Facebook publish: caption missing attribution (niche=%s) — %s",
                payload.niche_id,
                _l4_reason,
            )
            if layer4_block_enabled():
                return PublishResult(
                    platform=self.platform_id,
                    success=False,
                    error=(
                        "Layer 4 attribution gate: caption missing credit line "
                        "(set GENLAB_ATTRIBUTION_LAYER4_BLOCK=0 to bypass)"
                    ),
                )

        # Build caption + hashtags
        hashtags_str = " ".join(payload.hashtags) if payload.hashtags else ""
        message = payload.caption
        if hashtags_str:
            message = f"{message}\n\n{hashtags_str}"

        # Route: video vs text/link
        video_url = self._resolve_video_url(payload)

        if not video_url:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="Video required — Facebook text-only posts disabled (video-first mandate)",
            )

        result = self._publish_video(video_url=video_url, message=message)

        # Post affiliate link as first comment (FB downranks external URLs
        # in the main caption). Best-effort: a failure here doesn't fail
        # the publish itself.
        if result.success and payload.first_comment_text and result.post_id:
            try:
                comment_ok = self.post_reply(
                    parent_id=result.post_id,
                    text=payload.first_comment_text,
                    context_id=result.post_id,
                )
                if comment_ok:
                    self._log.info(
                        "Facebook: affiliate first-comment posted under %s",
                        result.post_id,
                    )
                else:
                    self._log.warning(
                        "Facebook: first-comment post returned False for %s",
                        result.post_id,
                    )
            except Exception as exc:
                self._log.warning(
                    "Facebook: first-comment exception (non-fatal): %s",
                    exc,
                )

        return result

    def _validate_token_preflight(self) -> bool:
        """Quick /me check to catch expired or missing tokens before publish."""
        if not self._access_token:
            self._log.error(
                "Facebook: no access token configured — "
                "set FB_PAGE_ACCESS_TOKEN or META_ACCESS_TOKEN in .env"
            )
            return False
        if not self._page_id:
            self._log.error(
                "Facebook: no page ID configured — set META_FB_PAGE_ID or FB_PAGE_ID in .env"
            )
            return False
        try:
            resp = requests.get(
                f"{self._base_url}/me",
                params={"access_token": self._access_token},
                timeout=10,
            )
            if resp.status_code == 400:
                self._log.error(
                    "Facebook: token invalid or expired (HTTP 400). "
                    "Skipping Facebook publish. Refresh token in .env."
                )
                return False
            if resp.status_code != 200:
                self._log.warning(
                    "Facebook: token check returned HTTP %d — proceeding cautiously",
                    resp.status_code,
                )
            return True
        except Exception as e:
            self._log.warning("Facebook: token pre-flight check failed: %s", e)
            return True  # Network error — don't block, let publish try

    def _resolve_video_url(self, payload: PublishPayload) -> str:
        """Extract a video URL or path from the payload.

        Returns the first media path as a string if media_paths is non-empty
        and media_type is ``'video'``.  Otherwise returns ``''``.
        """
        if not payload.media_paths:
            return ""
        if payload.media_type != "video":
            return ""
        return str(payload.media_paths[0])

    def _publish_video(self, *, video_url: str, message: str) -> PublishResult:
        """POST to /{page_id}/videos with file_url or multipart upload.

        Returns:
            :class:`~genlab_core.platforms.models.PublishResult`
        """
        url = f"{self._base_url}/{self._page_id}/videos"

        is_url = video_url.startswith("http://") or video_url.startswith("https://")

        data: dict[str, str] = {
            "description": message,
            "access_token": self._access_token,
        }

        try:
            if is_url:
                data["file_url"] = video_url
                resp = requests.post(url, data=data, timeout=300)
            else:
                # Local file multipart upload
                from pathlib import Path

                local_path = Path(video_url)
                if not local_path.exists():
                    return PublishResult(
                        platform=self.platform_id,
                        success=False,
                        error=f"Video file not found: {video_url}",
                    )
                with open(local_path, "rb") as fh:
                    resp = requests.post(
                        url,
                        data=data,
                        files={"source": (local_path.name, fh, "video/mp4")},
                        timeout=300,
                    )

            response_data = _safe_json(resp)

            if "id" in response_data:
                post_id = response_data["id"]
                self._log.info("Facebook: video published — post ID: %s", post_id)
                return PublishResult(
                    platform=self.platform_id,
                    success=True,
                    post_id=post_id,
                    post_url=f"https://www.facebook.com/{post_id}",
                    raw_response=response_data,
                )

            error_msg = _extract_error_message(response_data)
            self._log.error("Facebook: video publish failed: %s", error_msg)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=error_msg,
                raw_response=response_data,
            )

        except Exception as exc:
            self._log.error("Facebook: video publish exception: %s", exc)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=str(exc),
            )

    def _publish_feed(self, *, message: str, payload: PublishPayload) -> PublishResult:
        """POST to /{page_id}/feed for text or link posts.

        Returns:
            :class:`~genlab_core.platforms.models.PublishResult`
        """
        url = f"{self._base_url}/{self._page_id}/feed"
        data: dict[str, str] = {
            "message": message,
            "access_token": self._access_token,
        }

        try:
            resp = requests.post(url, data=data, timeout=60)
            response_data = _safe_json(resp)

            if "id" in response_data:
                post_id = response_data["id"]
                self._log.info("Facebook: feed post published — post ID: %s", post_id)
                return PublishResult(
                    platform=self.platform_id,
                    success=True,
                    post_id=post_id,
                    post_url=f"https://www.facebook.com/{post_id}",
                    raw_response=response_data,
                )

            error_msg = _extract_error_message(response_data)
            self._log.error("Facebook: feed post failed: %s", error_msg)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=error_msg,
                raw_response=response_data,
            )

        except Exception as exc:
            self._log.error("Facebook: feed post exception: %s", exc)
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Engageable protocol
    # ------------------------------------------------------------------

    def post_reply(self, parent_id: str, text: str, *, context_id: str = "") -> bool:
        """Reply to a Facebook comment.

        Unlike Instagram (which uses ``/replies``), Facebook comments use the
        same ``/{comment_id}/comments`` endpoint for nested replies.

        Args:
            parent_id: The comment ID to reply to.
            text: Reply text.
            context_id: Unused for Facebook (kept for protocol compatibility).

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        url = f"{self._base_url}/{parent_id}/comments"
        try:
            resp = requests.post(
                url,
                data={"message": text, "access_token": self._access_token},
                timeout=15,
            )
            data = _safe_json(resp)
            if resp.status_code == 200 and "id" in data:
                self._log.info("Facebook: replied to comment %s", parent_id)
                return True
            self._log.warning(
                "Facebook: reply failed (HTTP %d): %s",
                resp.status_code,
                data.get("error", {}).get("message", str(data)),
            )
            return False
        except Exception as exc:
            self._log.warning("Facebook: post_reply exception: %s", exc)
            return False

    def like(self, target_id: str, *, context_id: str = "") -> bool:
        """Like a Facebook comment.

        Note: The Graph API supports liking comments on behalf of a Page via
        ``/{object-id}/likes`` with a POST. However, this is often restricted
        or requires additional permissions. This implementation attempts the
        call and falls back gracefully.

        Returns ``True`` on success or if the API is unavailable.
        """
        url = f"{self._base_url}/{target_id}/likes"
        try:
            resp = requests.post(
                url,
                data={"access_token": self._access_token},
                timeout=15,
            )
            if resp.status_code == 200:
                self._log.info("Facebook: liked target %s", target_id)
                return True
            self._log.debug(
                "Facebook: like() not supported for target %s (HTTP %d)",
                target_id,
                resp.status_code,
            )
            return True  # Fail-open — keep engagement pipeline happy
        except Exception as exc:
            self._log.debug("Facebook: like() exception (ignored): %s", exc)
            return True  # Fail-open

    # ------------------------------------------------------------------
    # Trackable protocol
    # ------------------------------------------------------------------

    def get_metrics(self, post_id: str, published_at: datetime) -> PlatformMetrics | None:
        """Fetch post-level insights from the Facebook Graph API.

        Uses ``/{post_id}/insights`` with video or feed metric names.

        Args:
            post_id: Facebook post or video ID.
            published_at: UTC datetime when the post was published (unused
                          for Facebook, included for protocol compatibility).

        Returns:
            :class:`~genlab_core.platforms.models.PlatformMetrics` on success,
            or ``None`` on failure.
        """
        url = f"{self._base_url}/{post_id}/insights"

        # Try video metrics first; fall back to feed metrics on error
        metric_names = ",".join(_VIDEO_INSIGHTS_METRICS)
        params: dict[str, str] = {
            "metric": metric_names,
            "access_token": self._access_token,
        }

        try:
            resp = requests.get(url, params=params, timeout=30)
            response_data = _safe_json(resp)

            if resp.status_code != 200 or "error" in response_data:
                self._log.debug(
                    "Facebook: metrics request failed for %s (HTTP %d): %s",
                    post_id,
                    resp.status_code,
                    _extract_error_message(response_data),
                )
                return None

            data_items: list[dict[str, Any]] = response_data.get("data", [])
            if not data_items:
                self._log.debug("Facebook: no insights data for post %s", post_id)
                return None

            # Build a lookup from metric name → latest value
            metric_lookup: dict[str, int] = {}
            for item in data_items:
                name = item.get("name", "")
                values = item.get("values", [])
                if values:
                    # Use the most recent value
                    metric_lookup[name] = int(values[-1].get("value", 0))

            # Map to PlatformMetrics fields.
            # 2026-07-14 (X audit F3): prefer fb_reels_total_plays
            # over post_video_views. See _VIDEO_INSIGHTS_METRICS
            # comment above for v22→v23 cutover rationale.
            views = (
                metric_lookup.get("fb_reels_total_plays")
                or metric_lookup.get("post_video_views")
                or metric_lookup.get("post_video_views_organic")
                or metric_lookup.get("post_impressions")
                or 0
            )
            likes = metric_lookup.get("post_reactions_like_total") or 0
            comments = metric_lookup.get("post_comments", 0)
            shares = metric_lookup.get("post_shares", 0)

            return PlatformMetrics(
                views=views,
                likes=likes,
                comments=comments,
                shares=shares,
                extra={k: v for k, v in metric_lookup.items()},
            )

        except Exception as exc:
            self._log.warning("Facebook: get_metrics exception for %s: %s", post_id, exc)
            return None

    # ------------------------------------------------------------------
    # HealthCheckable protocol
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Post-survival check (audit R-33)
    # ------------------------------------------------------------------

    def check_post_alive(self, post_id: str) -> bool | None:
        """Verify a previously-published post is still live on the page.

        Used by the daily ``genlab-fb-survival-check`` job to detect
        Meta-removed reels (audit R-33).

        **Important asymmetry**: the Graph API's error codes
        intentionally conflate "object was deleted" with "object never
        existed / you don't have permission / wrong API surface for
        this object type" — all return ``code: 100`` (often with
        ``error_subcode: 33``) and the message "Object with ID X does
        not exist…". We CANNOT distinguish a real removal from a
        post-id-format mismatch or a permission slip from the response
        alone, so this primitive is intentionally conservative:

        Returns:
            * ``True``  — the post exists (HTTP 200, ``id`` present).
            * ``False`` — HTTP 410 (Gone) OR HTTP 400 with
              ``code=100 subcode=33`` and message containing
              "does not exist". The latter is Meta's response shape
              for a deleted post — live-probe of 5 known-deleted
              post_ids from the 2026-03-17 event confirmed all
              return this exact triple. Caller may flip the row to
              ``REMOVED_BY_META``.
            * ``None``  — everything else: HTTP 5xx, OAuth errors
              (102/190), network exceptions, code=100 with a
              different subcode/message, anything we can't
              interpret as a CONFIRMED removal. Caller MUST NOT
              mark removed.

        Historical note: originally the False branch fired on ANY
        ``code 100`` payload, but a prod backfill (2026-06-10)
        misclassified 352/404 legacy ``facebook:``-prefixed IDs.
        The prefix strip at line 537 fixes the root cause; the
        precise triple check above further narrows the False branch
        to only Meta's confirmed-deletion shape. The downstream
        ``run_check`` ``max_removal_rate`` guard is defense in
        depth against any residual false positives.

        PR #FB-Survival-Detect (2026-07-11): tightened conservatism
        after 4 months of zero takedown detection (post-Markanimation
        audit surfaced the gap).

        Also strips a ``platform:`` prefix (e.g. ``facebook:NNN``)
        before the request — see the legacy post_id note above.
        """
        # Defensive: tolerate the legacy ``facebook:NNN`` post_id shape
        # that survived an earlier publishing-analytics writer bug.
        clean_id = post_id.split(":", 1)[1] if post_id.startswith(("facebook:", "fb:")) else post_id

        url = f"{self._base_url}/{clean_id}"
        try:
            resp = requests.get(
                url,
                params={"fields": "id", "access_token": self._access_token},
                timeout=15,
            )
        except requests.RequestException as exc:
            self._log.debug("Facebook: survival check transient error for %s: %s", clean_id, exc)
            return None

        data = _safe_json(resp)

        if resp.status_code == 200 and data.get("id"):
            return True

        # HTTP 410 Gone remains an unambiguous confirmed-removal signal.
        if resp.status_code == 410:
            return False

        err_msg = _extract_error_message(data)
        err_obj = data.get("error", {}) if isinstance(data.get("error"), dict) else {}
        err_code = err_obj.get("code") if isinstance(err_obj, dict) else None
        err_subcode = err_obj.get("error_subcode") if isinstance(err_obj, dict) else None

        # PR #FB-Survival-Detect (2026-07-11, post-Markanimation audit):
        # Meta's Graph API returns HTTP 400 code=100 subcode=33 with
        # message "Object with ID X does not exist…" for deleted posts.
        # The prior implementation treated this as ambiguous because a
        # 2026-06-10 backfill misclassified 352/404 legacy ``facebook:``-
        # prefixed IDs. That prefix bug is now fixed at line 537 (strip
        # runs before the request), so the ONLY remaining false-positive
        # class is: page-permission drift (token lost access) or malformed
        # post_id shape. We rule out permission drift by only firing the
        # False on this specific error triple (code=100 subcode=33 +
        # "does not exist" substring); permission errors return code=200/
        # 190/10 with different messages. The ``run_check`` orchestrator
        # also has a ``max_removal_rate`` guard that aborts on abnormally
        # high removal counts per run — belt-and-suspenders.
        #
        # Root cause of the 4-month blind spot on takedowns: the previous
        # ``return None`` on code=100 meant every genuine removal looked
        # ambiguous → mark_removed never fired → REMOVED_BY_META count
        # stayed at 0. Live-probe of 5 known-deleted post_ids from the
        # 2026-03-17 mass-takedown event confirmed all return this exact
        # error shape.
        if (
            resp.status_code == 400
            and err_code == 100
            and err_subcode == 33
            and "does not exist" in (err_msg or "").lower()
        ):
            self._log.info(
                "Facebook: survival check CONFIRMED removal for %s "
                "(HTTP 400 code=100 subcode=33 'does not exist')",
                clean_id,
            )
            return False

        self._log.debug(
            "Facebook: survival check ambiguous for %s (HTTP %d, code=%s, subcode=%s): %s",
            clean_id,
            resp.status_code,
            err_code,
            err_subcode,
            err_msg,
        )
        return None

    def check_token_health(self) -> TokenStatus:
        """Verify the EAA Page Token by calling ``/me`` on the Graph API.

        EAA Page Tokens are permanent — ``expires_at=None``, ``needs_refresh=False``.

        Returns:
            :class:`~genlab_core.platforms.models.TokenStatus`
        """
        url = f"{self._base_url}/me"
        try:
            resp = requests.get(
                url,
                params={"access_token": self._access_token},
                timeout=15,
            )
            data = _safe_json(resp)
            if resp.status_code == 200 and "id" in data:
                name = data.get("name", "")
                return TokenStatus(
                    valid=True,
                    platform=self.platform_id,
                    expires_at=None,  # EAA page tokens are permanent
                    needs_refresh=False,
                    message=f"Token valid for page '{name}' (id={data['id']})",
                    details=data,
                )
            error_msg = data.get("error", {}).get("message", "") or f"HTTP {resp.status_code}"
            return TokenStatus(
                valid=False,
                platform=self.platform_id,
                expires_at=None,
                needs_refresh=False,
                message=f"Token check failed: {error_msg}",
                details=data,
            )
        except Exception as exc:
            return TokenStatus(
                valid=False,
                platform=self.platform_id,
                expires_at=None,
                needs_refresh=False,
                message=f"Token check exception: {exc}",
            )


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


# _safe_json imported from genlab_core.platforms.models


def _extract_error_message(data: dict[str, Any]) -> str:
    """Extract a human-readable error message from a Facebook API response."""
    error = data.get("error", {})
    if isinstance(error, dict):
        msg = error.get("message", "")
        code = error.get("code", "")
        if msg:
            return f"code={code}: {msg}" if code else msg
    return str(data)[:200] if data else "Unknown error"
