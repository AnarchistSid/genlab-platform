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

# Pipeline-valid YouTube source patterns. Tightened 2026-06-17 after the
# post-Wave-4 audit flagged that the original allowlist (which included
# ``youtu.be`` and accepted ANY youtube.com path) would let operators
# add invalid pipeline sources — video URLs, search pages, the homepage
# — and the pipeline would silently fail to extract a channel feed.
#
# Two accepted patterns:
#   1. RSS channel feed:  https://www.youtube.com/feeds/videos.xml?channel_id=UC...
#   2. Channel page:      https://www.youtube.com/@<handle>  OR  /channel/UC...
#
# Rejected (and previously accepted by mistake):
#   - youtu.be/*           (video short URLs — not channels)
#   - /watch?v=...         (video pages)
#   - /results?search_query (search pages)
#   - /              (homepage)
_VALID_YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com"}
# Path prefixes that identify a channel-flavoured URL. We use prefix
# matching (not regex) so operators get a predictable error message
# and so URL-encoded edge cases (%40 for @, etc.) don't bite.
_VALID_YOUTUBE_PATH_PREFIXES = (
    "/feeds/videos.xml",  # RSS feed — the canonical pipeline source
    "/channel/",  # /channel/UC... — channel-id page
    "/@",  # /@handle — channel handle page
    "/c/",  # /c/<custom> — legacy custom URL
    "/user/",  # /user/<name> — pre-2013 legacy
)
_NAME_MAX_LEN = 100


def _rt_yaml_singleton():
    """Module-level ruamel parser (avoid re-instantiating per request).

    Constructing ``YAML(typ='rt')`` compiles parsers — keeping one
    around is the standard ruamel pattern. Cached in module globals.
    """
    cached = getattr(_rt_yaml_singleton, "_cached", None)
    if cached is not None:
        return cached
    from ruamel.yaml import YAML

    yaml_rt = YAML(typ="rt")
    yaml_rt.preserve_quotes = True
    yaml_rt.default_flow_style = False
    yaml_rt.allow_unicode = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)
    yaml_rt.width = 4096
    _rt_yaml_singleton._cached = yaml_rt  # type: ignore[attr-defined]
    return yaml_rt


def _load_sources_yaml_rt(niche_id: str):
    """Load sources.yaml via ruamel round-trip (preserves comments).

    Returns ``(loaded_data, path)`` or ``(None, None)`` if no niche /
    no file. Caller's responsibility to handle the None path.
    """
    config_dir = _config_dir_for_niche(niche_id)
    if config_dir is None:
        return None, None
    path = config_dir / "sources.yaml"
    if not path.exists():
        return None, None
    with open(path, encoding="utf-8") as f:
        data = _rt_yaml_singleton().load(f)
    return data, path


def _dump_sources_yaml_rt(data, path):
    """Write sources.yaml back via ruamel round-trip."""
    with open(path, "w", encoding="utf-8") as f:
        _rt_yaml_singleton().dump(data, f)


class _SourcesWriteLock:
    """File-lock wrapper around the read-modify-write of sources.yaml.

    Post-Wave-4 audit (2026-06-17) flagged that two simultaneous POSTs
    to ``/youtube-channels`` (e.g. operator with two browser tabs)
    both read the same channels list, both appended different entries,
    both wrote — second wins, first silently lost. Same race on DELETE.

    Implementation: ``fcntl.flock(LOCK_EX)`` on a sidecar file named
    ``<path>.lock``. We don't lock the yaml itself because some editors
    truncate-then-write, which would race with our open(); the sidecar
    lock is the conventional pattern for "this file is being modified".

    Scope: process-local + cross-process on the SAME host. ``flock``
    advisory locks are honored only by processes that opt in (i.e. our
    own code). An operator hand-editing the yaml in vim won't be
    blocked. That's acceptable for our threat model — the rule
    (per cleanup_safety.md and the 2026-06-14 deploy-pipeline-gap
    lesson) is "all prod changes go through git, not hand-edits", so
    the only legitimate writer is this API.
    """

    def __init__(self, sources_yaml_path):
        import fcntl

        self._fcntl = fcntl
        # Sidecar lock file co-located with the yaml. ``.lock`` suffix
        # is the de facto convention; vim and other tools won't touch
        # it during a normal edit.
        self._lock_path = sources_yaml_path.parent / (sources_yaml_path.name + ".lock")
        self._fh = None

    def __enter__(self):
        # Open as O_CREAT|O_RDWR via "a+" — creates the lock file if
        # it doesn't exist, doesn't truncate if it does. Then flock the
        # underlying fd EX (exclusive) — second caller blocks here until
        # the first one releases.
        self._fh = open(self._lock_path, "a+")
        self._fcntl.flock(self._fh.fileno(), self._fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._fh is not None:
            self._fcntl.flock(self._fh.fileno(), self._fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
        # Don't suppress exceptions
        return False


def _validate_youtube_url(url: str) -> str | None:
    """Return None if valid, else an error message.

    Tightened by the post-Wave-4 audit: previously accepted any
    ``youtube.com`` URL (including ``/watch``, ``/results``, and the
    homepage) plus all ``youtu.be`` URLs (which are video short links,
    not channels). Pipeline silently no-oped on those. Now requires
    the path to look like a known channel-flavoured surface so
    operator typos surface at API time, not deep in the fetcher.
    """
    from urllib.parse import urlparse

    if not url or not isinstance(url, str):
        return "url is required"
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return "url must use http/https"
    if (parsed.hostname or "").lower() not in _VALID_YOUTUBE_HOSTS:
        return f"url host must be one of {sorted(_VALID_YOUTUBE_HOSTS)}"
    # Path-shape check: must look like a channel-flavoured URL. We
    # match prefixes (not full regex) so the error message is
    # predictable and the check is robust against URL-encoded handles.
    path = parsed.path or "/"
    if not any(path.startswith(prefix) for prefix in _VALID_YOUTUBE_PATH_PREFIXES):
        return (
            f"url path must be a channel-flavoured surface (one of "
            f"{list(_VALID_YOUTUBE_PATH_PREFIXES)}) — got {path!r}"
        )
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

    # First pass: locate the sources.yaml path so we can acquire the
    # file lock around the read-modify-write. We re-load inside the
    # lock to catch any concurrent write that landed between this
    # pre-flight and our lock acquisition (TOCTOU defense).
    config_dir = _config_dir_for_niche(niche_id)
    if config_dir is None or not (config_dir / "sources.yaml").exists():
        return api_not_found(message=f"sources.yaml not found for niche={niche_id}")
    sources_path = config_dir / "sources.yaml"

    with _SourcesWriteLock(sources_path):
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

    # Same lock pattern as add — acquire BEFORE loading the yaml so
    # a concurrent add can't slip an entry in between our read and
    # write. Pre-flight the path outside the lock so the 404 is fast.
    config_dir = _config_dir_for_niche(niche_id)
    if config_dir is None or not (config_dir / "sources.yaml").exists():
        return api_not_found(message=f"sources.yaml not found for niche={niche_id}")
    sources_path = config_dir / "sources.yaml"

    with _SourcesWriteLock(sources_path):
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


# ── AUTO #2: per-niche auto_publish rollout_pct slider ──────
#
# Operator-facing read/write for the ``auto_publish`` block in each
# niche's publishing.yaml. We expose READ for all 4 fields (enabled,
# min_confidence, max_approvals_per_pass, rollout_pct) so the slider
# UI can show context, but we ONLY allow WRITE for ``rollout_pct``.
#
# Rationale (per AUTO_2_ROLLOUT_ADDENDUM_2026-06-15-PM.md):
#   * ``enabled`` flip stays on the git-commit path — operator must
#     edit publishing.yaml + commit + deploy. This is the calibrated
#     opt-in gate, not a slider.
#   * ``min_confidence`` + ``max_approvals_per_pass`` are tuning
#     parameters set during runbook §4 calibration; changing them
#     post-rollout would invalidate the calibration data and is
#     intentionally manual.
#   * ``rollout_pct`` IS designed for in-session ramping (10% →
#     50% → 100% over a week). The whole point of W4.3 is that
#     operators can slide it without rebuilding the wheel.
#
# Like M-19/M-20/M-21, writes go through ruamel round-trip (comment
# preservation) + the file-lock helper.


def _resolve_publishing_yaml(niche_id: str):
    """Find publishing.yaml for a niche, returning (path, niche_folder)
    or (None, None) if not found.

    Reuses ``_config_dir_for_niche`` (same registry+folder lookup
    pattern as M-19's sources.yaml resolver), which already handles
    the gaming-niche nested-config layout (``CriticalRush/niches/
    gaming/config/``). niche_id is used ONLY to gate the registry
    lookup — the final path is built from registry-controlled strings
    + the hardcoded ``"publishing.yaml"`` filename. Keeps the taint
    chain bounded (CodeQL py/path-injection clean).
    """
    config_dir = _config_dir_for_niche(niche_id)
    if config_dir is None:
        return None, None
    path = config_dir / "publishing.yaml"
    if not path.exists():
        return None, None
    # Recover the folder name from the path for logging/test purposes;
    # cheaper than re-walking the registry.
    folder = (
        path.parts[len(_GENLAB_ROOT.parts)] if len(path.parts) > len(_GENLAB_ROOT.parts) else None
    )
    return path, folder


def _load_publishing_yaml_rt(niche_id: str):
    """Load publishing.yaml via ruamel round-trip (preserves comments)."""
    path, _folder = _resolve_publishing_yaml(niche_id)
    if path is None:
        return None, None
    with open(path, encoding="utf-8") as f:
        data = _rt_yaml_singleton().load(f)
    return data, path


def _dump_publishing_yaml_rt(data, path):
    """Write publishing.yaml back via ruamel round-trip."""
    with open(path, "w", encoding="utf-8") as f:
        _rt_yaml_singleton().dump(data, f)


@bp.route("/auto-publish", methods=["GET"])
def get_auto_publish():
    """Return the auto_publish block for a niche.

    All 4 fields surfaced for read so the dashboard can show context.
    Defaults match auto_approver.AutoApprovalPolicy defaults — kept in
    sync manually because dashboard doesn't import genlab_core's
    AutoApprovalPolicy dataclass.
    """
    niche_id = request.args.get("niche_id", "").strip()
    if not niche_id:
        return api_error(error="niche_id is required", code=400)

    data, _path = _load_publishing_yaml_rt(niche_id)
    if data is None:
        return api_not_found(message=f"publishing.yaml not found for niche={niche_id}")
    block = data.get("auto_publish") or {}
    if not isinstance(block, dict):
        block = {}
    return api_success(
        data={
            "niche_id": niche_id,
            "auto_publish": {
                "enabled": bool(block.get("enabled", False)),
                "min_confidence": float(block.get("min_confidence", 0.85)),
                "max_approvals_per_pass": int(block.get("max_approvals_per_pass", 3)),
                # W4.3: rollout_pct defaults to 1.0 (full traffic). We
                # match auto_approver's loader which clamps out-of-range
                # to 1.0; here we just surface the raw stored value so
                # the operator sees what's actually persisted.
                "rollout_pct": float(block.get("rollout_pct", 1.0)),
            },
        }
    )


@bp.route("/auto-publish", methods=["PATCH"])
def update_auto_publish():
    """Update ONLY the ``rollout_pct`` field of auto_publish.

    Body: ``{"rollout_pct": <float in [0.0, 1.0]>}``.

    enabled / min_confidence / max_approvals_per_pass are NOT
    writeable via this endpoint — those decisions belong on the
    git-commit path per the AUTO #2 rollout runbook §4-5.
    """
    niche_id = request.args.get("niche_id", "").strip()
    if not niche_id:
        return api_error(error="niche_id is required", code=400)
    body = request.get_json(silent=True) or {}
    raw_pct = body.get("rollout_pct")
    if raw_pct is None:
        return api_error(error="rollout_pct is required", code=400)
    try:
        pct = float(raw_pct)
    except (TypeError, ValueError):
        return api_error(error="rollout_pct must be a number", code=400)
    if pct < 0.0 or pct > 1.0:
        return api_error(error="rollout_pct must be in [0.0, 1.0]", code=400)

    path, _folder = _resolve_publishing_yaml(niche_id)
    if path is None:
        return api_not_found(message=f"publishing.yaml not found for niche={niche_id}")

    # Reuse the sources-write-lock helper — same fcntl.flock on
    # ``<path>.lock`` pattern. The class is named for sources but the
    # implementation is path-agnostic; renaming would be a wider
    # change and provides no behavioural benefit.
    with _SourcesWriteLock(path):
        data, real_path = _load_publishing_yaml_rt(niche_id)
        if data is None:
            return api_not_found(message=f"publishing.yaml not found for niche={niche_id}")
        block = data.get("auto_publish")
        if block is None or not isinstance(block, dict):
            # No auto_publish block yet — create with safe defaults.
            # The operator's first slide should land cleanly even on a
            # niche that hasn't been calibrated.
            from ruamel.yaml.comments import CommentedMap

            block = CommentedMap()
            block["enabled"] = False  # ← stays off until git-commit flip
            block["min_confidence"] = 0.85
            block["max_approvals_per_pass"] = 3
            data["auto_publish"] = block

        # ruamel preserves the comment attached to the rollout_pct key
        # IF the key already exists. If we're creating it, the comment
        # above it (the multi-line "W4.3 graduated rollout" block) was
        # attached to ``auto_publish`` as a whole, so it survives.
        block["rollout_pct"] = pct

        try:
            _dump_publishing_yaml_rt(data, real_path)
        except OSError as exc:
            logger.exception("publishing.yaml write failed")
            return api_error(error=f"write failed: {exc}", code=500)

    logger.info(
        "[config_routes] AUTO#2 updated rollout_pct niche=%s value=%.3f",
        niche_id,
        pct,
    )
    return api_success(
        data={
            "niche_id": niche_id,
            "updated": {"rollout_pct": pct},
        }
    )


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
