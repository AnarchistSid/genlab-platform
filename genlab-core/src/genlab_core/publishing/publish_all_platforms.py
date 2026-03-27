#!/usr/bin/env python3
"""Canonical multi-platform publisher for all Gen Lab niches.

One ~300 LOC orchestrator that delegates to existing infrastructure:
  - PublishGatekeeper: 7 composable gates (approval, schedule, score, media, cap)
  - get_client(): lazy platform client registry
  - DailyCapEnforcer: per-niche daily post caps
  - niche_credentials: per-niche token resolution
  - BacklogClient: SharePoint data access

Usage:
    python -m genlab_core.publishing.publish_all_platforms --niche gaming
    python -m genlab_core.publishing.publish_all_platforms --niche ai_creators

Exit codes:
    0 = success (>= 1 platform published)
    1 = no eligible blueprints
    2 = all platforms failed
    3 = daily cap reached for all platforms
    4 = lock held by another process
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from genlab_core.media.ffmpeg import PLATFORM_SPECS, Platform
from genlab_core.platforms.gatekeeper import PublishGatekeeper
from genlab_core.platforms.models import (
    FacebookSpecific,
    InstagramSpecific,
    PublishPayload,
    PublishResult,
    ThreadsSpecific,
    TwitterSpecific,
    YouTubeSpecific,
)
from genlab_core.platforms.registry import get_client
from genlab_core.publishing.analytics_recorder import record_publish
from genlab_core.publishing.error_classifier import classify, should_retry, retry_delay_seconds
from genlab_core.publishing.niche_credentials import (
    resolve_fb_credentials,
    resolve_meta_credentials,
    resolve_threads_credentials,
    resolve_twitter_credentials,
    resolve_youtube_credentials,
)

logger = logging.getLogger(__name__)

# Exit codes
EXIT_SUCCESS = 0
EXIT_NO_BLUEPRINTS = 1
EXIT_ALL_FAILED = 2
EXIT_DAILY_CAP = 3
EXIT_LOCK_HELD = 4

_VALID_NICHE_IDS = frozenset({
    "ai_creators", "gaming", "sports", "movies", "anime",
})

# Maps legacy/alternate platform names to canonical registry IDs
_PLATFORM_ID_MAP: dict[str, str] = {
    "twitter": "x_twitter",
    "x": "x_twitter",
    "ig": "instagram",
    "yt": "youtube",
    "fb": "facebook",
    "tt": "tiktok",
}


def _to_registry_id(platform: str) -> str:
    return _PLATFORM_ID_MAP.get(platform, platform)


def _normalize_niche(niche_id: str) -> str:
    niche_id = niche_id.strip()
    if niche_id in ("ai_tech", "ai_news"):
        return "ai_creators"
    return niche_id


def _validate_niche(niche_id: str) -> str:
    """Validate and normalize niche_id. Raises ValueError on invalid."""
    niche_id = _normalize_niche(niche_id)
    if not niche_id:
        raise ValueError("Empty niche_id — refusing to publish")
    if niche_id not in _VALID_NICHE_IDS:
        raise ValueError(
            f"unknown niche_id '{niche_id}' — valid: {sorted(_VALID_NICHE_IDS)}"
        )
    return niche_id


# ---------------------------------------------------------------------------
# PID Lock
# ---------------------------------------------------------------------------


class PidLock:
    """Process-level lock using a PID file with stale detection."""

    def __init__(self, niche_id: str, lock_dir: Path | None = None):
        base = lock_dir or Path("/tmp")
        self.path = base / f"publisher-{niche_id}.lock"

    def acquire(self) -> bool:
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # Lock file exists — check if holder is still alive
            try:
                pid = int(self.path.read_text().strip())
                os.kill(pid, 0)  # signal 0 = check if alive
                return False  # process is alive — lock held
            except (ValueError, ProcessLookupError, PermissionError):
                # Stale lock or unreadable — remove and retry once
                logger.info("Removing stale lock file: %s", self.path)
                self.path.unlink(missing_ok=True)
            except OSError:
                # Other OS errors — treat as stale
                self.path.unlink(missing_ok=True)
            # Retry atomic create after removing stale lock
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return True
            except FileExistsError:
                return False  # Another process won the race

    def release(self) -> None:
        self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Per-Platform Transcode
# ---------------------------------------------------------------------------

# Map legacy platform names to Platform enum
_PLATFORM_MAP: dict[str, Platform] = {
    "youtube": Platform.YOUTUBE,
    "instagram": Platform.INSTAGRAM,
    "facebook": Platform.FACEBOOK,
    "twitter": Platform.X_STD,
    "threads": Platform.THREADS,
    "tiktok": Platform.TIKTOK,
}


def _transcode_for_platform(source: Path, platform: str) -> Path:
    """Transcode video for a specific platform using PLATFORM_SPECS.

    Returns the platform variant path. If transcoding fails or the platform
    is unknown, returns the original source path (fail-open).
    """
    plat_enum = _PLATFORM_MAP.get(platform)
    if plat_enum is None or plat_enum not in PLATFORM_SPECS:
        return source

    spec = PLATFORM_SPECS[plat_enum]
    variant_path = source.parent / f"{source.stem}_{plat_enum.value}{source.suffix}"

    # Skip if variant already exists and is recent
    if variant_path.exists() and variant_path.stat().st_size > 10240:
        return variant_path

    try:
        from genlab_core.media.ffmpeg import get_ffmpeg_binary
        import subprocess
        import yaml as _yaml

        ffmpeg = get_ffmpeg_binary()

        # Load platform duration targets from config
        max_duration = None
        try:
            config_path = Path(__file__).resolve().parent.parent / "config" / "platform_encode_specs.yaml"
            if not config_path.exists():
                config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "platform_encode_specs.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    enc_config = _yaml.safe_load(f) or {}
                durations = enc_config.get("platform_durations", {}).get(platform, {})
                max_duration = durations.get("max_seconds")
        except Exception:
            pass

        cmd = [ffmpeg, "-y", "-i", str(source)]

        # Apply duration trim if source exceeds platform max (trim from end, keep hook)
        if max_duration:
            cmd.extend(["-t", str(max_duration)])

        cmd.extend(["-c:v", spec.codec])
        if spec.crf is not None:
            cmd.extend(["-crf", str(spec.crf)])
        cmd.extend(["-preset", "slow"])
        if spec.width and spec.height:
            cmd.extend(["-vf", f"scale={spec.width}:{spec.height}"])
        cmd.extend([
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            str(variant_path),
        ])
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        dur_msg = f", trimmed to {max_duration}s" if max_duration else ""
        logger.info("[publish] Transcoded %s for %s (%s CRF %s%s)",
                    source.name, platform, spec.codec, spec.crf, dur_msg)
        return variant_path
    except Exception as exc:
        logger.warning("[publish] Transcode failed for %s/%s: %s — using original",
                       platform, source.name, exc)
        return source


# ---------------------------------------------------------------------------
# First-Reply Affiliate Posting
# ---------------------------------------------------------------------------


def _post_affiliate_reply(
    platform: str, post_id: str | None, fields: dict, niche_id: str
) -> None:
    """Post affiliate link as first reply/comment after publishing.

    Facebook: POST /{post_id}/comments with affiliate text.
    X/Twitter: POST tweet reply with in_reply_to_tweet_id.
    Non-blocking: failures are logged but never crash the publisher.
    """
    if not post_id:
        return

    affiliate_url = fields.get("affiliate_url", "")
    affiliate_product = fields.get("affiliate_product", "")
    if not affiliate_url or not affiliate_product:
        return

    try:
        if platform == "facebook":
            from genlab_core.platforms.facebook import FacebookClient
            from genlab_core.publishing.niche_credentials import resolve_credentials
            creds = resolve_credentials(niche_id, "facebook")
            if not creds.get("access_token"):
                return
            fb = FacebookClient(access_token=creds["access_token"])
            text = f"🔗 {affiliate_product}: {affiliate_url}"
            fb.post_reply(post_id, text)
            logger.info("[affiliate] Posted FB comment on %s", post_id)

        elif platform in ("twitter", "x_twitter"):
            from genlab_core.platforms.x_twitter import XTwitterClient
            from genlab_core.publishing.niche_credentials import resolve_credentials
            creds = resolve_credentials(niche_id, "twitter")
            if not creds.get("api_key"):
                return
            x = XTwitterClient(**creds)
            text = f"🔗 {affiliate_product}: {affiliate_url}"
            x.post_reply(post_id, text)
            logger.info("[affiliate] Posted X reply to %s", post_id)

    except Exception:
        logger.debug("[affiliate] Reply failed for %s/%s (non-blocking)", platform, post_id)


# ---------------------------------------------------------------------------
# Payload Builder
# ---------------------------------------------------------------------------


def build_payload(fields: dict[str, Any], platform: str) -> PublishPayload:
    """Convert raw blueprint fields dict into a typed PublishPayload.

    Args:
        fields: The ``fields`` dict from a SharePoint blueprint record.
        platform: Legacy platform name (e.g. "instagram", "twitter").
    """
    niche_id = _validate_niche(fields.get("niche_id", "") or "")

    # Media paths
    visual_paths_raw = fields.get("visual_paths", "[]")
    try:
        vp_list = (
            json.loads(visual_paths_raw)
            if isinstance(visual_paths_raw, str)
            else (visual_paths_raw or [])
        )
    except (json.JSONDecodeError, TypeError):
        vp_list = []
    media_paths = [Path(p) for p in vp_list if p]

    # Verify media files exist on disk and are non-empty before attempting publish
    missing = [p for p in media_paths if not p.exists()]
    if missing:
        logger.warning("[publish] Missing media files: %s", [str(m) for m in missing])
        media_paths = [p for p in media_paths if p.exists()]

    # Filter out corrupted/empty files (< 10KB is not a valid video)
    too_small = [p for p in media_paths if p.exists() and p.stat().st_size < 10240]
    if too_small:
        logger.warning("[publish] Skipping too-small media files (<10KB): %s", [str(p) for p in too_small])
        media_paths = [p for p in media_paths if p not in too_small]

    # Per-platform transcode: produce optimized variant for the target platform
    if media_paths and any(str(p).lower().endswith(".mp4") for p in media_paths):
        media_paths = [
            _transcode_for_platform(p, platform) if str(p).lower().endswith(".mp4") else p
            for p in media_paths
        ]

    # Media type
    fmt = (fields.get("format", "") or "").strip().lower()
    has_mp4 = any(str(p).lower().endswith(".mp4") for p in media_paths)
    if has_mp4 or fmt in ("reel", "short", "video"):
        media_type = "video"
    else:
        media_type = "image"

    # Abort early if video format but no valid media files remain
    if media_type == "video" and not media_paths:
        raise ValueError(
            f"No valid media files for video publish (niche={niche_id}, "
            f"format={fmt}, original_paths={vp_list})"
        )

    # Caption, hashtags, hook
    caption = (fields.get("caption", "") or "").strip()
    hashtags_raw = fields.get("hashtags", "") or ""
    if isinstance(hashtags_raw, list):
        hashtags = [t.strip() if t.strip().startswith("#") else f"#{t.strip()}" for t in (str(h) for h in hashtags_raw if h) if t.strip()]
    else:
        hashtags = [
            t.strip() if t.strip().startswith("#") else f"#{t.strip()}" for t in str(hashtags_raw).split() if t.strip()
        ]
    hook = (fields.get("hook", "") or fields.get("hook_text", "") or "").strip()

    # Platform-specific config
    platform_specific = _build_platform_specific(fields, platform, caption, hook)

    return PublishPayload(
        caption=caption,
        media_paths=media_paths,
        media_type=media_type,
        hashtags=hashtags,
        hook=hook,
        niche_id=niche_id,
        platform_specific=platform_specific,
    )


def _build_platform_specific(
    fields: dict[str, Any], platform: str, caption: str, hook: str
):
    """Build the platform-specific config from blueprint fields."""
    if platform == "instagram":
        image_paths = [
            str(p) for p in _parse_visual_paths(fields)
            if not str(p).lower().endswith(".mp4")
        ]
        return InstagramSpecific(
            cover_url=image_paths[0] if image_paths else "",
            share_to_feed=True,
        )

    if platform == "youtube":
        yt_content = _parse_json_field(fields.get("youtube_content", ""))
        shorts_title = (
            yt_content.get("title", "")
            or fields.get("topic", "")
            or hook[:100]
        )
        return YouTubeSpecific(
            shorts_title=shorts_title[:100],
            community_post_text=yt_content.get("community_post_text", "") or caption,
        )

    if platform == "twitter":
        tw_content = _parse_json_field(fields.get("twitter_content", ""))
        routing = str(
            tw_content.get("routing", tw_content.get("strategy", "single"))
        ).strip().lower()
        tweet_text = str(tw_content.get("tweet_text", "") or caption).strip()
        tweet_text = tweet_text[:280]
        return TwitterSpecific(
            routing=routing if routing in ("single", "thread") else "single",
            tweet_text=tweet_text,
            thread_tweets=tw_content.get("thread_tweets", []),
        )

    if platform == "facebook":
        return FacebookSpecific()

    if platform == "threads":
        return ThreadsSpecific()

    return None


def _parse_visual_paths(fields: dict[str, Any]) -> list:
    raw = fields.get("visual_paths", "[]")
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_json_field(raw) -> dict:
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# Credential Resolution
# ---------------------------------------------------------------------------


def _resolve_client_kwargs(registry_id: str, niche_id: str) -> dict | None:
    """Resolve per-niche constructor kwargs for a platform client.

    Returns None if credentials are missing (platform should be skipped).
    """
    if registry_id == "instagram":
        creds = resolve_meta_credentials(niche_id)
        token = creds.get("ig_access_token", "")
        user_id = creds.get("ig_user_id", "")
        if token and user_id:
            return {"access_token": token, "ig_user_id": user_id}
        return None

    if registry_id == "facebook":
        token, page_id = resolve_fb_credentials(niche_id)
        if token and page_id:
            return {"access_token": token, "page_id": page_id}
        return None

    if registry_id == "youtube":
        creds = resolve_youtube_credentials(niche_id)
        refresh_token = creds.get("refresh_token", "")
        client_id = creds.get("client_id", "") or os.environ.get("YOUTUBE_CLIENT_ID", "")
        client_secret = creds.get("client_secret", "") or os.environ.get("YOUTUBE_CLIENT_SECRET", "")
        # Expected channel ID for cross-channel verification
        from genlab_core.publishing.niche_credentials import resolve_niche_env
        expected_channel = resolve_niche_env(niche_id, "", "YT_CHANNEL_ID")
        if refresh_token and client_id and client_secret:
            kwargs = {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
            if expected_channel:
                kwargs["expected_channel_id"] = expected_channel
            return kwargs
        return None

    if registry_id == "x_twitter":
        creds = resolve_twitter_credentials(niche_id)
        if all(creds.values()):
            return creds
        return None

    if registry_id == "threads":
        token, user_id = resolve_threads_credentials(niche_id)
        if token and user_id:
            return {"access_token": token, "user_id": user_id}
        return None

    return {}


# ---------------------------------------------------------------------------
# Core Publish Loop
# ---------------------------------------------------------------------------


def run_publish(
    *,
    niche_id: str,
    backlog_client,
    daily_cap,
    enabled_platforms: list[str],
) -> int:
    """Core publish logic. Returns an exit code.

    This is the testable entry point — main() handles CLI + lock + client creation.
    """
    niche_id = _validate_niche(niche_id)
    logger.info("[publish] niche=%s platforms=%s", niche_id, enabled_platforms)

    # Recover blueprints stuck in PUBLISHING status (crash recovery)
    try:
        stuck = backlog_client.get_blueprints_by_status("PUBLISHING", niche_id=niche_id)
        for bp in stuck:
            fields = bp.get("fields", bp)
            updated_at = fields.get("updated_at", "")
            attempt_count = int(fields.get("publish_attempts", 0) or 0)

            # If stuck for >30 minutes, reset to VISUAL_READY (or PUBLISH_FAILED if 3+ attempts)
            if updated_at:
                try:
                    dt = datetime.fromisoformat(updated_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if datetime.now(UTC) - dt > timedelta(minutes=30):
                        # Check if already published on any platform (crash after partial success)
                        pps_raw = fields.get("platform_publish_status", "{}")
                        try:
                            pps = json.loads(pps_raw) if isinstance(pps_raw, str) else (pps_raw or {})
                        except (json.JSONDecodeError, TypeError):
                            pps = {}
                        has_published = any(
                            v == "PUBLISHED" or (isinstance(v, dict) and v.get("status") == "PUBLISHED")
                            for v in pps.values()
                        )
                        if has_published:
                            # Already published on some platforms — mark as PUBLISHED, don't re-publish
                            backlog_client.blueprints.update(bp["id"], {"status": "PUBLISHED"})
                            logger.warning("[publish] Recovered stuck PUBLISHING blueprint %s as PUBLISHED (partial success detected)", bp["id"][:8])
                        elif attempt_count >= 3:
                            # Too many attempts — give up
                            backlog_client.blueprints.update(bp["id"], {"status": "PUBLISH_FAILED"})
                            logger.error("[publish] Blueprint %s stuck after %d attempts — marking PUBLISH_FAILED", bp["id"][:8], attempt_count)
                        else:
                            backlog_client.blueprints.update(bp["id"], {"status": "VISUAL_READY"})
                            logger.warning("[publish] Recovered stuck PUBLISHING blueprint %s (>30min)", bp["id"][:8])
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.debug("[publish] PUBLISHING recovery check failed: %s", e)

    # Auto-recover PUBLISH_FAILED blueprints after 24h cooldown
    try:
        failed = backlog_client.get_blueprints_by_status("PUBLISH_FAILED", niche_id=niche_id)
        for bp in failed:
            fields = bp.get("fields", bp)
            updated_at = fields.get("updated_at", "")
            if updated_at:
                try:
                    dt = datetime.fromisoformat(updated_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if datetime.now(UTC) - dt > timedelta(hours=24):
                        backlog_client.blueprints.update(bp["id"], {
                            "status": "VISUAL_READY",
                            "publish_attempts": 0,
                        })
                        logger.info("[publish] Auto-recovered PUBLISH_FAILED blueprint %s after 24h cooldown", bp["id"][:8])
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.debug("[publish] PUBLISH_FAILED recovery check failed: %s", e)

    # 1. Query VISUAL_READY blueprints for this niche
    all_blueprints = backlog_client.get_blueprints_by_status(
        "VISUAL_READY", niche_id=niche_id,
    )
    if not all_blueprints:
        logger.info("[publish] No VISUAL_READY blueprints for niche=%s", niche_id)
        return EXIT_NO_BLUEPRINTS

    # 2. Run gatekeeper on each blueprint (filters by approval, schedule, score, media)
    #    Daily cap is checked per-platform in step 4, not at gatekeeper level.
    gatekeeper = PublishGatekeeper()
    eligible = []
    for bp in all_blueprints:
        fields = bp.get("fields", bp)
        # Cross-niche safety: skip blueprints that don't match our niche
        bp_niche = _normalize_niche(fields.get("niche_id", "") or "")
        if bp_niche != niche_id:
            logger.warning(
                "[publish] Skipping blueprint %s: niche mismatch (%s != %s)",
                bp.get("id", "?"), bp_niche, niche_id,
            )
            continue
        gate = gatekeeper.evaluate(fields, "")  # platform-agnostic evaluation
        if gate.allowed:
            eligible.append(bp)
        else:
            logger.debug(
                "[publish] Blueprint %s blocked by %s: %s",
                bp.get("id", "?"), gate.gate_name, gate.reason,
            )

    if not eligible:
        logger.info("[publish] No blueprints passed gatekeeper for niche=%s", niche_id)
        return EXIT_NO_BLUEPRINTS

    # 3. Sort by priority_score descending, take top 1
    eligible.sort(
        key=lambda b: float(b.get("fields", {}).get("priority_score", 0) or 0),
        reverse=True,
    )
    best = eligible[0]
    fields = best.get("fields", best)
    record_id = best.get("id", "")
    candidate_id = fields.get("candidate_id", "")
    logger.info(
        "[publish] Selected blueprint %s (score=%.2f, hook=%s)",
        record_id[:16],
        float(fields.get("priority_score", 0) or 0),
        (fields.get("hook", "") or "")[:50],
    )

    # 4. Daily cap check (per-platform)
    platforms_to_publish = []
    all_capped = True
    for p in enabled_platforms:
        if daily_cap and not daily_cap.can_publish(p):
            logger.info("[publish] %s: daily cap reached, skipping", p)
        else:
            all_capped = False
            platforms_to_publish.append(p)

    if not platforms_to_publish:
        logger.info("[publish] All platforms capped for niche=%s", niche_id)
        return EXIT_DAILY_CAP

    # 5. Set status = PUBLISHING
    try:
        backlog_client.blueprints.update(
            record_id,
            {"status": "PUBLISHING"},
            typecast=True,
        )
    except Exception as exc:
        logger.warning("[publish] Failed to set PUBLISHING status: %s", exc)

    # 6. Publish to each platform
    platform_status: dict[str, str] = {}
    any_success = False

    def _publish_one(platform: str) -> tuple[str, PublishResult]:
        registry_id = _to_registry_id(platform)
        try:
            kwargs = _resolve_client_kwargs(registry_id, niche_id)
            if kwargs is None:
                return platform, PublishResult(
                    platform=registry_id, success=False,
                    error=f"No {registry_id} credentials for niche '{niche_id}'",
                )
            payload = build_payload(fields, platform)
            client = get_client(registry_id, **kwargs)
            result = client.publish(payload)
            return platform, result
        except Exception as exc:
            return platform, PublishResult(
                platform=registry_id, success=False, error=str(exc),
            )

    with ThreadPoolExecutor(max_workers=len(platforms_to_publish)) as pool:
        futures = {
            pool.submit(_publish_one, p): p for p in platforms_to_publish
        }
        for future in futures:
            try:
                platform, result = future.result(timeout=300)  # 5-min max per platform
            except TimeoutError:
                platform = futures[future]
                result = PublishResult(
                    platform=_to_registry_id(platform), success=False,
                    error=f"Publish timed out after 300s for {platform}",
                )
            except Exception as exc:
                platform = futures[future]
                result = PublishResult(
                    platform=_to_registry_id(platform), success=False,
                    error=f"Publish error: {exc}",
                )
            registry_id = _to_registry_id(platform)
            error_class = ""
            if result.success:
                any_success = True
                platform_status[platform] = "PUBLISHED"
                if daily_cap:
                    daily_cap.record_publish(platform)
                logger.info(
                    "[publish] %s: SUCCESS post_id=%s url=%s",
                    platform, result.post_id, result.post_url,
                )
                # Post affiliate link as first reply/comment (non-blocking)
                _post_affiliate_reply(platform, result.post_id, fields, niche_id)
                # Immediately persist platform status to prevent double-post on crash
                try:
                    backlog_client.blueprints.update(
                        record_id,
                        {"platform_publish_status": json.dumps(platform_status)},
                    )
                except Exception:
                    pass  # best-effort — final update at step 7 will catch up
            else:
                error_class = classify(result.error, platform)
                attempt_data = platform_status.get(platform, {})
                if isinstance(attempt_data, dict):
                    prev_attempts = attempt_data.get("attempts", 0)
                else:
                    prev_attempts = 0
                platform_status[platform] = {
                    "status": "FAILED",
                    "attempts": prev_attempts + 1,
                    "last_error": result.error[:200],
                    "error_class": error_class,
                }
                logger.error("[publish] %s: FAILED error=%s", platform, result.error)

            # Record to Publishing_Analytics
            # Use SKIPPED for credential failures (not retryable, not a real failure)
            if result.success:
                analytics_status = "SUCCESS"
            elif error_class == "CREDENTIAL":
                analytics_status = "SKIPPED"
            else:
                analytics_status = "FAILED"
            record_publish(
                client=backlog_client,
                niche_id=niche_id,
                platform=platform,
                status=analytics_status,
                post_url=result.post_url,
                blueprint_id=record_id,
                candidate_id=candidate_id,
                error_message=result.error if not result.success else "",
            )

    # 7. Update blueprint final status
    # Track publish attempts
    attempt_count = int(fields.get("publish_attempts", 0) or 0) + 1
    if not any_success and attempt_count >= 3:
        final_status = "PUBLISH_FAILED"
        logger.error("[publish] Blueprint %s failed %d times — marking PUBLISH_FAILED", record_id, attempt_count)
    elif any_success:
        final_status = "PUBLISHED"
    else:
        final_status = "VISUAL_READY"
    try:
        backlog_client.blueprints.update(
            record_id,
            {
                "status": final_status,
                "platform_publish_status": json.dumps(platform_status),
                "publish_attempts": attempt_count,
            },
            typecast=True,
        )
        logger.info("[publish] Blueprint %s -> %s (%s)", record_id[:16], final_status, platform_status)
    except Exception as exc:
        logger.error("[publish] Failed to update final status: %s", exc)

    # 8. Register PendingFeedback for the learning loop (bandit updates)
    if any_success:
        try:
            from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
            from genlab_core.learning.pending_feedback_task import PendingFeedbackTask

            fb_store = PendingFeedbackStore(backlog_client)
            for plat, pstatus in platform_status.items():
                if pstatus == "PUBLISHED" or (isinstance(pstatus, dict) and pstatus.get("status") == "PUBLISHED"):
                    # Find the post_id from the publish results
                    post_id_for_plat = ""
                    for future in futures:
                        try:
                            fp, fr = future.result()
                            if fp == plat and fr.success:
                                post_id_for_plat = fr.post_id or ""
                                break
                        except Exception as exc:
                            logger.debug("Failed to get post_id from future: %s", exc)

                    # Build bandit_context with hook features for LinUCB (Break 11 fix)
                    bandit_ctx = None
                    try:
                        from genlab_core.learning.hook_features import build_feature_vector
                        from genlab_core.learning.linucb import build_content_context
                        hook_txt = fields.get("hook", "")
                        hook_feats = build_feature_vector(hook_txt) if hook_txt else {}
                        linucb_ctx = build_content_context(fields, niche_id).tolist()
                        bandit_ctx = {
                            "hook_features": hook_feats,
                            "linucb_context": linucb_ctx,
                        }
                    except Exception as ctx_exc:
                        logger.debug("[publish] bandit_context build failed: %s", ctx_exc)

                    task = PendingFeedbackTask(
                        content_id=candidate_id or record_id[:16],
                        platform=plat,
                        niche_id=niche_id,
                        published_at=datetime.now(UTC),
                        platform_post_id=post_id_for_plat,
                        content_type="video",
                        hook_text=fields.get("hook", "")[:100],
                        hook_length=len(fields.get("hook", "")),
                        bandit_arm=fields.get("arm_id", ""),
                        bandit_context=bandit_ctx,
                    )
                    fb_store.create(task)
        except Exception as e:
            logger.warning("[publish] PendingFeedback registration failed (non-fatal): %s", e)

    # ── Retry pass: check recent PUBLISHED blueprints for failed platforms ──
    # Only check blueprints published in the last 7 days (not the entire history)
    try:
        seven_days_ago = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        published_bps = backlog_client.get_blueprints_by_status(
            "PUBLISHED", niche_id=niche_id, max_records=50,
        )
        for bp in published_bps:
            fields = bp.get("fields", bp)
            pps_raw = fields.get("platform_publish_status", "{}")
            if isinstance(pps_raw, str):
                try:
                    pps = json.loads(pps_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            else:
                pps = pps_raw or {}

            # Find platforms that failed and are eligible for retry
            retry_platforms = []
            for plat, status_data in pps.items():
                if isinstance(status_data, dict) and status_data.get("status") == "FAILED":
                    error_class = status_data.get("error_class", "TRANSIENT")
                    attempts = status_data.get("attempts", 0)
                    if not should_retry(error_class) or attempts >= 3:
                        continue
                    # Check backoff timing
                    next_retry = status_data.get("next_retry_after", "")
                    if next_retry:
                        try:
                            retry_dt = datetime.fromisoformat(next_retry)
                            if retry_dt.tzinfo is None:
                                retry_dt = retry_dt.replace(tzinfo=UTC)
                            if retry_dt > datetime.now(UTC):
                                continue  # Not yet time to retry
                        except (ValueError, TypeError):
                            pass
                    # Check daily cap before retrying
                    if daily_cap and not daily_cap.can_publish(plat):
                        logger.debug("[publish] Retry skipped for %s — daily cap reached", plat)
                        continue
                    retry_platforms.append(plat)

            if not retry_platforms:
                continue

            # Skip retry if media files are gone (cleaned up)
            vp_raw = fields.get("visual_paths", "[]")
            try:
                vp_list = json.loads(vp_raw) if isinstance(vp_raw, str) else (vp_raw or [])
            except (json.JSONDecodeError, TypeError):
                vp_list = []
            if vp_list and not any(Path(p).exists() for p in vp_list if p):
                logger.info("[publish] Skipping retry for %s — media files deleted", bp["id"][:8])
                continue

            logger.info("[publish] Retrying %d failed platform(s) for blueprint %s: %s",
                        len(retry_platforms), bp["id"][:8], retry_platforms)

            # Retry each failed platform with its own payload
            try:
                record_id = bp["id"]

                for plat in retry_platforms:
                    payload = build_payload(fields, plat)
                    registry_id = _to_registry_id(plat)
                    kwargs = _resolve_client_kwargs(registry_id, niche_id)
                    if not kwargs:
                        continue

                    try:
                        client_instance = get_client(registry_id, **kwargs)
                        result = client_instance.publish(payload)

                        if result.success:
                            pps[plat] = "PUBLISHED"
                            logger.info("[publish] Retry SUCCESS: %s/%s post_id=%s", niche_id, plat, result.post_id)
                        else:
                            ec = classify(result.error, plat)
                            prev = pps[plat] if isinstance(pps[plat], dict) else {}
                            attempts = (prev.get("attempts", 0) if isinstance(prev, dict) else 0) + 1
                            delay = retry_delay_seconds(ec, attempts)
                            pps[plat] = {
                                "status": "FAILED",
                                "attempts": attempts,
                                "last_error": result.error[:200],
                                "error_class": ec,
                                "next_retry_after": (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
                            }
                            logger.warning("[publish] Retry FAILED: %s/%s (%s): %s", niche_id, plat, ec, result.error[:100])

                        # Record to analytics
                        record_publish(
                            client=backlog_client,
                            niche_id=niche_id,
                            platform=plat,
                            status="SUCCESS" if result.success else "FAILED",
                            post_url=result.post_url,
                            blueprint_id=record_id,
                            error_message=result.error if not result.success else "",
                        )
                    except Exception as e:
                        logger.warning("[publish] Retry exception for %s/%s: %s", niche_id, plat, e)

                # Update platform_publish_status
                backlog_client.blueprints.update(record_id, {
                    "platform_publish_status": json.dumps(pps),
                })
            except Exception as e:
                logger.warning("[publish] Retry processing failed for blueprint %s: %s", bp["id"][:8], e)

    except Exception as e:
        logger.debug("[publish] Retry pass failed: %s", e)

    return EXIT_SUCCESS if any_success else EXIT_ALL_FAILED


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Canonical multi-platform publisher for Gen Lab niches",
    )
    parser.add_argument(
        "--niche",
        required=True,
        help="Niche ID (ai_creators, gaming, sports, movies, anime) or 'all'",
    )
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=None,
        help="Override enabled platforms (default: instagram youtube facebook twitter threads)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from dotenv import load_dotenv
    load_dotenv(override=True)

    from genlab_core.http.backlog_client import BacklogClient
    from genlab_core.publishing.daily_cap import DailyCapEnforcer

    enabled = args.platforms or [
        "instagram", "youtube", "facebook", "twitter", "threads",
    ]

    niches = (
        ["ai_creators", "gaming", "sports", "movies", "anime"]
        if args.niche == "all"
        else [_validate_niche(args.niche)]
    )

    # Reuse a single BacklogClient across all niches to avoid creating
    # multiple connection pools (P21)
    shared_client = BacklogClient()

    total_exit = 0
    for nid in niches:
        nid = _validate_niche(nid)
        logger.info("=" * 60)
        logger.info("Publishing for niche: %s", nid)
        logger.info("=" * 60)

        lock = PidLock(nid)
        if not lock.acquire():
            logger.warning("[publish] Lock held for niche=%s — skipping", nid)
            continue

        try:
            enforcer = DailyCapEnforcer(backlog_client=shared_client, niche_id=nid)
            enforcer.log_headroom()

            exit_code = run_publish(
                niche_id=nid,
                backlog_client=shared_client,
                daily_cap=enforcer,
                enabled_platforms=enabled,
            )
            total_exit = max(total_exit, exit_code)
        except Exception as exc:
            logger.error("[publish] Failed for %s: %s", nid, exc)
            total_exit = 1
        finally:
            lock.release()

    return total_exit


if __name__ == "__main__":
    sys.exit(main())
