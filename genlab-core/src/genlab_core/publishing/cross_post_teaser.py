"""Post a cross-platform teaser after a publish on a source platform.

When a publish succeeds on a supported SOURCE platform (currently
YouTube + Facebook), this module optionally creates an X/Twitter
teaser pointing back to the original post:

  Main tweet  → hook text only (no URL — CLAUDE.md X rule)
  First reply → Source URL with UTM tagging + brief CTA

## Supported routes (2026-06-23)

  YouTube  → X  (PR #480) — opt-in via cross_post.youtube_to_x.enabled
  Facebook → X  (PR Y)    — opt-in via cross_post.facebook_to_x.enabled

Both routes use the same X-posting code; they differ only in the
source-platform filter and the config-flag key. Instagram → X is
intentionally NOT shipped — IG public URLs require shortcode
inference that the current IG client doesn't expose cleanly.

## Why X is always the destination

* X is text-first → a teaser tweet is native-feeling, not the
  awkward "watch on another platform" UX a screenshot would give.
* X's audience overlap with video viewers (YT + FB) is high.
* The first-reply pattern (PR #480) satisfies CLAUDE.md's "no URLs
  in tweet body" rule cleanly.

## Distribution thesis

The 2026-06-22 autonomy synthesis identified distribution as the
revenue bottleneck (operator at 9 IG followers / 7800 monthly
views, code intelligence already mid-tier creator quality).
Cross-platform synergy adds reach without adding content cost —
the hook is already optimized, the video is already rendered, the
source URL is free.

Expected uplift: 5-15% reach per opt-in route (the X teaser drives
source-platform clicks that wouldn't have happened from organic
discovery). UTM tagging makes the lift measurable.

## Opt-in policy

Per-niche flag in ``publishing.yaml``:

  cross_post:
    youtube_to_x:
      enabled: true
    facebook_to_x:
      enabled: true

Default off across all 5 niches — same shadow-rollout pattern
shipped for every other speculative feature this sprint. Operators
flip per-niche per-route when they're ready to test.

## Failure model — strictly non-blocking

Every error is caught and logged at WARN. The source post has
already shipped before this module runs; a cross-post failure must
NEVER unwind that. Sibling architectural pattern to
``affiliate_reply`` (which fires after every successful publish
and shares the same non-blocking guarantee).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml as _yaml

from genlab_core.platforms.x_twitter import XTwitterClient
from genlab_core.publishing.niche_credentials import resolve_twitter_credentials

logger = logging.getLogger(__name__)

# Max characters per tweet — hard X API limit. Reserve a few chars
# for safety against silent emoji-byte miscounts.
_MAX_TWEET_CHARS = 275


# Source-platform → (config-route-key, CTA label) dispatch.
# Adding a new source route means adding one entry here + verifying
# the source client's PublishResult returns a stable public post_url.
# Currently both routes use X as the destination; future routes (e.g.
# YT → Threads) would need a separate dispatch.
_SOURCE_ROUTES: dict[str, dict[str, str]] = {
    # YT Shorts URL: https://youtube.com/shorts/<video_id> — stable.
    "youtube": {"route": "youtube_to_x", "cta": "Full video 🎥 "},
    # FB post URL: https://www.facebook.com/<post_id> — stable.
    # (See platforms/facebook.py:280 — post_url is set from post_id.)
    "facebook": {"route": "facebook_to_x", "cta": "Watch on Facebook 📺 "},
}


def _is_cross_post_enabled(niche_id: str, route: str = "youtube_to_x") -> bool:
    """Read ``publishing.yaml`` for ``cross_post.<route>.enabled``.

    Mirrors the existing pattern in ``publish_all_platforms``
    ``_load_publishing_yaml``: checks both the gaming-style nested
    layout (niches/<niche>/config/) and the flat layout (config/).
    Returns False on ANY error (missing file, malformed YAML, key
    typo, etc.) — opt-out is the safe default.
    """
    try:
        from genlab_core.pipeline.cli import NICHE_DIR_NAMES, _resolve_genlab_root

        root = _resolve_genlab_root()
        dir_name = NICHE_DIR_NAMES.get(niche_id)
        if not dir_name:
            return False
        niche_root = Path(root) / dir_name
        candidates = [
            niche_root / "niches" / niche_id / "config" / "publishing.yaml",
            niche_root / "config" / "publishing.yaml",
        ]
        data: dict | None = None
        for candidate in candidates:
            if candidate.exists():
                with open(candidate, encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                break
        if not isinstance(data, dict):
            return False
        cross = data.get("cross_post", {})
        if not isinstance(cross, dict):
            return False
        route_cfg = cross.get(route, {})
        if not isinstance(route_cfg, dict):
            return False
        return bool(route_cfg.get("enabled", False))
    except Exception:  # noqa: BLE001 — config-read failure → opt-out
        return False


def _build_teaser_text(fields: dict[str, Any]) -> str | None:
    """Build the main tweet body — hook only, no URL.

    Reuses the already-optimized hook (LLM-generated + critic-
    grounded + scratchpad-aware as of PR #479). Returns None when
    no usable hook is present — the caller will skip the entire
    cross-post in that case.
    """
    hook = (fields.get("hook_text") or fields.get("hook") or fields.get("title") or "").strip()
    if not hook:
        return None
    # CLAUDE.md X rule: NO external links in tweet body. We strip
    # the URL from this surface and put it in the first reply.
    # Length cap is purely defensive — the hook itself is already
    # capped at 60 chars by the hook generator, so this almost
    # never fires.
    return hook[:_MAX_TWEET_CHARS]


def _append_utm(url: str) -> str:
    """Append ``?utm_source=x_teaser&utm_medium=cross_post`` to the URL.

    Lets downstream attribution distinguish clicks driven by the X
    teaser from organic YouTube discovery. Tolerant of URLs that
    already have a query string.
    """
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source=x_teaser&utm_medium=cross_post"


def _build_link_reply(post_url: str, cta: str = "Full video 🎥 ") -> str | None:
    """Build the first-reply tweet — UTM-tagged URL + per-route CTA.

    Format: ``<cta> https://<source>/<id>?utm_…``

    The CTA varies per source route ("Full video" for YT, "Watch on
    Facebook" for FB) so the teaser reads naturally for each
    audience. Returns None when no URL.
    """
    if not post_url:
        return None
    tagged_url = _append_utm(post_url)
    body = f"{cta}{tagged_url}"
    return body[:_MAX_TWEET_CHARS]


def post_cross_teaser(
    source_platform: str,
    post_id: str | None,
    post_url: str | None,
    fields: dict[str, Any],
    niche_id: str,
) -> None:
    """Post a teaser on X pointing back to a supported source post.

    Source-routing is via the ``_SOURCE_ROUTES`` dispatch table.
    Currently YT + FB are supported; everything else short-circuits.
    Adding a new source route means: (a) verify the source client's
    PublishResult returns a stable public post_url, (b) add an entry
    to ``_SOURCE_ROUTES`` with the YAML config-key + CTA copy.

    Strictly non-blocking. Mirrors ``affiliate_reply.post_affiliate_reply``
    semantics: any error is caught and logged at WARN; the main
    publish has already shipped.
    """
    # Route dispatch — non-supported source platforms short-circuit
    # immediately. Keeps the callsite (parallel_publish._on_success)
    # cheap for the most common case (publishes that aren't cross-
    # postable, e.g. X itself, IG, Threads, TikTok).
    route_info = _SOURCE_ROUTES.get(source_platform)
    if route_info is None:
        return
    if not post_id or not post_url:
        return

    route_key = route_info["route"]
    cta = route_info["cta"]

    # Per-niche opt-in — default-off shadow rollout.
    if not _is_cross_post_enabled(niche_id, route=route_key):
        return

    teaser_text = _build_teaser_text(fields)
    if not teaser_text:
        logger.debug(
            "[cross_teaser] %s/%s — no hook text, skipping",
            source_platform,
            post_id,
        )
        return

    link_reply = _build_link_reply(post_url, cta=cta)
    if not link_reply:
        # post_url was None — handled above already but defense in depth
        return

    try:
        creds = resolve_twitter_credentials(niche_id)
        if not creds.get("api_key"):
            logger.debug(
                "[cross_teaser] %s/%s — no Twitter creds for niche=%s, skipping",
                source_platform,
                post_id,
                niche_id,
            )
            return
        x = XTwitterClient(**creds)

        # Step 1: post main teaser tweet (text only, no URL per X rule)
        tweet_id = x._post_single_tweet(text=teaser_text)
        if not tweet_id:
            logger.warning(
                "[cross_teaser] Main teaser tweet failed for %s/%s "
                "(non-blocking; main publish unaffected)",
                source_platform,
                post_id,
            )
            return

        # Step 2: post UTM-tagged URL as first reply
        ok = x.post_reply(tweet_id, link_reply, context_id=f"cross_teaser:{post_id}")
        if not ok:
            # Main tweet succeeded but the URL reply didn't —
            # surface this clearly so the operator can investigate
            # rate-limit / quota issues. The teaser still ran (the
            # main hook is in front of the X audience), just
            # without the clickable link.
            logger.warning(
                "[cross_teaser] First-reply URL failed for %s/%s tweet=%s "
                "(main tweet succeeded but link reply did not)",
                source_platform,
                post_id,
                tweet_id,
            )
            return

        logger.info(
            "[cross_teaser] %s → X: %s → tweet=%s + URL reply",
            source_platform.capitalize(),
            post_id,
            tweet_id,
        )

    except Exception as exc:
        logger.warning(
            "[cross_teaser] Failed for %s/%s (non-blocking): %s",
            source_platform,
            post_id,
            exc,
        )
