"""Instagram Graph API platform client.

Auth: META_ACCESS_TOKEN (EAA Page Token — permanent, never refresh).
ALWAYS use graph.facebook.com — NEVER graph.instagram.com.

Implements Publisher + Engageable + HealthCheckable protocols.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import requests

from genlab_core.platforms.meta_api import META_GRAPH_API_VERSION
from genlab_core.platforms.models import (
    InstagramSpecific,
    PublishPayload,
    PublishResult,
    TokenStatus,
)
from genlab_core.platforms.models import (
    safe_json as _safe_json,
)

logger = logging.getLogger(__name__)

# Poll config (kept short for tests; real use is fine because it's mocked)
_DEFAULT_MAX_POLL_SECONDS = 480
_POLL_INTERVAL_INITIAL = 5
_POLL_INTERVAL_SLOW = 10
_POLL_SLOWDOWN_AFTER = 30

# R-29: the whole reel publish (create + poll + one retry) MUST finish before
# the publisher's per-platform executor timeout (publish_all_platforms.py:
# `future.result(timeout=600)`). Previously a 480s poll + 30s + 480s retry could
# run ~990s — past the 600s future, which records the platform FAILED but does
# NOT cancel the thread (Python can't), so the orphaned thread could post the
# reel AFTER the publisher gave up → a phantom/duplicate publish. We bound the
# TOTAL wall-clock budget below the executor timeout and share it across the
# initial poll + retry, so the call always returns before the future times out.
# 2026-07-17 (audit round 4): 540 → 420. Combined with
# parallel_publish.DEFAULT_PUBLISH_TIMEOUT_SECONDS raise to 900,
# gives IG 480s slippage margin (was 60s). Prior 60s margin was
# insufficient — Meta's async container polling occasionally exceeded
# 540s → executor killed IG mid-poll at 600s → TimeoutError →
# ambiguous mark → permanent skip via retry_pass.py:177. Empirical
# result: 24/24 IG publishes failed with empty error strings over
# 30 days.
_TOTAL_PUBLISH_BUDGET_SECONDS = 420  # 420 IG budget + 900 executor timeout = 480s margin
_RETRY_MIN_REMAINING_SECONDS = 90  # 30s backoff + ≥60s for a useful second poll


class InstagramClient:
    """Meta Graph API client for Instagram publishing and engagement.

    All HTTP calls go to ``graph.facebook.com`` (never ``graph.instagram.com``).
    The EAA Page Token must never be refreshed via ``ig_refresh_token``.

    Args:
        access_token: Meta EAA Page Token. Defaults to ``META_ACCESS_TOKEN`` env var.
        ig_user_id: Instagram Business Account ID (FB-scoped).
                    Defaults to ``META_IG_USER_ID`` env var.
        api_version: Graph API version string. Defaults to
            ``META_GRAPH_API_VERSION`` from
            :mod:`genlab_core.platforms.meta_api` — the single source
            of truth bumped centrally (audit R-44).
        max_poll_seconds: Maximum seconds to wait for container processing.
                          BB uses 600; default is 120.
    """

    platform_id = "instagram"

    def __init__(
        self,
        access_token: str | None = None,
        ig_user_id: str | None = None,
        api_version: str = META_GRAPH_API_VERSION,
        max_poll_seconds: int = _DEFAULT_MAX_POLL_SECONDS,
        *,
        niche_id: str = "",
    ) -> None:
        """Construct an Instagram client.

        Token + IG user ID resolution priority (highest → lowest):
          1. Explicit ``access_token`` / ``ig_user_id`` kwargs (test fixtures,
             explicit operator override)
          2. Per-niche resolution via ``resolve_meta_credentials(niche_id)``
             when ``niche_id`` is non-empty (P2 phase 1, 2026-06-19)
          3. Global env vars ``META_ACCESS_TOKEN`` / ``META_IG_USER_ID``
             (legacy default — preserves backward compatibility for callers
             that haven't migrated to ``niche_id``)

        The ``niche_id`` kwarg is the per-niche migration path. Callers in
        publish_all_platforms / preflight / retry_pass can pass ``niche_id``
        to get correct per-channel token scoping
        (CLUTCHWIRE_META_ACCESS_TOKEN, CRITICALRUSH_META_ACCESS_TOKEN, etc.)
        instead of the global ``META_ACCESS_TOKEN``. The strict
        no-cross-channel-fallback rule from ``niche_credentials`` is honored.
        """
        self.niche_id = niche_id  # public so dispatcher can introspect
        self._access_token: str = self._resolve_access_token(access_token, niche_id)
        self._ig_user_id: str = self._resolve_ig_user_id(ig_user_id, niche_id)
        self._api_version = api_version
        self._base_url = f"https://graph.facebook.com/{api_version}"
        self._max_poll_seconds = max_poll_seconds
        self._last_error: str = ""  # Captures detailed error from internal methods
        # P2 phase 2 (2026-06-19): per-niche structured-logger adapter.
        # Every log record emitted via ``self._log`` carries
        # ``extra={"niche_id": <self.niche_id>, "platform": "instagram"}``
        # so operators can filter Loki/Splunk queries by niche cleanly
        # (e.g. ``{platform="instagram", niche_id="gaming"}`` JSON view).
        # The structlog wrapper in observability/logging.py promotes
        # ``extra`` keys into top-level JSON fields. Pattern proven; other
        # 4 platform clients can follow in a single ~50-LOC PR.
        # Falls back to module logger when niche_id is empty (legacy
        # callers that don't pass niche_id keep their old log shape).
        self._log: logging.LoggerAdapter | logging.Logger = (
            logging.LoggerAdapter(
                logger,
                {"niche_id": self.niche_id, "platform": self.platform_id},
            )
            if self.niche_id
            else logger
        )

    @staticmethod
    def _resolve_access_token(explicit: str | None, niche_id: str) -> str:
        """3-tier lookup: explicit kwarg → niche-resolved → global env fallback."""
        if explicit:
            return explicit
        if niche_id:
            from genlab_core.publishing.niche_credentials import resolve_meta_credentials

            tok = resolve_meta_credentials(niche_id).get("ig_access_token", "")
            if tok:
                return tok
        return os.environ.get("META_ACCESS_TOKEN", "")

    @staticmethod
    def _resolve_ig_user_id(explicit: str | None, niche_id: str) -> str:
        """3-tier lookup: explicit kwarg → niche-resolved → global env fallback.

        2026-07-14 fix (auth audit F1): route through
        ``resolve_meta_credentials`` (same as
        :meth:`_resolve_access_token`) instead of calling
        ``resolve_niche_env`` directly. Prior state passed
        ``niche_suffix="META_IG_USER_ID"`` which looks for
        ``{PREFIX}_META_IG_USER_ID`` in .env — but the actual .env
        uses ``{PREFIX}_IG_USER_ID`` (no ``META_`` in the suffix).
        Every non-BB niche's publish silently fell through to the
        global ``META_IG_USER_ID`` = **BlackboxBrief's IG account**.
        Cross-channel publish leak vector; sibling ``ig_access_token``
        path resolved correctly because it used ``resolve_meta_
        credentials`` which encodes the correct suffix at
        ``publishing/niche_credentials.py:78``.
        """
        if explicit:
            return explicit
        if niche_id:
            from genlab_core.publishing.niche_credentials import resolve_meta_credentials

            uid = resolve_meta_credentials(niche_id).get("ig_user_id", "")
            if uid:
                return uid
        return os.environ.get("META_IG_USER_ID", "")

    # ------------------------------------------------------------------
    # Publisher protocol
    # ------------------------------------------------------------------

    def publish(self, payload: PublishPayload) -> PublishResult:
        """Publish a Reel to Instagram.

        Flow:
          1. Validate that at least one media path was supplied.
          2. The caller is expected to have already uploaded the video to a
             public CDN and provided the URL; if ``media_paths`` contains a
             raw local path that starts with ``http`` it is used as-is.
             Otherwise we look for a ``video_url`` hint in
             ``payload.platform_specific`` or fall back to the first path's
             string representation so tests/callers that pass a URL as a
             ``Path`` still work.
          3. Create the REELS container.
          4. Poll the container status until ``FINISHED`` (or ``PUBLISHED``).
          5. POST to ``media_publish``.

        Returns a :class:`~genlab_core.platforms.models.PublishResult`.
        """
        if not payload.media_paths:
            return PublishResult(
                platform=self.platform_id,
                success=False,
                error="No media paths provided — video is required for Instagram Reels",
            )

        # PR #Layer4 (2026-07-11): publisher-side attribution validation.
        # See platforms/caption_validation.py + facebook.py for the pattern
        # and rationale — this is the API-POST boundary backstop.
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
                "[layer4] Instagram publish: caption missing attribution (niche=%s) — %s",
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

        # Resolve the video URL. Instagram requires a public HTTPS URL.
        # If the path is a local file, upload to temp CDN first.
        first_path = payload.media_paths[0]
        video_url = str(first_path)

        # Build caption with hashtags (avoid duplication — caption may already
        # contain inline hashtags from the writing stage)
        caption = payload.caption
        if payload.hashtags:
            existing_tags = set(t.lower() for t in re.findall(r"#\w+", caption))
            new_tags = [t for t in payload.hashtags if t.lower() not in existing_tags]
            if new_tags:
                caption = f"{caption}\n\n{' '.join(new_tags)}"
        caption = caption[:2200]

        # Platform-specific options
        ig_specific = payload.platform_specific
        share_to_feed: bool = True
        cover_url: str = ""
        if isinstance(ig_specific, InstagramSpecific):
            share_to_feed = ig_specific.share_to_feed
            cover_url = ig_specific.cover_url

        # R-29 (2026-07-08 revision): create a SINGLE shared deadline for the
        # whole publish() call — including any CDN-provider retries. Previously
        # each ``_publish_reel`` call would default its own 540s ``_deadline``,
        # so a CDN retry could stack (60s upload + 540s publish) × 2 = 1200s,
        # blowing past the 600s executor timeout in
        # ``parallel_publish.execute_parallel_publish``. 2026-07-07 live-fire:
        # 2 of 5 niches hit "Publish timed out after 600s for instagram"
        # exactly because of this stacking; anime never got processed because
        # the resulting service TERM at 30 min killed the sequential loop.
        # See task #572 for the full analysis.
        shared_deadline = time.monotonic() + _TOTAL_PUBLISH_BUDGET_SECONDS

        # Fast path: caller already supplied a public URL — skip CDN entirely.
        if video_url.startswith("http"):
            self._last_error = ""
            post_id = self._publish_reel(
                video_url=video_url,
                caption=caption,
                share_to_feed=share_to_feed,
                cover_url=cover_url,
                max_poll_seconds=self._max_poll_seconds,
                _deadline=shared_deadline,
            )
            if post_id is None:
                return PublishResult(
                    platform=self.platform_id,
                    success=False,
                    error=self._last_error or "Instagram Reel publish failed — unknown error",
                )
            result = self._build_publish_success(post_id)
            self._post_first_comment_if_present(result, payload)
            return result

        # Local path — upload to CDN and retry against a different provider on
        # Meta container error 2207077 ("media upload failed", i.e. Meta's
        # fetcher could not download from the URL we gave it). The inner CDN
        # helpers (litterbox + tmpfiles) each have their own attempt loop for
        # ephemeral upload errors; the outer loop here is for the Meta-side
        # 2207077 case where the upload succeeded but Meta still can't fetch.
        # Prod data (ai_creators, last 60 days): 8/8 IG failures were 2207077
        # on litterbox URLs — without this loop every one was a silent slot
        # burn. See PR fix(platforms): retry IG/Threads CDN on Meta 2207077.
        from genlab_core.platforms.cdn_upload import (
            PROVIDER_LITTERBOX,
            PROVIDER_TMPFILES,
            upload_to_cdn_full,
        )

        provider_order = (PROVIDER_LITTERBOX, PROVIDER_TMPFILES)
        excluded: frozenset[str] = frozenset()
        last_failure_error = ""

        for _ in provider_order:
            # R-29 (2026-07-08 revision): honour the shared deadline BEFORE
            # spending time on a fresh CDN upload. If <90s remain, another
            # provider iteration can't complete a create + poll + publish
            # cycle before the executor kills us — bail with a clean error
            # so the outer publisher records FAILED cleanly instead of the
            # thread outliving the executor and posting a phantom reel.
            remaining = shared_deadline - time.monotonic()
            if remaining < _RETRY_MIN_REMAINING_SECONDS:
                self._log.warning(
                    "[IG] Skipping CDN retry — only %.0fs of the publish budget "
                    "left (< %ds needed for another provider attempt). Bailing "
                    "so the executor doesn't have to kill an orphaned thread.",
                    remaining,
                    _RETRY_MIN_REMAINING_SECONDS,
                )
                return PublishResult(
                    platform=self.platform_id,
                    success=False,
                    error=(
                        f"Instagram: publish budget exhausted after "
                        f"{_TOTAL_PUBLISH_BUDGET_SECONDS - int(remaining)}s "
                        f"across CDN retries. Excluded providers: {sorted(excluded)}. "
                        f"Last error: {last_failure_error or 'none yet'}"
                    ),
                )

            cdn_result = upload_to_cdn_full(
                video_url,
                require_external=True,
                exclude_providers=excluded,
            )
            if cdn_result is None:
                # All non-excluded providers refused the upload (e.g. file too
                # large, both circuits open). Trying again with no provider
                # change will give the same answer — bail.
                tunnel = os.environ.get("CLOUDFLARE_TUNNEL_URL", "")
                from pathlib import Path as _Path

                exists = _Path(video_url).exists() if video_url else False
                return PublishResult(
                    platform=self.platform_id,
                    success=False,
                    error=(
                        f"CDN upload failed for Instagram (exhausted providers "
                        f"{sorted(excluded)}, file_exists={exists},"
                        f" tunnel={'set' if tunnel else 'unset'},"
                        f" path={video_url[-60:]})"
                    ),
                )

            cdn_url = cdn_result.url
            used_provider = cdn_result.provider
            self._last_error = ""
            post_id = self._publish_reel(
                video_url=cdn_url,
                caption=caption,
                share_to_feed=share_to_feed,
                cover_url=cover_url,
                max_poll_seconds=self._max_poll_seconds,
                _deadline=shared_deadline,
            )

            if post_id is not None:
                result = self._build_publish_success(post_id)
                self._post_first_comment_if_present(result, payload)
                return result

            # Publish failed. If the failure is NOT a Meta CDN-fetch error,
            # retrying with a different provider can't help — return now.
            last_failure_error = self._last_error or "unknown error"
            if "2207077" not in last_failure_error:
                return PublishResult(
                    platform=self.platform_id,
                    success=False,
                    error=last_failure_error,
                )

            # 2207077 with this provider's URL — exclude it and try the next
            # tier on the next iteration.
            self._log.warning(
                "[IG] 2207077 with %s URL — retrying with different CDN provider",
                used_provider,
            )
            excluded = excluded | {used_provider}

        # Loop exhausted on 2207077 across every provider.
        return PublishResult(
            platform=self.platform_id,
            success=False,
            error=(
                f"Instagram: 2207077 on all CDN providers ({list(provider_order)})"
                f" — last error: {last_failure_error}"
            ),
        )

    def _build_publish_success(self, post_id: str) -> PublishResult:
        """Build a SUCCESS PublishResult after a confirmed publish.

        Fetches the real permalink (numeric Graph IDs don't resolve as ``/p/``
        URLs) but falls back to a synthesised reel URL on any error — the
        post is already live, the permalink is purely for dashboard display.
        """
        real_url = f"https://www.instagram.com/reel/{post_id}/"
        try:
            permalink_resp = self._graph_get(f"/{post_id}", params={"fields": "permalink"})
            if permalink_resp and "permalink" in permalink_resp:
                real_url = permalink_resp["permalink"]
        except Exception:
            pass  # fall back to numeric ID URL

        return PublishResult(
            platform=self.platform_id,
            success=True,
            post_id=post_id,
            post_url=real_url,
        )

    def _post_first_comment_if_present(self, result: PublishResult, payload: PublishPayload) -> None:
        """Post the affiliate first-comment after successful IG publish.

        2026-07-17 (Layer 2 monetization): IG allows external URLs in
        comments (unlike caption where they downrank to search-only
        reach). Pinning an affiliate comment on publish gets 2-8% CTR
        vs 0.1-0.3% bio-link CTR = 20-80× click improvement with zero
        extra traffic. Best-effort — a failure here doesn't fail the
        publish (mirrors Facebook's pattern at facebook.py:208).
        """
        if not (result.success and payload.first_comment_text and result.post_id):
            return
        try:
            ok = self.post_reply(
                parent_id=result.post_id,
                text=payload.first_comment_text,
                context_id=result.post_id,
            )
            if ok:
                self._log.info(
                    "Instagram: affiliate first-comment posted under %s",
                    result.post_id,
                )
            else:
                self._log.warning(
                    "Instagram: first-comment post returned False for %s",
                    result.post_id,
                )
        except Exception as exc:
            self._log.warning(
                "Instagram: first-comment post exception under %s: %s",
                result.post_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Engageable protocol
    # ------------------------------------------------------------------

    def post_reply(self, parent_id: str, text: str, *, context_id: str = "") -> bool:
        """Reply to an Instagram comment.

        Args:
            parent_id: The comment ID to reply to.
            text: Reply text.
            context_id: Optional media ID (used for logging only).

        Returns:
            ``True`` on success, ``False`` on any failure.
        """
        url = f"{self._base_url}/{parent_id}/replies"
        try:
            resp = requests.post(
                url,
                data={"message": text, "access_token": self._access_token},
                timeout=15,
            )
            data = _safe_json(resp)
            if resp.status_code == 200 and "id" in data:
                self._log.info("Instagram: replied to comment %s (media=%s)", parent_id, context_id)
                return True
            self._log.warning(
                "Instagram: reply failed (HTTP %d): %s",
                resp.status_code,
                data.get("error", {}).get("message", str(data)),
            )
            return False
        except Exception as exc:
            self._log.warning("Instagram: post_reply exception: %s", exc)
            return False

    def like(self, target_id: str, *, context_id: str = "") -> bool:
        """Like an Instagram comment.

        Note: Meta's Graph API does not support liking comments on behalf
        of a Page using EAA tokens.  This is a no-op that always returns
        ``True`` to keep the engagement pipeline happy.
        """
        self._log.debug(
            "Instagram: like() is a no-op (API unsupported for EAA tokens) — target=%s",
            target_id,
        )
        return True

    # ------------------------------------------------------------------
    # HealthCheckable protocol
    # ------------------------------------------------------------------

    def check_token_health(self) -> TokenStatus:
        """Verify the access token by calling ``/me`` on the Graph API.

        Returns:
            :class:`~genlab_core.platforms.models.TokenStatus` with
            ``valid=True`` when the token is accepted.
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
                    message=f"Token valid for account '{name}' (id={data['id']})",
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
    # Channel verification
    # ------------------------------------------------------------------

    def verify_channel(self) -> bool:
        """Verify the IG Business Account exists and is accessible.

        Makes a lightweight GET to ``/{ig_user_id}?fields=id,username`` to
        confirm the token + account ID combination is valid.  Useful as a
        pre-flight check before attempting a publish.

        Returns:
            ``True`` if the account is accessible, ``False`` otherwise.
        """
        url = f"{self._base_url}/{self._ig_user_id}"
        try:
            resp = requests.get(
                url,
                params={
                    "fields": "id,username",
                    "access_token": self._access_token,
                },
                timeout=15,
            )
            data = _safe_json(resp)
            if resp.status_code == 200 and "id" in data:
                username = data.get("username", "")
                self._log.info(
                    "Instagram: channel verified — id=%s username=%s",
                    data["id"],
                    username,
                )
                return True
            error_msg = data.get("error", {}).get("message", "") or f"HTTP {resp.status_code}"
            self._log.error("Instagram: channel verification failed: %s", error_msg)
            return False
        except Exception as exc:
            self._log.error("Instagram: channel verification exception: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _publish_reel(
        self,
        *,
        video_url: str,
        caption: str,
        share_to_feed: bool = True,
        cover_url: str = "",
        max_poll_seconds: int = _DEFAULT_MAX_POLL_SECONDS,
        _retry_count: int = 0,
        _deadline: float | None = None,
    ) -> str | None:
        """Three-step reel publish: create container → poll → publish.

        Returns the published post ID on success, or ``None`` on failure.
        Retries once if the container hits ERROR quickly (Meta processor lag).

        R-29: ``_deadline`` is a monotonic wall-clock budget shared across the
        initial attempt and the retry so the TOTAL time stays under the
        publisher's 600s executor timeout — otherwise an orphaned thread could
        post the reel after the publisher already recorded it FAILED.
        """
        if _deadline is None:
            _deadline = time.monotonic() + _TOTAL_PUBLISH_BUDGET_SECONDS

        creation_id = self._create_reel_container(
            video_url=video_url,
            caption=caption,
            share_to_feed=share_to_feed,
            cover_url=cover_url,
        )
        if creation_id is None:
            return None

        # Cap this poll to whatever budget remains before the shared deadline.
        poll_budget = min(max_poll_seconds, max(1, int(_deadline - time.monotonic())))
        already_published = self._poll_container_status(
            creation_id=creation_id,
            max_poll_seconds=poll_budget,
        )
        if already_published is None:
            # 2207077 = "media upload failed". Meta keys this off the URL we
            # gave it; another attempt against the same URL almost always
            # fails the same way. Bail out so the publisher's outer retry
            # loop can re-upload to a fresh CDN URL instead of burning 30s.
            if "2207077" in (self._last_error or ""):
                self._log.warning(
                    "Reel container failed with 2207077 — skipping in-client retry "
                    "(outer publisher loop will retry with a fresh CDN URL)"
                )
                return None
            # Container ERROR or timeout — retry once after 30s
            # Meta's processor sometimes returns ERROR immediately on
            # transient overload, but succeeds on a second attempt.
            # R-29: only retry if enough of the shared budget remains for a
            # meaningful second poll — otherwise the retry would push the total
            # past the executor timeout (orphaned-thread risk).
            remaining = _deadline - time.monotonic()
            if _retry_count < 1 and remaining > _RETRY_MIN_REMAINING_SECONDS:
                self._log.warning(
                    "Reel container ERROR — retrying in 30s (attempt %d/2, %.0fs budget left)",
                    _retry_count + 1,
                    remaining,
                )
                time.sleep(30)
                return self._publish_reel(
                    video_url=video_url,
                    caption=caption,
                    share_to_feed=share_to_feed,
                    cover_url=cover_url,
                    max_poll_seconds=max_poll_seconds,
                    _retry_count=_retry_count + 1,
                    _deadline=_deadline,
                )
            if _retry_count < 1:
                self._log.warning(
                    "Reel container ERROR — skipping retry (only %.0fs of the publish "
                    "budget left; staying under the executor timeout to avoid an "
                    "orphaned thread)",
                    remaining,
                )
            return None

        if already_published:
            # Container transitioned to PUBLISHED during polling; skip media_publish
            self._log.info("Reel already PUBLISHED during polling — skipping media_publish")
            return creation_id

        return self._media_publish(creation_id=creation_id)

    def _preflight_video_url(self, video_url: str) -> str | None:
        """HEAD-check the CDN URL is fetchable by Meta.

        2026-07-17 (Layer 1 batch 2): Meta's REELS container-creation
        polls the URL server-side; if the CDN returned 404 / non-video
        content-type / expired, Meta fails with generic 2207077
        ("media upload failed") after burning 30-60s. Local pre-check
        skips the wasted round-trip. Returns error string on failure,
        None on success.
        """
        try:
            head = requests.head(video_url, timeout=15, allow_redirects=True)
        except Exception as exc:
            return f"CDN URL preflight failed ({type(exc).__name__}): {exc}"
        if head.status_code != 200:
            return f"CDN URL preflight returned HTTP {head.status_code}"
        ctype = (head.headers.get("Content-Type") or "").lower()
        if not (ctype.startswith("video/") or ctype == "application/octet-stream"):
            return f"CDN URL preflight returned unexpected Content-Type: {ctype!r}"
        return None

    def _create_reel_container(
        self,
        *,
        video_url: str,
        caption: str,
        share_to_feed: bool,
        cover_url: str,
    ) -> str | None:
        """POST to /{ig_user_id}/media to create the REELS container.

        Returns the creation_id on success, ``None`` on failure.
        """
        # 2026-07-17 (Layer 1 batch 2): preflight CDN URL to catch
        # 404 / wrong-content-type / expired-URL cases locally instead
        # of burning 30-60s on Meta's async container polling. Only
        # runs for http(s) URLs — Meta accepts file:// only via a
        # different upload path.
        if video_url.startswith(("http://", "https://")):
            preflight_error = self._preflight_video_url(video_url)
            if preflight_error is not None:
                self._last_error = f"Preflight rejected: {preflight_error}"
                self._log.warning(
                    "Skipping Meta container-create; %s (url tail=%s)",
                    preflight_error,
                    video_url[-60:],
                )
                return None

        url = f"{self._base_url}/{self._ig_user_id}/media"
        data: dict[str, Any] = {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": str(share_to_feed).lower(),
            "access_token": self._access_token,
        }
        if cover_url:
            data["cover_url"] = cover_url

        try:
            resp = requests.post(url, data=data, timeout=60)
            payload = _safe_json(resp)
            if "id" in payload:
                self._log.info("Reel container created: %s", payload["id"])
                return payload["id"]
            error_msg = payload.get("error", {}).get("message", str(payload))
            self._last_error = f"Container creation failed: {error_msg}"
            self._log.error("Reel container creation failed: %s", error_msg)
            return None
        except Exception as exc:
            self._last_error = f"Container request error: {exc}"
            self._log.error("Reel container request error: %s", exc)
            return None

    def _poll_container_status(
        self,
        *,
        creation_id: str,
        max_poll_seconds: int,
    ) -> bool | None:
        """Poll container status until FINISHED or timeout.

        Returns:
            ``True``  — container is already PUBLISHED (skip media_publish).
            ``False`` — container is FINISHED (proceed to media_publish).
            ``None``  — timed out or encountered an ERROR status.
        """
        status_url = f"{self._base_url}/{creation_id}"
        status_params = {
            "fields": "status_code,status",
            "access_token": self._access_token,
        }
        poll_start = time.time()
        poll_interval = _POLL_INTERVAL_INITIAL

        while True:
            elapsed = time.time() - poll_start
            if elapsed > max_poll_seconds:
                self._last_error = f"Container polling timed out after {max_poll_seconds}s"
                self._log.error(
                    "Reel container polling timed out after %ds (container=%s)",
                    max_poll_seconds,
                    creation_id,
                )
                return None

            try:
                resp = requests.get(status_url, params=status_params, timeout=30)
                data = _safe_json(resp)
                status_code = data.get("status_code", "UNKNOWN")

                if status_code == "FINISHED":
                    self._log.info(
                        "Reel container processing complete (%.0fs): %s",
                        elapsed,
                        creation_id,
                    )
                    return False  # Proceed to media_publish

                if status_code == "PUBLISHED":
                    return True  # Already live, skip media_publish

                if status_code == "ERROR":
                    # status field contains human-readable error detail
                    error_detail = data.get("status", "")
                    self._last_error = f"Container processing error: {error_detail or data}"
                    self._log.error(
                        "Reel container processing error: %s (detail: %s)",
                        data,
                        error_detail,
                    )
                    return None

                # IN_PROGRESS or UNKNOWN — keep waiting
                self._log.debug("Reel container status=%s (%.0fs elapsed)", status_code, elapsed)
            except Exception as exc:
                self._log.warning("Reel status poll error (retrying): %s", exc)

            time.sleep(poll_interval)
            if elapsed > _POLL_SLOWDOWN_AFTER:
                poll_interval = _POLL_INTERVAL_SLOW

    def _media_publish(self, *, creation_id: str) -> str | None:
        """POST to /{ig_user_id}/media_publish to finalise the reel.

        Returns the published post ID on success, ``None`` on failure.
        """
        url = f"{self._base_url}/{self._ig_user_id}/media_publish"
        data = {
            "creation_id": creation_id,
            "access_token": self._access_token,
        }
        try:
            resp = requests.post(url, data=data, timeout=60)
            payload = _safe_json(resp)
            if "id" in payload:
                self._log.info("Reel published — post ID: %s", payload["id"])
                return payload["id"]
            error_msg = payload.get("error", {}).get("message", str(payload))
            self._last_error = f"media_publish failed: {error_msg}"
            self._log.error("Reel media_publish failed: %s", error_msg)
            return None
        except Exception as exc:
            self._last_error = f"media_publish request error: {exc}"
            self._log.error("Reel media_publish request error: %s", exc)
            return None

    def _graph_get(self, path: str, params: dict | None = None) -> dict:
        """GET request to the Graph API. Returns parsed JSON or empty dict."""
        url = f"{self._base_url}{path}"
        all_params = {"access_token": self._access_token}
        if params:
            all_params.update(params)
        try:
            resp = requests.get(url, params=all_params, timeout=15)
            return _safe_json(resp)
        except Exception as exc:
            # WARNING (not silent {}): callers (token verify, media
            # status poll, insights) currently can't distinguish
            # "Meta returned no data" from "network / DNS / timeout".
            # Publish-time media polls loop against {} instead of
            # backing off on transient network flakes.
            self._log.warning("_graph_get failed for %s: %s", path, exc)
            return {}


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

# _safe_json imported from genlab_core.platforms.models
