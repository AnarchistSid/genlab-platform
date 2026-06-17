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
        slots = (
            data.get("instagram", {}).get("schedule_slots")
            or data.get("schedule_slots")
            or data.get("publish_times")
            or ["12:00"]
        )  # Default to 12:00 IST
        timezone = data.get("instagram", {}).get("timezone", "Asia/Kolkata")
        return api_success(
            data={
                "data": {
                    "slots": slots,
                    "timezone": timezone,
                }
            }
        )
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
    return api_success(
        data={"data": [{"value": v, "label": v.replace("_", " ").title()} for v in sorted(values)]}
    )


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
        platform_info.append(
            {
                "name": p,
                "enabled": cfg.get("enabled", True),
            }
        )
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
            result.append(
                {
                    "id": tpl.get("template_id", ""),
                    "name": tpl.get("name", ""),
                    "type": tpl.get("format", "reel"),
                    "best_for": tpl.get("best_for", []),
                    "structure": tpl.get("structure", []),
                    "default_cta": tpl.get("default_cta", ""),
                    "notes": tpl.get("notes", ""),
                }
            )
        return api_success(data={"data": result})
    # Fallback: try Microsoft Lists
    try:
        from server.core.graph_sync import get_sync_client

        records = get_sync_client().templates.all()
        return api_success(data={"data": [{"id": r["id"], **r.get("fields", {})} for r in records]})
    except Exception as e:
        return api_error(error=str(e), code=502)


# ── M-19: sources YouTube-channels edit API ────────────────
#
# Operator-facing CRUD for the ``youtube_channels`` list in each
# niche's ``config/sources.yaml``. All 5 niches share this top-level
# key (verified 2026-06-17) so a single endpoint can serve all of
# them. Tracks:
#   * GET    /api/v1/config/sources/youtube-channels?niche_id=X
#   * POST   /api/v1/config/sources/youtube-channels?niche_id=X
#                body {url, name}
#   * DELETE /api/v1/config/sources/youtube-channels?niche_id=X&url=...
#
# Writes preserve comments + structure via ruamel.yaml round-trip
# (per the 2026-06-15 T#69 lesson: PyYAML's yaml.dump strips comments,
# destroys hand-edited config). The 6 system reminders in the operator
# instructions about "PR-based change, NOT a hand-edit" are operator-
# scope; this endpoint is for operator-scope edits authorized via the
# dashboard auth + CSRF gate.
#
# Validation:
#   * niche_id whitelisted via _config_dir_for_niche (returns None on
#     unknown → 404)
#   * URL must be http/https YouTube domain
#   * No path traversal possible — niche → path mapping goes through
#     the same registry-backed lookup the read endpoints use
#   * name is required, max 100 chars

_VALID_YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "youtu.be"}
_NAME_MAX_LEN = 100


def _load_sources_yaml_rt(niche_id: str):
    """Load sources.yaml via ruamel round-trip (preserves comments).

    Returns ``(loaded_data, path)`` or ``(None, None)`` if no niche /
    no file. Caller's responsibility to handle the None path.
    """
    from ruamel.yaml import YAML

    config_dir = _config_dir_for_niche(niche_id)
    if config_dir is None:
        return None, None
    path = config_dir / "sources.yaml"
    if not path.exists():
        return None, None
    yaml_rt = YAML(typ="rt")
    yaml_rt.preserve_quotes = True
    yaml_rt.default_flow_style = False
    yaml_rt.allow_unicode = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    yaml_rt.width = 4096
    with open(path, encoding="utf-8") as f:
        data = yaml_rt.load(f)
    return data, path


def _dump_sources_yaml_rt(data, path):
    """Write sources.yaml back via ruamel round-trip."""
    from ruamel.yaml import YAML

    yaml_rt = YAML(typ="rt")
    yaml_rt.preserve_quotes = True
    yaml_rt.default_flow_style = False
    yaml_rt.allow_unicode = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    yaml_rt.width = 4096
    with open(path, "w", encoding="utf-8") as f:
        yaml_rt.dump(data, f)


def _validate_youtube_url(url: str) -> str | None:
    """Return None if valid, else an error message."""
    from urllib.parse import urlparse

    if not url or not isinstance(url, str):
        return "url is required"
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return "url must use http/https"
    if (parsed.hostname or "").lower() not in _VALID_YOUTUBE_HOSTS:
        return f"url host must be one of {sorted(_VALID_YOUTUBE_HOSTS)}"
    return None


@bp.route("/sources/youtube-channels", methods=["GET"])
def list_youtube_channels():
    """List the ``youtube_channels`` entries for a niche.

    Returns the list in declaration order — the same order the
    pipeline iterates. Useful for the operator to see which channels
    are currently active and where new ones would land.
    """
    niche_id = request.args.get("niche_id", "").strip()
    if not niche_id:
        return api_error(error="niche_id is required", code=400)
    data, _path = _load_sources_yaml_rt(niche_id)
    if data is None:
        return api_not_found(message=f"sources.yaml not found for niche={niche_id}")
    channels = data.get("youtube_channels") or []
    # Normalise to {url, name} — ruamel CommentedMap is dict-like
    result = [{"url": str(c.get("url", "")), "name": str(c.get("name", ""))} for c in channels]
    return api_success(data={"niche_id": niche_id, "youtube_channels": result})


@bp.route("/sources/youtube-channels", methods=["POST"])
def add_youtube_channel():
    """Append a new {url, name} entry to ``youtube_channels``.

    Idempotent on URL: if the URL already exists in the list, returns
    409 — operator's intent for the duplicate ID is ambiguous (re-
    name? re-add at end?) so we surface rather than guess.
    """
    niche_id = request.args.get("niche_id", "").strip()
    if not niche_id:
        return api_error(error="niche_id is required", code=400)
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    name = (body.get("name") or "").strip()
    if not name:
        return api_error(error="name is required", code=400)
    if len(name) > _NAME_MAX_LEN:
        return api_error(error=f"name must be ≤{_NAME_MAX_LEN} chars", code=400)
    url_err = _validate_youtube_url(url)
    if url_err:
        return api_error(error=url_err, code=400)

    data, path = _load_sources_yaml_rt(niche_id)
    if data is None:
        return api_not_found(message=f"sources.yaml not found for niche={niche_id}")
    channels = data.get("youtube_channels")
    if channels is None:
        # The key is missing entirely (shouldn't happen — all 5 niches
        # ship with the key) but degrade to creating it so the
        # operator's first add still works.
        from ruamel.yaml.comments import CommentedSeq

        channels = CommentedSeq()
        data["youtube_channels"] = channels

    for existing in channels:
        if str(existing.get("url", "")).strip() == url:
            return api_error(
                error=f"url already present in youtube_channels: {url}",
                code=409,
            )

    from ruamel.yaml.comments import CommentedMap

    new_entry = CommentedMap()
    new_entry["url"] = url
    new_entry["name"] = name
    channels.append(new_entry)

    try:
        _dump_sources_yaml_rt(data, path)
    except OSError as exc:
        logger.exception("sources.yaml write failed")
        return api_error(error=f"write failed: {exc}", code=500)

    logger.info(
        "[config_routes] M-19 added youtube_channel niche=%s name=%s url=%s",
        niche_id,
        name,
        url,
    )
    return api_success(data={"niche_id": niche_id, "added": {"url": url, "name": name}})


@bp.route("/sources/youtube-channels", methods=["DELETE"])
def remove_youtube_channel():
    """Remove the entry whose ``url`` matches the query param.

    URL is the natural key — names can collide ("Two Minute Papers"
    might appear twice with different channel IDs if someone re-added
    it). URL-as-key gives operators an unambiguous handle.
    """
    niche_id = request.args.get("niche_id", "").strip()
    url = (request.args.get("url") or "").strip()
    if not niche_id:
        return api_error(error="niche_id is required", code=400)
    if not url:
        return api_error(error="url is required", code=400)

    data, path = _load_sources_yaml_rt(niche_id)
    if data is None:
        return api_not_found(message=f"sources.yaml not found for niche={niche_id}")
    channels = data.get("youtube_channels") or []
    new_channels = [c for c in channels if str(c.get("url", "")).strip() != url]
    if len(new_channels) == len(channels):
        return api_not_found(
            message=f"url not found in youtube_channels for niche={niche_id}: {url}"
        )
    # Replace in place to preserve any block-level comments attached
    # to the parent mapping. ruamel CommentedSeq supports slice assign.
    channels[:] = new_channels

    try:
        _dump_sources_yaml_rt(data, path)
    except OSError as exc:
        logger.exception("sources.yaml write failed")
        return api_error(error=f"write failed: {exc}", code=500)

    logger.info(
        "[config_routes] M-19 removed youtube_channel niche=%s url=%s",
        niche_id,
        url,
    )
    return api_success(data={"niche_id": niche_id, "removed": {"url": url}})


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

            if not re.match(
                r"^https://hooks\.slack\.com/services/T[A-Z0-9]+/B[A-Z0-9]+/[A-Za-z0-9]+$",
                webhook_url,
            ):
                return api_error(
                    error="Invalid Slack webhook URL format. Must be https://hooks.slack.com/services/..."
                )
        prefs["slack_webhook_url"] = webhook_url
    if "email_digest" in data and data["email_digest"] in ("daily", "weekly", "never"):
        prefs["email_digest"] = data["email_digest"]
    if "enabled_types" in data and isinstance(data["enabled_types"], list):
        prefs["enabled_types"] = [str(t) for t in data["enabled_types"]]
    _save_notif_prefs(prefs)
    return api_success(data={"status": "ok"})
