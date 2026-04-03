"""Niche registry API endpoint.

Routes:
    GET /api/v1/niches                       -- list all niches
    GET /api/v1/niches/<niche_id>/config      -- merged niche config
    GET /api/v1/niches/<niche_id>/credentials -- credential status per platform
"""

import logging
import os
from pathlib import Path

import yaml
from flask import Blueprint

from server.core.responses import api_error, api_not_found, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("niches_api", __name__, url_prefix="/api/v1/niches")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REGISTRY_PATH = PROJECT_ROOT / "configs" / "niches_registry.yaml"
_GENLAB_ROOT = PROJECT_ROOT.parent


def _load_registry() -> list[dict]:
    """Load niche definitions from YAML."""
    if not REGISTRY_PATH.exists():
        logger.warning("Niche registry not found at %s", REGISTRY_PATH)
        return []
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("niches", [])


def _find_niche(niche_id: str) -> dict | None:
    """Look up a niche entry by ID from the registry."""
    for niche in _load_registry():
        if niche["id"] == niche_id:
            return niche
    return None


def _config_dir_for_niche(niche_id: str) -> Path | None:
    """Resolve the config/ directory for a given niche_id."""
    niche = _find_niche(niche_id)
    if not niche:
        return None
    folder = niche["folder"]
    # CriticalRush stores gaming config in niches/gaming/config/
    if folder == "CriticalRush" and niche_id == "gaming":
        path = _GENLAB_ROOT / folder / "niches" / "gaming" / "config"
    else:
        path = _GENLAB_ROOT / folder / "config"
    return path if path.is_dir() else None


# Per-niche credential env var prefixes
_NICHE_CREDENTIAL_PREFIXES: dict[str, str] = {
    "ai_creators": "",          # BB uses unprefixed env vars
    "gaming": "CRITICALRUSH_",
    "sports": "CLUTCHWIRE_",
    "movies": "SPLICEREEL_",
    "anime": "FRAMEDRIFT_",
}

# Platforms and the env var suffixes that indicate they're configured
_PLATFORM_CREDENTIALS: dict[str, list[str]] = {
    "instagram": ["META_ACCESS_TOKEN", "META_IG_USER_ID"],
    "youtube": ["YOUTUBE_REFRESH_TOKEN"],
    "facebook": ["META_ACCESS_TOKEN", "META_PAGE_ID"],
    "twitter": ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"],
    "threads": ["THREADS_ACCESS_TOKEN"],
    "tiktok": ["TIKTOK_ACCESS_TOKEN"],
}


@bp.route("", methods=["GET"])
def list_niches():
    """Return all registered niches with their status."""
    niches = _load_registry()
    result = []
    for niche in niches:
        result.append({
            "id": niche["id"],
            "display_name": niche["display_name"],
            "tagline": niche.get("tagline", ""),
            "status": niche.get("status", "coming_soon"),
            "accent_hex": niche.get("accent_hex", "#6366f1"),
            "last_run": None,
            "pending_review": 0,
        })
    return api_success(data=result)


@bp.route("/<niche_id>/config", methods=["GET"])
def get_niche_config(niche_id: str):
    """Return full niche config (niche.yaml + sources.yaml merged)."""
    niche = _find_niche(niche_id)
    if not niche:
        return api_not_found(message=f"Niche '{niche_id}' not found in registry")

    config_dir = _config_dir_for_niche(niche_id)
    if not config_dir:
        return api_not_found(
            message=f"Config directory not found for niche '{niche_id}'"
        )

    merged: dict = {"niche_id": niche_id, "folder": niche["folder"]}

    # Load and merge key config files
    config_files = ["niche.yaml", "sources.yaml", "schedule.yaml", "publishing.yaml"]
    for filename in config_files:
        filepath = config_dir / filename
        if filepath.is_file():
            try:
                with open(filepath) as f:
                    content = yaml.safe_load(f) or {}
                key = filename.replace(".yaml", "")
                merged[key] = content
            except (yaml.YAMLError, OSError) as exc:
                logger.warning("Failed to load %s for %s: %s", filename, niche_id, exc)
                merged[filename.replace(".yaml", "")] = {"error": str(exc)}

    return api_success(data=merged)


@bp.route("/<niche_id>/credentials", methods=["GET"])
def check_niche_credentials(niche_id: str):
    """Check which credentials are configured for a niche.

    Returns {platform: "configured"/"missing"} per platform.
    Does NOT expose actual credential values.
    """
    niche = _find_niche(niche_id)
    if not niche:
        return api_not_found(message=f"Niche '{niche_id}' not found in registry")

    prefix = _NICHE_CREDENTIAL_PREFIXES.get(niche_id)
    if prefix is None:
        return api_error(
            error=f"No credential mapping for niche '{niche_id}'",
            code=400,
        )

    result: dict[str, dict] = {}

    for platform, suffixes in _PLATFORM_CREDENTIALS.items():
        env_vars = [f"{prefix}{suffix}" for suffix in suffixes]
        present = []
        missing = []
        for var in env_vars:
            if os.environ.get(var):
                present.append(var)
            else:
                missing.append(var)

        if not missing:
            status = "configured"
        elif present:
            status = "partial"
        else:
            status = "missing"

        result[platform] = {
            "status": status,
            "required_vars": len(env_vars),
            "configured_vars": len(present),
        }

    return api_success(data={
        "niche_id": niche_id,
        "display_name": niche["display_name"],
        "platforms": result,
    })
