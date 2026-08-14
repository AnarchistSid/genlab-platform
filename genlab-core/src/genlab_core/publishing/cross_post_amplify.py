"""Cross-platform amplification module (Phase 3.E, 2026-08-14).

Sits alongside ``cross_post_teaser.py`` (2026-06-23). That module
posted an X teaser on YT/FB source. THIS module handles the
in-scope-per-rule-#23 amplification routes:

  * YouTube  → Threads (text post)   — opt-in
    ``cross_post.youtube_to_threads.enabled``
  * Facebook → self-comment with YT  — opt-in + threshold
    ``cross_post.facebook_self_comment.{enabled, min_reach}``

Rule #23 explicitly deprioritises X/TikTok, so cross_post_teaser
stays for legacy tenants but new routes go here.

## Design

Same non-blocking contract as ``cross_post_teaser``: the source
publish has already succeeded when this fires; an amplify failure
must NEVER regress the source publish. Every path fails-open to
False, WARN-logged, so the operator can spot pattern-level failures
via journal grep.

## Config gates

::

  cross_post:
    youtube_to_threads:
      enabled: true
    facebook_self_comment:
      enabled: true
      min_reach: 1000          # only self-comment on posts with
                               # ≥ this many views (skip low-reach
                               # posts to avoid comment-spam feel)

Cold-start / missing config = False = feature stays off.

## Deferred (Phase 3.E scope trim, 2026-08-14)

  * ``instagram_pre_drop`` (IG story 2h before YT) — requires
    IG Story API + a delayed-scheduling worker. Not fit-for-purpose
    in a 1.5-session budget.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

import yaml as _yaml

logger = logging.getLogger(__name__)

_MAX_THREADS_CHARS: Final[int] = 500  # Threads posts cap at 500

# ── Config-read helpers (mirror cross_post_teaser._is_cross_post_enabled) ──


def _load_publishing_yaml(niche_id: str) -> dict:
    """Same 2-candidate resolution as cross_post_teaser — nested
    layout for gaming-style niches + flat for BlackboxBrief. Returns
    empty dict on any failure (opt-out = safe default)."""
    try:
        from genlab_core.pipeline.cli import (
            NICHE_DIR_NAMES,
            _resolve_genlab_root,
        )
        root = _resolve_genlab_root()
        dir_name = NICHE_DIR_NAMES.get(niche_id)
        if not dir_name:
            return {}
        niche_root = Path(root) / dir_name
        for candidate in (
            niche_root / "niches" / niche_id / "config" / "publishing.yaml",
            niche_root / "config" / "publishing.yaml",
        ):
            if candidate.exists():
                with open(candidate, encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    return data
                return {}
    except Exception:  # noqa: BLE001
        return {}
    return {}


def _route_enabled(niche_id: str, route: str) -> bool:
    """True if ``cross_post.<route>.enabled`` is truthy in the
    niche's publishing.yaml."""
    data = _load_publishing_yaml(niche_id)
    cross = data.get("cross_post") or {}
    if not isinstance(cross, dict):
        return False
    route_cfg = cross.get(route) or {}
    if not isinstance(route_cfg, dict):
        return False
    return bool(route_cfg.get("enabled", False))


def _fb_min_reach_threshold(niche_id: str) -> int:
    """Read the min_reach threshold. Default 1000 — a self-comment
    on a low-reach post reads spammy + wastes attribution."""
    data = _load_publishing_yaml(niche_id)
    cross = data.get("cross_post") or {}
    fbsc = cross.get("facebook_self_comment") or {}
    if not isinstance(fbsc, dict):
        return 1000
    try:
        return int(fbsc.get("min_reach", 1000))
    except (TypeError, ValueError):
        return 1000


# ── URL utilities ────────────────────────────────────────────────


def _append_utm(url: str, source: str) -> str:
    """utm_source=<source>&utm_medium=cross_post so downstream
    attribution distinguishes drives from each cross-post route."""
    if not url:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source={source}&utm_medium=cross_post"


# ── YouTube → Threads: root text post with YT link ────────────────


def _build_threads_amplify_text(
    fields: dict[str, Any], yt_url: str,
) -> str | None:
    """Build the Threads root post: hook + UTM-tagged YT link.

    Returns None when no usable hook — caller skips the amplify.
    """
    hook = (
        fields.get("hook_text") or fields.get("hook") or fields.get("title") or ""
    ).strip()
    if not hook or not yt_url:
        return None
    tagged = _append_utm(yt_url, "threads_amplify")
    body = f"{hook}\n\nFull video 🎥 {tagged}"
    return body[:_MAX_THREADS_CHARS]


def post_youtube_to_threads_amplify(
    source_platform: str,
    yt_post_url: str,
    fields: dict[str, Any],
    niche_id: str,
    *,
    _threads_client_factory=None,  # test seam
) -> bool:
    """Called from parallel_publish AFTER a successful YT publish.

    Returns True on success, False on any failure — including the
    common cases (route disabled, non-YT source, no hook, Threads
    creds missing). Never raises.
    """
    if source_platform != "youtube":
        return False
    if not _route_enabled(niche_id, "youtube_to_threads"):
        return False

    text = _build_threads_amplify_text(fields, yt_post_url)
    if text is None:
        logger.info(
            "[amplify] yt→threads: skip niche=%s — no hook or url",
            niche_id,
        )
        return False

    try:
        if _threads_client_factory is not None:
            client = _threads_client_factory(niche_id)
        else:
            from genlab_core.platforms.threads import ThreadsClient
            client = ThreadsClient(niche_id=niche_id)
    except Exception as exc:
        logger.warning(
            "[amplify] yt→threads: client init failed niche=%s: %s",
            niche_id, exc,
        )
        return False

    try:
        # Use the private _publish_text helper — the public
        # publish() requires media_path which we don't have here
        # (text-only amplify post).
        result = client._publish_text(caption=text)
    except Exception as exc:
        logger.warning(
            "[amplify] yt→threads: publish crashed niche=%s: %s",
            niche_id, exc,
        )
        return False

    if not getattr(result, "success", False):
        logger.warning(
            "[amplify] yt→threads: publish failed niche=%s error=%r",
            niche_id, getattr(result, "error", None),
        )
        return False
    logger.info(
        "[amplify] yt→threads: SUCCESS niche=%s threads_id=%s",
        niche_id, getattr(result, "post_id", "?"),
    )
    return True


# ── Facebook self-comment with YT link on high-reach posts ────────


def _build_fb_self_comment(yt_url: str) -> str | None:
    """Simple: 'Full video 🎥 <utm-tagged url>'. Facebook comment
    length cap is 8000 chars — we're nowhere near it."""
    if not yt_url:
        return None
    return f"Full video 🎥 {_append_utm(yt_url, 'fb_self_comment')}"


def post_facebook_self_comment(
    source_platform: str,
    fb_post_id: str,
    fb_reach: int,
    yt_post_url: str,
    niche_id: str,
    *,
    _fb_client_factory=None,  # test seam
) -> bool:
    """Called AFTER FB publish + 24h-metric-fetch (when reach is
    known). Posts a self-comment on the FB post with the YT link,
    but ONLY when fb_reach >= min_reach (default 1000). Never
    fires when source isn't FB or route disabled.

    Returns True on success, False on skip / any failure.
    """
    if source_platform != "facebook":
        return False
    if not _route_enabled(niche_id, "facebook_self_comment"):
        return False
    if not yt_post_url or not fb_post_id:
        return False

    min_reach = _fb_min_reach_threshold(niche_id)
    if fb_reach < min_reach:
        logger.info(
            "[amplify] fb_self_comment: skip niche=%s post=%s "
            "reach=%d < min_reach=%d",
            niche_id, fb_post_id, fb_reach, min_reach,
        )
        return False

    comment = _build_fb_self_comment(yt_post_url)
    if comment is None:
        return False

    try:
        if _fb_client_factory is not None:
            client = _fb_client_factory(niche_id)
        else:
            from genlab_core.platforms.facebook import FacebookClient
            client = FacebookClient(niche_id=niche_id)
    except Exception as exc:
        logger.warning(
            "[amplify] fb_self_comment: client init failed niche=%s: %s",
            niche_id, exc,
        )
        return False

    try:
        ok = client.post_reply(fb_post_id, comment)
    except Exception as exc:
        logger.warning(
            "[amplify] fb_self_comment: post crashed niche=%s: %s",
            niche_id, exc,
        )
        return False
    if not ok:
        logger.warning(
            "[amplify] fb_self_comment: post_reply returned False niche=%s post=%s",
            niche_id, fb_post_id,
        )
        return False
    logger.info(
        "[amplify] fb_self_comment: SUCCESS niche=%s fb_post=%s (reach=%d)",
        niche_id, fb_post_id, fb_reach,
    )
    return True
