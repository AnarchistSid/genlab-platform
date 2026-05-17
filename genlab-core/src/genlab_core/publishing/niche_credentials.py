"""Per-niche credential resolution with cross-channel publishing guard.

Each niche channel has its own platform accounts (Meta, YouTube, X, Threads).
Env vars are prefixed by brand name: CLUTCHWIRE_META_ACCESS_TOKEN, etc.

The guard: when a niche has a registered prefix but the niche-specific env var
is empty, we return "" rather than falling back to BB's global credentials.
This prevents accidentally publishing to the wrong channel's pages.

BB (ai_creators/ai_tech) has prefix BLACKBOXBRIEF but falls through to global vars
when the prefixed var is missing — BB's globals ARE its own tokens.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

NICHE_CREDENTIAL_PREFIXES: dict[str, str] = {
    "sports": "CLUTCHWIRE",
    "movies": "SPLICEREEL",
    "anime": "FRAMEDRIFT",
    "gaming": "CRITICALRUSH",
    "ai_creators": "BLACKBOXBRIEF",
    "ai_tech": "BLACKBOXBRIEF",  # alias — normalizes to ai_creators
}


def resolve_niche_env(niche_id: str, global_var: str, niche_suffix: str) -> str:
    """Resolve a single env var for a niche, with cross-channel guard.

    Args:
        niche_id: The niche identifier (e.g. "sports", "ai_creators").
        global_var: The global/BB env var name (e.g. "META_ACCESS_TOKEN").
        niche_suffix: The niche-specific suffix (e.g. "META_ACCESS_TOKEN").

    Returns:
        The resolved value, or "" if missing (never falls back across channels).
    """
    prefix = NICHE_CREDENTIAL_PREFIXES.get(niche_id, "")

    if not prefix and niche_id:
        # Unknown niche_id — block fallback to global credentials to prevent
        # accidentally publishing to BB's accounts with a typo'd niche_id
        logger.warning(
            "Unknown niche_id '%s' — no credential prefix registered. "
            "Refusing to fall back to global credentials.",
            niche_id,
        )
        return ""

    if prefix:
        val = os.getenv(f"{prefix}_{niche_suffix}", "").strip()
        if val:
            return val
        # BB (BLACKBOXBRIEF) falls through to global — its globals ARE its own tokens.
        # Other niches: guard blocks fallback to prevent cross-channel publishing.
        if prefix == "BLACKBOXBRIEF":
            return os.getenv(global_var, "").strip()
        logger.debug(
            "Niche '%s' missing %s_%s — refusing fallback to global %s",
            niche_id, prefix, niche_suffix, global_var,
        )
        return ""

    return os.getenv(global_var, "").strip()


def resolve_meta_credentials(niche_id: str) -> dict[str, str]:
    """Resolve Meta (IG + FB) credentials for a niche."""
    return {
        "ig_access_token": resolve_niche_env(niche_id, "META_ACCESS_TOKEN", "META_ACCESS_TOKEN"),
        "ig_user_id": resolve_niche_env(niche_id, "META_IG_USER_ID", "IG_USER_ID"),
        "fb_access_token": resolve_niche_env(niche_id, "FB_PAGE_ACCESS_TOKEN", "FB_PAGE_ACCESS_TOKEN"),
        "fb_page_id": resolve_niche_env(niche_id, "META_FB_PAGE_ID", "FB_PAGE_ID"),
    }


def resolve_fb_credentials(niche_id: str) -> tuple:
    """Return (access_token, page_id) for a niche's Facebook Page."""
    return (
        resolve_niche_env(niche_id, "FB_PAGE_ACCESS_TOKEN", "FB_PAGE_ACCESS_TOKEN"),
        resolve_niche_env(niche_id, "META_FB_PAGE_ID", "FB_PAGE_ID"),
    )


def resolve_threads_credentials(niche_id: str) -> tuple:
    """Return (access_token, user_id) for a niche's Threads account."""
    return (
        resolve_niche_env(niche_id, "THREADS_ACCESS_TOKEN", "THREADS_ACCESS_TOKEN"),
        resolve_niche_env(niche_id, "THREADS_USER_ID", "THREADS_USER_ID"),
    )


def resolve_youtube_credentials(niche_id: str) -> dict[str, str]:
    """Resolve YouTube OAuth credentials for a niche.

    client_id/secret are shared (same OAuth app) — always use global.
    Only refresh_token is per-niche.
    """
    return {
        "client_id": os.getenv("YOUTUBE_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("YOUTUBE_CLIENT_SECRET", "").strip(),
        "refresh_token": resolve_niche_env(niche_id, "YOUTUBE_REFRESH_TOKEN", "YOUTUBE_REFRESH_TOKEN"),
    }


def resolve_youtube_analytics_credentials() -> dict[str, str]:
    """Resolve shared YouTube Analytics OAuth credentials.

    Analytics is read-only and runs through a single "super-account"
    refresh_token that has manager access on all 5 channels. Per-niche
    isolation is preserved at the channel level via
    ``{PREFIX}_YT_CHANNEL_ID`` (used as the ``ids=channel==<id>`` filter)
    rather than at the credential level.

    Falls back to the per-niche YouTube credentials if no shared
    analytics token is configured — useful for niches that later
    re-consent with the analytics scope individually.
    """
    return {
        "client_id": os.getenv("YOUTUBE_ANALYTICS_CLIENT_ID", "").strip()
                     or os.getenv("YOUTUBE_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("YOUTUBE_ANALYTICS_CLIENT_SECRET", "").strip()
                         or os.getenv("YOUTUBE_CLIENT_SECRET", "").strip(),
        "refresh_token": os.getenv("YOUTUBE_ANALYTICS_REFRESH_TOKEN", "").strip(),
    }


def resolve_youtube_channel_id(niche_id: str) -> str:
    """Return the YouTube channel ID for a niche from ``{PREFIX}_YT_CHANNEL_ID``.

    Returns "" if missing — _fetch_youtube_analytics_extras treats that
    as a soft-fail (no API call attempted).
    """
    return resolve_niche_env(niche_id, "YT_CHANNEL_ID", "YT_CHANNEL_ID")


def resolve_twitter_credentials(niche_id: str) -> dict[str, str]:
    """Resolve X/Twitter OAuth 1.0a credentials for a niche.

    api_key/secret are shared (same app) — always use global.
    Only access_token/secret are per-niche.
    """
    return {
        "api_key": os.getenv("X_API_KEY", "").strip(),
        "api_secret": os.getenv("X_API_SECRET", "").strip(),
        "access_token": resolve_niche_env(niche_id, "X_ACCESS_TOKEN", "X_ACCESS_TOKEN"),
        "access_secret": resolve_niche_env(niche_id, "X_ACCESS_SECRET", "X_ACCESS_SECRET"),
    }


class CrossChannelPublishError(RuntimeError):
    """Raised when a blueprint's niche doesn't match the credential niche."""


def validate_niche_match(blueprint_niche: str, credential_niche: str) -> None:
    """Assert that blueprint niche matches credential niche.

    Raises CrossChannelPublishError if there is a mismatch.
    """
    if not credential_niche:
        raise CrossChannelPublishError(
            f"No credential niche provided for blueprint niche '{blueprint_niche}'"
        )
    if blueprint_niche != credential_niche:
        raise CrossChannelPublishError(
            f"Cross-channel publish blocked: blueprint niche '{blueprint_niche}' "
            f"!= credential niche '{credential_niche}'"
        )
