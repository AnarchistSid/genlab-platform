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
from pathlib import Path
from typing import Any, Dict

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
    "ai_creators", "ai_tech", "gaming", "sports", "movies", "anime",
})

# Maps legacy platform names to registry IDs
_PLATFORM_ID_MAP: Dict[str, str] = {"twitter": "x_twitter"}


def _to_registry_id(platform: str) -> str:
    return _PLATFORM_ID_MAP.get(platform, platform)


def _normalize_niche(niche_id: str) -> str:
    niche_id = niche_id.strip()
    if niche_id == "ai_tech":
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
# Payload Builder
# ---------------------------------------------------------------------------


def build_payload(fields: Dict[str, Any], platform: str) -> PublishPayload:
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

    # Media type
    fmt = (fields.get("format", "") or "").strip().lower()
    has_mp4 = any(str(p).lower().endswith(".mp4") for p in media_paths)
    if has_mp4 or fmt in ("reel", "short", "video"):
        media_type = "video"
    else:
        media_type = "image"

    # Caption, hashtags, hook
    caption = (fields.get("caption", "") or "").strip()
    hashtags_raw = fields.get("hashtags", "") or ""
    if isinstance(hashtags_raw, list):
        hashtags = [str(h).strip().lstrip("#") for h in hashtags_raw if h]
    else:
        hashtags = [
            t.strip().lstrip("#") for t in str(hashtags_raw).split() if t.strip()
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
    fields: Dict[str, Any], platform: str, caption: str, hook: str
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
        return TwitterSpecific(
            routing=routing if routing in ("single", "thread") else "single",
            tweet_text=str(tw_content.get("tweet_text", "") or caption).strip(),
            thread_tweets=tw_content.get("thread_tweets", []),
        )

    if platform == "facebook":
        return FacebookSpecific()

    if platform == "threads":
        return ThreadsSpecific()

    return None


def _parse_visual_paths(fields: Dict[str, Any]) -> list:
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
        if refresh_token and client_id and client_secret:
            return {
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
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
        gate = gatekeeper.evaluate(fields, "instagram")  # gate is platform-agnostic except cap
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
    platform_status: Dict[str, str] = {}
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
            platform, result = future.result()
            registry_id = _to_registry_id(platform)
            if result.success:
                any_success = True
                platform_status[platform] = "PUBLISHED"
                if daily_cap:
                    daily_cap.record_publish(platform)
                logger.info(
                    "[publish] %s: SUCCESS post_id=%s url=%s",
                    platform, result.post_id, result.post_url,
                )
            else:
                platform_status[platform] = "FAILED"
                logger.error("[publish] %s: FAILED error=%s", platform, result.error)

            # Record to Publishing_Analytics
            record_publish(
                client=backlog_client,
                niche_id=niche_id,
                platform=platform,
                status="SUCCESS" if result.success else "FAILED",
                post_url=result.post_url,
                blueprint_id=record_id,
                candidate_id=candidate_id,
                error_message=result.error if not result.success else "",
            )

    # 7. Update blueprint final status
    final_status = "PUBLISHED" if any_success else "VISUAL_READY"
    try:
        backlog_client.blueprints.update(
            record_id,
            {
                "status": final_status,
                "platform_publish_status": json.dumps(platform_status),
            },
            typecast=True,
        )
        logger.info("[publish] Blueprint %s -> %s (%s)", record_id[:16], final_status, platform_status)
    except Exception as exc:
        logger.error("[publish] Failed to update final status: %s", exc)

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
        help="Niche ID (ai_creators, gaming, sports, movies, anime)",
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

    niche_id = _validate_niche(args.niche)

    # PID lock
    lock = PidLock(niche_id)
    if not lock.acquire():
        logger.warning("[publish] Lock held for niche=%s — another instance running", niche_id)
        return EXIT_LOCK_HELD

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)

        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.publishing.daily_cap import DailyCapEnforcer

        client = BacklogClient()
        enforcer = DailyCapEnforcer(backlog_client=client, niche_id=niche_id)
        enforcer.log_headroom()

        enabled = args.platforms or [
            "instagram", "youtube", "facebook", "twitter", "threads",
        ]

        return run_publish(
            niche_id=niche_id,
            backlog_client=client,
            daily_cap=enforcer,
            enabled_platforms=enabled,
        )
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
