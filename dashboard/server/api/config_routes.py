"""Config & settings API endpoints."""
import json
import logging
from pathlib import Path

import yaml
from flask import Blueprint, request

from server.core.responses import api_error, api_not_found, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("config_api", __name__, url_prefix="/api/v1/config")
settings_bp = Blueprint("settings_api", __name__, url_prefix="/api/v1/settings")

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent.parent
_GENLAB_ROOT = _DASHBOARD_ROOT.parent
_REGISTRY_PATH = _DASHBOARD_ROOT / "configs" / "niches_registry.yaml"

# Fallback for notification prefs (niche-independent)
PROJECT_ROOT = _DASHBOARD_ROOT


def _niche_registry() -> list[dict]:
    """Load niche registry once per request (cheap YAML read)."""
    if not _REGISTRY_PATH.exists():
        return []
    with open(_REGISTRY_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data.get("niches", [])


def _config_dir_for_niche(niche_id: str) -> Path | None:
    """Resolve the config/ directory for a given niche_id."""
    for niche in _niche_registry():
        if niche["id"] == niche_id:
            folder = niche["folder"]
            # CriticalRush stores gaming config in niches/gaming/config/
            if folder == "CriticalRush" and niche_id == "gaming":
                path = _GENLAB_ROOT / folder / "niches" / "gaming" / "config"
            else:
                path = _GENLAB_ROOT / folder / "config"
            return path if path.is_dir() else None
    return None


def _load_yaml(filename, niche_id: str | None = None):
    """Load a YAML config file, optionally scoped to a niche.

    When niche_id is "all" or not set, tries ai_creators as a sensible default
    before falling back to genlab-core shared config.
    """
    effective_niche = niche_id if niche_id and niche_id != "all" else None
    niches_to_try = [effective_niche] if effective_niche else ["ai_creators", "gaming"]

    for nid in niches_to_try:
        config_dir = _config_dir_for_niche(nid)
        if config_dir:
            path = config_dir / filename
            if path.exists():
                with open(path) as f:
                    return yaml.safe_load(f)
    # Fallback: try genlab-core shared config
    fallback = _GENLAB_ROOT / "genlab-core" / "config" / filename
    if fallback.exists():
        with open(fallback) as f:
            return yaml.safe_load(f)
    return None


@bp.route("/sources", methods=["GET"])
def sources():
    niche_id = request.args.get("niche_id")
    data = _load_yaml("sources.yaml", niche_id)
    if data:
        return api_success(data={"data": data})
    return api_not_found(message="Not found")


@bp.route("/schedule-slots", methods=["GET"])
def schedule_slots():
    niche_id = request.args.get("niche_id")
    data = _load_yaml("publishing.yaml", niche_id)
    if data:
        slots = (data.get("instagram", {}).get("schedule_slots")
                 or data.get("schedule_slots")
                 or data.get("publish_times")
                 or ["12:00"])  # Default to 12:00 IST
        timezone = data.get("instagram", {}).get("timezone", "Asia/Kolkata")
        return api_success(data={"data": {
            "slots": slots,
            "timezone": timezone,
        }})
    return api_not_found(message="Not found")


@bp.route("/source-filters", methods=["GET"])
def source_filters():
    """Return unique source platforms/names for filter dropdowns."""
    niche_id = request.args.get("niche_id")
    data = _load_yaml("sources.yaml", niche_id)
    if not data:
        return api_success(data={"data": []})
    sources_list = data.get("sources", [])
    # Also check tiered structure (e.g. sports uses tier_1.sources, tier_2.sources)
    if not sources_list:
        for tier in ["tier_1", "tier_2", "tier_3"]:
            tier_sources = data.get(tier, {}).get("sources", [])
            sources_list.extend(tier_sources)
    # Also check rss_feeds (e.g. gaming uses rss_feeds as top-level key)
    if not sources_list:
        sources_list.extend(data.get("rss_feeds", []))
        sources_list.extend(data.get("youtube_channels", []))
        sources_list.extend(data.get("reddit", {}).get("subreddits", []))
    # Collect unique source types/names — sources use "type" or "platform" or "name"
    values = set()
    for s in sources_list:
        val = s.get("platform") or s.get("type") or s.get("name") or ""
        if val:
            values.add(val)
    return api_success(data={"data": [{"value": v, "label": v.replace("_", " ").title()} for v in sorted(values)]})


@bp.route("/platforms", methods=["GET"])
def platforms():
    """Return enabled publishing platforms from config."""
    niche_id = request.args.get("niche_id")
    data = _load_yaml("publishing.yaml", niche_id)
    if not data:
        return api_success(data={"data": []})
    platforms_cfg = data.get("platforms", {})
    enabled = platforms_cfg.get("enabled_platforms") or platforms_cfg.get("enabled", [])
    platform_info = []
    for p in enabled:
        cfg = data.get(p, {})
        platform_info.append({
            "name": p,
            "enabled": cfg.get("enabled", True),
        })
    return api_success(data={"data": platform_info})


@bp.route("/scoring", methods=["GET"])
def scoring():
    niche_id = request.args.get("niche_id")
    data = _load_yaml("scoring_weights.yaml", niche_id)
    if data:
        return api_success(data={"data": data})
    return api_not_found(message="Not found")


@bp.route("/templates", methods=["GET"])
def templates():
    """Return templates from config/templates.yaml (video-only pipeline).

    Falls back to Microsoft Lists if YAML is unavailable.
    """
    niche_id = request.args.get("niche_id")
    data = _load_yaml("templates.yaml", niche_id)
    if data and data.get("templates"):
        tpl_list = data["templates"]
        result = []
        for tpl in tpl_list:
            result.append({
                "id": tpl.get("template_id", ""),
                "name": tpl.get("name", ""),
                "type": tpl.get("format", "reel"),
                "best_for": tpl.get("best_for", []),
                "structure": tpl.get("structure", []),
                "default_cta": tpl.get("default_cta", ""),
                "notes": tpl.get("notes", ""),
            })
        return api_success(data={"data": result})
    # Fallback: try Microsoft Lists
    try:
        from server.core.graph_sync import get_sync_client
        records = get_sync_client().templates.all()
        return api_success(data={"data": [{"id": r["id"], **r.get("fields", {})} for r in records]})
    except Exception as e:
        return api_error(error=str(e), code=502)


# ── Notification Preferences ───────────────────────────────
_NOTIF_PREFS_FILE = _DASHBOARD_ROOT / ".tmp" / "notification_prefs.json"

_DEFAULT_PREFS = {
    "slack_webhook_url": "",
    "email_digest": "never",
    "enabled_types": ["pipeline_complete", "pipeline_error", "content_review"],
}


def _load_notif_prefs():
    if _NOTIF_PREFS_FILE.exists():
        with open(_NOTIF_PREFS_FILE) as f:
            return json.load(f)
    return dict(_DEFAULT_PREFS)


def _save_notif_prefs(prefs):
    _NOTIF_PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_NOTIF_PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2)


@settings_bp.route("/notifications", methods=["GET"])
def get_notifications():
    return api_success(data={"data": _load_notif_prefs()})


@settings_bp.route("/notifications", methods=["POST"])
def save_notifications():
    data = request.json or {}
    prefs = _load_notif_prefs()
    if "slack_webhook_url" in data:
        webhook_url = str(data["slack_webhook_url"]).strip()
        if webhook_url:
            # Validate Slack webhook URL format
            import re
            if not re.match(r'^https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+$', webhook_url):
                return api_error(error="Invalid Slack webhook URL format. Must be https://hooks.slack.com/services/...")
        prefs["slack_webhook_url"] = webhook_url
    if "email_digest" in data and data["email_digest"] in ("daily", "weekly", "never"):
        prefs["email_digest"] = data["email_digest"]
    if "enabled_types" in data and isinstance(data["enabled_types"], list):
        prefs["enabled_types"] = [str(t) for t in data["enabled_types"]]
    _save_notif_prefs(prefs)
    return api_success(data={"status": "ok"})
