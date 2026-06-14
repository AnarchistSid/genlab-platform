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
            niche_id,
            prefix,
            niche_suffix,
            global_var,
        )
        return ""

    return os.getenv(global_var, "").strip()


def resolve_meta_credentials(niche_id: str) -> dict[str, str]:
    """Resolve Meta (IG + FB) credentials for a niche."""
    return {
        "ig_access_token": resolve_niche_env(niche_id, "META_ACCESS_TOKEN", "META_ACCESS_TOKEN"),
        "ig_user_id": resolve_niche_env(niche_id, "META_IG_USER_ID", "IG_USER_ID"),
        "fb_access_token": resolve_niche_env(
            niche_id, "FB_PAGE_ACCESS_TOKEN", "FB_PAGE_ACCESS_TOKEN"
        ),
        "fb_page_id": resolve_niche_env(niche_id, "META_FB_PAGE_ID", "FB_PAGE_ID"),
    }


def resolve_fb_credentials(niche_id: str) -> tuple:
    """Return (access_token, page_id) for a niche's Facebook Page."""
    return (
        resolve_niche_env(niche_id, "FB_PAGE_ACCESS_TOKEN", "FB_PAGE_ACCESS_TOKEN"),
        resolve_niche_env(niche_id, "META_FB_PAGE_ID", "FB_PAGE_ID"),
    )


# 2026-06-14: per-niche Threads tokens can also live in a JSON cache
# file populated by the auto-refresh script. The cache takes priority
# over .env so operator-provisioned tokens stay valid even after the
# 60-day Threads LLUT expiry — see ``scripts/refresh_threads_tokens.py``.
_THREADS_TOKEN_CACHE_PATH = "/opt/genlab/.threads_tokens.json"


def _read_threads_token_cache(niche_id: str) -> str:
    """Look up a refresh-cached Threads access token for the niche.

    Returns empty string if the cache file doesn't exist, isn't valid
    JSON, doesn't have an entry for the niche, or is past expiry. The
    caller falls back to ``resolve_niche_env``.

    Safe to call with no cache file — returns "" silently. Never raises.
    """
    try:
        import json
        from pathlib import Path

        cache_path = Path(_THREADS_TOKEN_CACHE_PATH)
        if not cache_path.is_file():
            return ""
        data = json.loads(cache_path.read_text())
        entry = data.get(niche_id, {})
        token = entry.get("access_token", "")
        if not token:
            return ""
        # Optional expires_at field — if present + past, treat as stale.
        # Operators may store tokens manually without expires_at; in that
        # case we still trust the value (no expiry to compare against).
        expires_at_iso = entry.get("expires_at", "")
        if expires_at_iso:
            from datetime import UTC, datetime

            try:
                expires_at = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00"))
                if datetime.now(UTC) >= expires_at:
                    return ""  # expired — fall through to .env
            except (ValueError, TypeError):
                pass  # malformed timestamp — keep the token, log nothing
        return token
    except Exception:
        return ""


def resolve_threads_credentials(niche_id: str) -> tuple:
    """Return (access_token, user_id) for a niche's Threads account.

    Token resolution order (2026-06-14):
      1. ``/opt/genlab/.threads_tokens.json`` cache (populated by the
         refresh script when a non-expired entry exists)
      2. Per-niche env var (e.g., ``CRITICALRUSH_THREADS_ACCESS_TOKEN``)
      3. Global ``THREADS_ACCESS_TOKEN`` env var fallback

    The user_id never auto-refreshes; only env-var resolution.
    """
    cached_token = _read_threads_token_cache(niche_id)
    if cached_token:
        token = cached_token
    else:
        token = resolve_niche_env(niche_id, "THREADS_ACCESS_TOKEN", "THREADS_ACCESS_TOKEN")
    return (
        token,
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
        "refresh_token": resolve_niche_env(
            niche_id, "YOUTUBE_REFRESH_TOKEN", "YOUTUBE_REFRESH_TOKEN"
        ),
    }


def resolve_youtube_analytics_credentials(niche_id: str = "") -> dict[str, str]:
    """Resolve YouTube Analytics OAuth credentials for a niche.

    Verified 2026-05-21: YouTube Analytics API requires the OAuth token
    to be issued *to the channel itself*, not to a manager of the channel.
    The brand-account "manager" relationship that lets a parent Google
    account upload + switch channels in YouTube Studio does NOT extend to
    Analytics API access (returns 403 on managed-but-not-owned channels).

    Consequence: per-niche refresh tokens are mandatory.
      * BlackboxBrief — channel owned directly by anarchistsid@gmail.com;
        token issued under that account works.
      * CriticalRush / ClutchWire / SpliceReel / FrameDrift — brand accounts;
        each needs its own OAuth flow signed in as that brand account.

    Reads ``{PREFIX}_YOUTUBE_ANALYTICS_REFRESH_TOKEN`` first, then falls
    back to the legacy singular ``YOUTUBE_ANALYTICS_REFRESH_TOKEN`` for
    accounts whose token happens to be the global one (e.g. BB historically).
    """
    return {
        "client_id": os.getenv("YOUTUBE_ANALYTICS_CLIENT_ID", "").strip()
        or os.getenv("YOUTUBE_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("YOUTUBE_ANALYTICS_CLIENT_SECRET", "").strip()
        or os.getenv("YOUTUBE_CLIENT_SECRET", "").strip(),
        "refresh_token": resolve_niche_env(
            niche_id,
            "YOUTUBE_ANALYTICS_REFRESH_TOKEN",
            "YOUTUBE_ANALYTICS_REFRESH_TOKEN",
        ),
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
