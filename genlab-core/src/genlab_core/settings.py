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
from typing import Dict, List, Optional

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
_root_env = _PROJECT_ROOT / ".env"
if _root_env.is_file():
    load_dotenv(str(_root_env), override=False)


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
NICHE_REQUIREMENTS: Dict[str, List[str]] = {
    "ai_creators": [
        # Backlog
        "azure_tenant_id", "azure_client_id", "azure_client_secret",
        "sharepoint_site_id",
        # LLM
        "anthropic_api_key",
        # Publishing (at least one platform)
        "meta_access_token",
    ],
    "gaming": [
        "azure_tenant_id", "azure_client_id", "azure_client_secret",
        "sharepoint_site_id",
        "anthropic_api_key",
        "twitch_client_id", "twitch_client_secret",
    ],
    "default": [
        "azure_tenant_id", "azure_client_id", "azure_client_secret",
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
    azure_tenant_id: Optional[str] = Field(
        default=None,
        validation_alias="AZURE_TENANT_ID",
    )
    azure_client_id: Optional[str] = Field(
        default=None,
        validation_alias="AZURE_CLIENT_ID",
    )
    azure_client_secret: Optional[str] = Field(
        default=None,
        validation_alias="AZURE_CLIENT_SECRET",
    )
    sharepoint_site_id: Optional[str] = Field(
        default=None,
        validation_alias="SHAREPOINT_SITE_ID",
    )

    # ── Anthropic (primary LLM) ───────────────────────────────
    anthropic_api_key: Optional[str] = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
    )

    # ── OpenAI (secondary LLM + image gen) ────────────────────
    openai_api_key: Optional[str] = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
    )

    # ── Meta / Instagram ──────────────────────────────────────
    meta_access_token: Optional[str] = Field(
        default=None,
        validation_alias="META_ACCESS_TOKEN",
    )
    meta_ig_user_id: Optional[str] = Field(
        default=None,
        validation_alias="META_IG_USER_ID",
    )
    meta_ig_app_id: Optional[str] = Field(
        default=None,
        validation_alias="META_IG_APP_ID",
    )
    meta_ig_app_secret: Optional[str] = Field(
        default=None,
        validation_alias="META_IG_APP_SECRET",
    )

    # ── Meta / Facebook ───────────────────────────────────────
    fb_page_access_token: Optional[str] = Field(
        default=None,
        validation_alias="FB_PAGE_ACCESS_TOKEN",
    )
    meta_fb_page_id: Optional[str] = Field(
        default=None,
        validation_alias="META_FB_PAGE_ID",
    )
    fb_app_id: Optional[str] = Field(
        default=None,
        validation_alias="FB_APP_ID",
    )
    fb_app_secret: Optional[str] = Field(
        default=None,
        validation_alias="FB_APP_SECRET",
    )

    # ── YouTube (Data API v3 + OAuth2) ────────────────────────
    youtube_client_id: Optional[str] = Field(
        default=None,
        validation_alias="YOUTUBE_CLIENT_ID",
    )
    youtube_client_secret: Optional[str] = Field(
        default=None,
        validation_alias="YOUTUBE_CLIENT_SECRET",
    )
    youtube_refresh_token: Optional[str] = Field(
        default=None,
        validation_alias="YOUTUBE_REFRESH_TOKEN",
    )

    # ── X / Twitter (API v2) ──────────────────────────────────
    x_api_key: Optional[str] = Field(
        default=None,
        validation_alias="X_API_KEY",
    )
    x_api_secret: Optional[str] = Field(
        default=None,
        validation_alias="X_API_SECRET",
    )
    x_access_token: Optional[str] = Field(
        default=None,
        validation_alias="X_ACCESS_TOKEN",
    )
    x_access_secret: Optional[str] = Field(
        default=None,
        validation_alias="X_ACCESS_SECRET",
    )
    x_bearer_token: Optional[str] = Field(
        default=None,
        validation_alias="X_BEARER_TOKEN",
    )

    # ── Pexels (stock media) ──────────────────────────────────
    pexels_api_key: Optional[str] = Field(
        default=None,
        validation_alias="PEXELS_API_KEY",
    )

    # ── Pixabay (stock media) ─────────────────────────────────
    pixabay_api_key: Optional[str] = Field(
        default=None,
        validation_alias="PIXABAY_API_KEY",
    )

    # ── Unsplash (stock images) ───────────────────────────────
    unsplash_access_key: Optional[str] = Field(
        default=None,
        validation_alias="UNSPLASH_ACCESS_KEY",
    )

    # ── Twitch / IGDB ─────────────────────────────────────────
    twitch_client_id: Optional[str] = Field(
        default=None,
        validation_alias="TWITCH_CLIENT_ID",
    )
    twitch_client_secret: Optional[str] = Field(
        default=None,
        validation_alias="TWITCH_CLIENT_SECRET",
    )

    # ── ElevenLabs (TTS) ──────────────────────────────────────
    elevenlabs_api_key: Optional[str] = Field(
        default=None,
        validation_alias="ELEVENLABS_API_KEY",
    )

    # ── short-video-maker ─────────────────────────────────────
    short_video_maker_url: str = Field(
        default="http://localhost:3000",
        validation_alias="SHORT_VIDEO_MAKER_URL",
    )

    # ── Review dashboard ──────────────────────────────────────
    flask_secret_key: Optional[str] = Field(
        default=None,
        validation_alias="FLASK_SECRET_KEY",
    )
    review_auth_user: Optional[str] = Field(
        default=None,
        validation_alias="REVIEW_AUTH_USER",
    )
    review_auth_pass: Optional[str] = Field(
        default=None,
        validation_alias="REVIEW_AUTH_PASS",
    )

    # ── Threads (Meta Threads API v1) ──────────────────────────
    threads_access_token: Optional[str] = Field(
        default=None,
        validation_alias="THREADS_ACCESS_TOKEN",
    )
    threads_user_id: Optional[str] = Field(
        default=None,
        validation_alias="THREADS_USER_ID",
    )
    threads_token_issued_at: Optional[str] = Field(
        default=None,
        validation_alias="THREADS_TOKEN_ISSUED_AT",
    )

    # ── TikTok (Content Posting API v2) ─────────────────────
    tiktok_client_key: Optional[str] = Field(
        default=None,
        validation_alias="TIKTOK_CLIENT_KEY",
    )
    tiktok_client_secret: Optional[str] = Field(
        default=None,
        validation_alias="TIKTOK_CLIENT_SECRET",
    )
    tiktok_access_token: Optional[str] = Field(
        default=None,
        validation_alias="TIKTOK_ACCESS_TOKEN",
    )
    tiktok_refresh_token: Optional[str] = Field(
        default=None,
        validation_alias="TIKTOK_REFRESH_TOKEN",
    )
    tiktok_token_issued_at: Optional[str] = Field(
        default=None,
        validation_alias="TIKTOK_TOKEN_ISSUED_AT",
    )
    tiktok_audit_approved: Optional[str] = Field(
        default="false",
        validation_alias="TIKTOK_AUDIT_APPROVED",
    )

    # ── Cloudflare Tunnel (public URL for local CDN) ────────────
    cloudflare_tunnel_url: Optional[str] = Field(
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
    def validate_for_niche(self, niche_id: str) -> List[str]:
        """Check which credentials are missing for *niche_id*.

        Logs a warning per missing field but never raises.
        Returns the list of missing field names (empty = all good).
        """
        requirements = NICHE_REQUIREMENTS.get(
            niche_id, NICHE_REQUIREMENTS["default"]
        )
        missing: List[str] = []
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
