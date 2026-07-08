"""Centralized settings loaded from .env via pydantic-settings.

All credentials and configuration live here. Every field defaults to None
(or a sensible non-secret default) so missing keys produce warnings, not crashes.

Usage:
    from core.settings import settings
    token = settings.meta_access_token
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# AGENT_ROOT env var lets each agent declare its own project root.
# Falls back to the directory above genlab-core/ if not set.
# CriticalRush sets this in its venv activate script or launch config.
_PROJECT_ROOT = Path(
    os.getenv("AGENT_ROOT", str(Path(__file__).resolve().parent.parent.parent.parent))
)

# Populate os.environ from root .env so code using os.environ.get() can
# find shared credentials (e.g. ANTHROPIC_API_KEY, YOUTUBE_API_KEY).
# pydantic-settings reads .env into model fields only — it does NOT call
# load_dotenv(), leaving os.environ empty for direct lookups.
# override=False means existing env vars (e.g. from shell) take precedence.
#
# GENLAB_SUPPRESS_DOTENV=1 disables the load — used by ``tests/conftest.py``
# to prevent .env from re-populating POSTGRES_PASSWORD (and other prod
# credentials) into os.environ after the conftest has explicitly popped
# them. Without this guard, the FIRST test that imports ``genlab_core``
# re-loads .env, mid-suite skipif predicates flip True→False, and
# storage tests that should SKIP instead run against the operator's
# prod DB → confusing test failures that only surface in CI when test
# ordering changes (e.g. starlette 1.x upgrade). See
# ``docs/U-24-starlette-1x-investigation.md`` for the full bug.
_root_env = _PROJECT_ROOT / ".env"
if _root_env.is_file() and not os.environ.get("GENLAB_SUPPRESS_DOTENV"):
    load_dotenv(str(_root_env), override=False)


# ---------------------------------------------------------------------------
# Feature-flag helper — enforces uniform truthiness semantics
# ---------------------------------------------------------------------------
# The 2026-07-08 round-3 flag audit found the codebase has ~26 GENLAB_*
# feature flags read with INCOMPATIBLE truthiness checks across sites:
#   * ``!= "1"`` (strict — only "1" is truthy)
#   * ``.lower() in ("1", "true", "yes", "on")`` (permissive)
#   * ``in ("true", "TRUE", "True")`` (case-list, not case-fold — misses "1")
#
# Different truthiness checks for the SAME flag caused a documented silent
# no-op on ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED``: the orchestrator and
# selector used the permissive check, the post_render entrypoint used the
# strict check. Setting the flag to ``"true"`` produced a partial fire —
# selector picks arms, orchestrator accepts, post_render silently rejects.
# Operator had no visible signal the flag was mis-configured.
#
# This helper is the source of truth for boolean env flags. Every new flag
# read site should call ``env_true(name)``; the round-4 flag-hygiene PR
# migrates the existing sites.
_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})


def env_true(
    name: str,
    *,
    default: bool = False,
    legacy_name: str | None = None,
) -> bool:
    """Return True if the env var ``name`` is set to a truthy value.

    Accepts (case-insensitive, whitespace-stripped): ``1``, ``true``,
    ``yes``, ``on``, ``y``, ``t``. Everything else — including empty
    string, ``0``, ``false``, ``no``, ``off``, and any other string — is
    False.

    Args:
        name: Env var name (e.g. ``"GENLAB_INTELLIGENT_TRANSFORM_ENABLED"``).
        default: Returned when the env var is UNSET. Distinct from unset
            behavior — use ``env_true(name, default=True)`` for kill-switch
            flags that should be ON unless explicitly disabled.
        legacy_name: Optional deprecated env var name to check as a
            fallback when ``name`` is unset. Used when a flag's canonical
            name changed (e.g. ``GENLAB_OPTIMAL_TIME_BANDIT`` →
            ``GENLAB_OPTIMAL_TIME_BANDIT_ENABLED`` to align with the
            ``_ENABLED`` suffix convention) but existing prod .env files
            still reference the old name. Both names are checked; the
            newer ``name`` takes precedence.

    Returns:
        Boolean truthiness of the flag.
    """
    raw = os.environ.get(name)
    if raw is not None:
        return raw.strip().lower() in _TRUE_VALUES
    if legacy_name is not None:
        legacy_raw = os.environ.get(legacy_name)
        if legacy_raw is not None:
            return legacy_raw.strip().lower() in _TRUE_VALUES
    return default


# ---------------------------------------------------------------------------
# Niche → required credential groups
# ---------------------------------------------------------------------------
# Maps niche IDs to the Settings field names each niche needs at runtime.
# validate_for_niche() checks these and logs warnings for anything missing.
#
# Each agent must set the AGENT_ROOT environment variable to its own project
# root directory, either in the virtualenv's activate script, in a .env file,
# or in its launch configuration. This ensures settings.py finds the correct
# .env file for that agent.
NICHE_REQUIREMENTS: dict[str, list[str]] = {
    "ai_creators": [
        # Backlog
        "azure_tenant_id",
        "azure_client_id",
        "azure_client_secret",
        "sharepoint_site_id",
        # LLM
        "anthropic_api_key",
        # Publishing (at least one platform)
        "meta_access_token",
    ],
    "gaming": [
        "azure_tenant_id",
        "azure_client_id",
        "azure_client_secret",
        "sharepoint_site_id",
        "anthropic_api_key",
        "twitch_client_id",
        "twitch_client_secret",
    ],
    "default": [
        "azure_tenant_id",
        "azure_client_id",
        "azure_client_secret",
        "sharepoint_site_id",
        "anthropic_api_key",
    ],
}


class Settings(BaseSettings):
    """Application settings — loaded from .env automatically.

    Field names use snake_case Python conventions. Where the env var name
    differs (e.g. AZURE_TENANT_ID → microsoft_tenant_id), a
    ``validation_alias`` maps the env var to the field.
    """

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",  # don't fail on unknown env vars
    )

    # ── Microsoft Graph / SharePoint ──────────────────────────
    azure_tenant_id: str | None = Field(
        default=None,
        validation_alias="AZURE_TENANT_ID",
    )
    azure_client_id: str | None = Field(
        default=None,
        validation_alias="AZURE_CLIENT_ID",
    )
    azure_client_secret: str | None = Field(
        default=None,
        validation_alias="AZURE_CLIENT_SECRET",
    )
    sharepoint_site_id: str | None = Field(
        default=None,
        validation_alias="SHAREPOINT_SITE_ID",
    )

    # ── Anthropic (primary LLM) ───────────────────────────────
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
    )

    # ── OpenAI (secondary LLM + image gen) ────────────────────
    openai_api_key: str | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )

    # ── Meta / Instagram ──────────────────────────────────────
    meta_access_token: str | None = Field(
        default=None,
        validation_alias="META_ACCESS_TOKEN",
    )
    meta_ig_user_id: str | None = Field(
        default=None,
        validation_alias="META_IG_USER_ID",
    )
    meta_ig_app_id: str | None = Field(
        default=None,
        validation_alias="META_IG_APP_ID",
    )
    meta_ig_app_secret: str | None = Field(
        default=None,
        validation_alias="META_IG_APP_SECRET",
    )

    # ── Meta / Facebook ───────────────────────────────────────
    fb_page_access_token: str | None = Field(
        default=None,
        validation_alias="FB_PAGE_ACCESS_TOKEN",
    )
    meta_fb_page_id: str | None = Field(
        default=None,
        validation_alias="META_FB_PAGE_ID",
    )
    fb_app_id: str | None = Field(
        default=None,
        validation_alias="FB_APP_ID",
    )
    fb_app_secret: str | None = Field(
        default=None,
        validation_alias="FB_APP_SECRET",
    )

    # ── YouTube (Data API v3 + OAuth2) ────────────────────────
    youtube_client_id: str | None = Field(
        default=None,
        validation_alias="YOUTUBE_CLIENT_ID",
    )
    youtube_client_secret: str | None = Field(
        default=None,
        validation_alias="YOUTUBE_CLIENT_SECRET",
    )
    youtube_refresh_token: str | None = Field(
        default=None,
        validation_alias="YOUTUBE_REFRESH_TOKEN",
    )

    # ── X / Twitter (API v2) ──────────────────────────────────
    x_api_key: str | None = Field(
        default=None,
        validation_alias="X_API_KEY",
    )
    x_api_secret: str | None = Field(
        default=None,
        validation_alias="X_API_SECRET",
    )
    x_access_token: str | None = Field(
        default=None,
        validation_alias="X_ACCESS_TOKEN",
    )
    x_access_secret: str | None = Field(
        default=None,
        validation_alias="X_ACCESS_SECRET",
    )
    x_bearer_token: str | None = Field(
        default=None,
        validation_alias="X_BEARER_TOKEN",
    )

    # ── Pexels (stock media) ──────────────────────────────────
    pexels_api_key: str | None = Field(
        default=None,
        validation_alias="PEXELS_API_KEY",
    )

    # ── Pixabay (stock media) ─────────────────────────────────
    pixabay_api_key: str | None = Field(
        default=None,
        validation_alias="PIXABAY_API_KEY",
    )

    # ── Unsplash (stock images) ───────────────────────────────
    unsplash_access_key: str | None = Field(
        default=None,
        validation_alias="UNSPLASH_ACCESS_KEY",
    )

    # ── Twitch / IGDB ─────────────────────────────────────────
    twitch_client_id: str | None = Field(
        default=None,
        validation_alias="TWITCH_CLIENT_ID",
    )
    twitch_client_secret: str | None = Field(
        default=None,
        validation_alias="TWITCH_CLIENT_SECRET",
    )

    # ── ElevenLabs (TTS) ──────────────────────────────────────
    elevenlabs_api_key: str | None = Field(
        default=None,
        validation_alias="ELEVENLABS_API_KEY",
    )

    # ── short-video-maker ─────────────────────────────────────
    short_video_maker_url: str = Field(
        default="http://localhost:3000",
        validation_alias="SHORT_VIDEO_MAKER_URL",
    )

    # ── Review dashboard ──────────────────────────────────────
    flask_secret_key: str | None = Field(
        default=None,
        validation_alias="FLASK_SECRET_KEY",
    )
    review_auth_user: str | None = Field(
        default=None,
        validation_alias="REVIEW_AUTH_USER",
    )
    review_auth_pass: str | None = Field(
        default=None,
        validation_alias="REVIEW_AUTH_PASS",
    )

    # ── Threads (Meta Threads API v1) ──────────────────────────
    threads_access_token: str | None = Field(
        default=None,
        validation_alias="THREADS_ACCESS_TOKEN",
    )
    threads_user_id: str | None = Field(
        default=None,
        validation_alias="THREADS_USER_ID",
    )
    threads_token_issued_at: str | None = Field(
        default=None,
        validation_alias="THREADS_TOKEN_ISSUED_AT",
    )

    # ── TikTok (Content Posting API v2) ─────────────────────
    tiktok_client_key: str | None = Field(
        default=None,
        validation_alias="TIKTOK_CLIENT_KEY",
    )
    tiktok_client_secret: str | None = Field(
        default=None,
        validation_alias="TIKTOK_CLIENT_SECRET",
    )
    tiktok_access_token: str | None = Field(
        default=None,
        validation_alias="TIKTOK_ACCESS_TOKEN",
    )
    tiktok_refresh_token: str | None = Field(
        default=None,
        validation_alias="TIKTOK_REFRESH_TOKEN",
    )
    tiktok_token_issued_at: str | None = Field(
        default=None,
        validation_alias="TIKTOK_TOKEN_ISSUED_AT",
    )
    tiktok_audit_approved: str | None = Field(
        default="false",
        validation_alias="TIKTOK_AUDIT_APPROVED",
    )

    # ── Cloudflare Tunnel (public URL for local CDN) ────────────
    cloudflare_tunnel_url: str | None = Field(
        default=None,
        validation_alias="CLOUDFLARE_TUNNEL_URL",
    )
    local_cdn_port: int = Field(
        default=8766,
        validation_alias="LOCAL_CDN_PORT",
    )

    # ── Runtime config (non-secret) ───────────────────────────
    log_level: str = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
    )
    max_stories_per_run: int = Field(
        default=20,
        validation_alias="MAX_STORIES_PER_RUN",
    )
    cache_ttl_hours: int = Field(
        default=6,
        validation_alias="CACHE_TTL_HOURS",
    )

    # ------------------------------------------------------------------
    # Niche validation
    # ------------------------------------------------------------------
    def validate_for_niche(self, niche_id: str) -> list[str]:
        """Check which credentials are missing for *niche_id*.

        Logs a warning per missing field but never raises.
        Returns the list of missing field names (empty = all good).
        """
        requirements = NICHE_REQUIREMENTS.get(niche_id, NICHE_REQUIREMENTS["default"])
        missing: list[str] = []
        for field_name in requirements:
            value = getattr(self, field_name, None)
            if not value:
                missing.append(field_name)

        if missing:
            logger.warning(
                "Niche '%s' is missing %d credential(s): %s",
                niche_id,
                len(missing),
                ", ".join(missing),
            )
        else:
            logger.info("Niche '%s': all required credentials present", niche_id)

        return missing

    def get_project_root(self) -> Path:
        """Return the resolved project root for this agent."""
        return _PROJECT_ROOT


# ---------------------------------------------------------------------------
# Module-level singleton — import this everywhere:
#   from core.settings import settings
# ---------------------------------------------------------------------------
settings = Settings()
