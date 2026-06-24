"""Instagram shortcode resolution for media-kit top-posts.

PR KK (2026-06-23): closes the last platform gap in PR #490's
top-posts content samples. IG posts have a Meta media_id (e.g.
``18067901951361934``) but the public URL uses a separate
alphanumeric shortcode (e.g. ``DWQeTghibLu`` →
``instagram.com/p/DWQeTghibLu/``). The Graph API maps one to the
other via ``GET /{media_id}?fields=permalink``.

## Why shortcodes get a dedicated resolver

YT/FB/X/Threads/TikTok URLs all derive from ``post_id + (handle or
none)`` — pure string templates, no I/O. IG is the exception
because the shortcode lives only in the platform's metadata.
Without this resolver, IG posts get silently filtered out at
``_derive_post_url`` (returns None for "instagram"), and brands
viewing the kit see fewer-than-expected content samples.

## Caching strategy — permanent module-level dict

Shortcodes are **permanent** for a given media_id. Once Meta
assigns ``DWQeTghibLu`` to media_id ``18067...``, that mapping
never changes. So the cache:
  - Module-level ``dict[str, str]`` keyed by media_id
  - Never invalidates
  - Only grows

At ~5 niches × ~30 IG posts/month, cache grows ~1800 entries/year
— trivial memory footprint, eliminates per-request API calls.

For prod-scale (after this clone has been running months), a
Postgres-backed cache could replace the in-memory dict (~5 LOC
swap behind the same ``resolve()`` interface). Defer to v2 — the
in-memory cache survives until process restart, which is the
typical Gunicorn worker lifetime (~hours).

## Per-niche access token

Each niche has its own Meta app token via
``niche_credentials.resolve_meta_credentials(niche_id)["ig_access_token"]``.
Same function the affiliate-reply path uses (PR R-06 audit
2026-06-22 — `affiliate_reply.py:96`). No new credential infra.

## Failure mode

Any error (no token, API call failure, missing permalink field,
malformed response) → return None. The caller (``top_posts_pg``)
treats None as "no URL derivable" and filters the post from the
kit's response. Same fail-OPEN posture as the YT/FB/X handlers
in ``_derive_post_url``.
"""

from __future__ import annotations

import logging
from typing import Final

import requests

logger = logging.getLogger(__name__)

# Graph API endpoint. Always graph.facebook.com per CLAUDE.md
# "Meta / Instagram API" rule — never graph.instagram.com.
#
# PR #515 (2026-06-24): import the canonical version from genlab-core
# (META_GRAPH_BASE_URL) rather than hardcoding v21.0 here. This dashboard
# module was stranded on v21.0 because the R-44 cleanup (genlab-core
# bumped to v22.0) didn't touch downstream consumers. Future bumps should
# update ``META_GRAPH_API_VERSION`` in
# ``genlab_core/platforms/meta_api.py`` and re-deploy — this constant
# auto-tracks.
from genlab_core.platforms.meta_api import META_GRAPH_BASE_URL

_GRAPH_BASE_URL: Final[str] = META_GRAPH_BASE_URL

# Permanent module-level cache. See docstring for why never
# invalidating is correct.
_CACHE: dict[str, str] = {}

# 15s HTTP timeout — matches the affiliate-reply path's posture.
# IG Graph API is generally fast (<500ms); 15s leaves room for
# the rare slow request without blocking the kit response.
_HTTP_TIMEOUT_S: Final[float] = 15.0


def _strip_platform_prefix(media_id: str) -> str:
    """PR #486 W3.2 normalized some post_ids to ``{platform}:{id}``
    shape. Strip the prefix so the Graph API sees the bare media_id.
    Mirrors the same strip in ``top_posts_pg._derive_post_url``.
    """
    if ":" in media_id:
        return media_id.split(":", 1)[1]
    return media_id


def _extract_shortcode_from_permalink(permalink: str) -> str | None:
    """Parse the shortcode out of a Graph API permalink.

    Permalinks look like:
      https://www.instagram.com/p/DWQeTghibLu/
      https://www.instagram.com/reel/CXxXxXxXxXx/   (reels)
      https://www.instagram.com/tv/...               (IGTV, legacy)

    Returns the segment after ``/p/``, ``/reel/``, or ``/tv/``.
    None when no recognized pattern matches — safer than returning
    a malformed URL.
    """
    if not permalink:
        return None
    for marker in ("/p/", "/reel/", "/tv/"):
        idx = permalink.find(marker)
        if idx < 0:
            continue
        after = permalink[idx + len(marker) :]
        # Strip trailing slash + query string if present
        shortcode = after.split("/")[0].split("?")[0]
        if shortcode:
            return shortcode
    return None


def resolve(media_id: str, niche_id: str) -> str | None:
    """Resolve an IG media_id to its public shortcode.

    Returns the shortcode (e.g. ``"DWQeTghibLu"``) or None when:
      - media_id empty
      - no IG access token for the niche
      - Graph API returns an error
      - permalink field missing or malformed

    Cache hit returns immediately. Cache miss issues a single
    Graph API call + caches the result before returning.
    """
    if not media_id:
        return None
    media_id = _strip_platform_prefix(media_id)
    if not media_id:
        return None

    cached = _CACHE.get(media_id)
    if cached is not None:
        return cached

    # Lazy import keeps the resolver decoupled from
    # niche_credentials at module-load time (test-mocking pattern
    # documented in OPERATOR-PLAYBOOK.md).
    try:
        from genlab_core.publishing.niche_credentials import resolve_meta_credentials
    except ImportError:
        return None

    meta_creds = resolve_meta_credentials(niche_id) or {}
    access_token = meta_creds.get("ig_access_token", "")
    if not access_token:
        logger.debug(
            "[ig_shortcode] no IG access token for niche=%s, can't resolve %s",
            niche_id,
            media_id,
        )
        return None

    try:
        resp = requests.get(
            f"{_GRAPH_BASE_URL}/{media_id}",
            params={"fields": "permalink", "access_token": access_token},
            timeout=_HTTP_TIMEOUT_S,
        )
    except Exception as exc:
        logger.debug(
            "[ig_shortcode] HTTP error for niche=%s media=%s: %s",
            niche_id,
            media_id,
            exc,
        )
        return None

    if resp.status_code != 200:
        logger.debug(
            "[ig_shortcode] Graph API non-200 for niche=%s media=%s: HTTP %d %s",
            niche_id,
            media_id,
            resp.status_code,
            resp.text[:200],
        )
        return None

    try:
        permalink = resp.json().get("permalink", "")
    except ValueError:
        logger.debug(
            "[ig_shortcode] Graph API returned non-JSON for niche=%s media=%s",
            niche_id,
            media_id,
        )
        return None

    shortcode = _extract_shortcode_from_permalink(permalink)
    if shortcode is None:
        logger.debug(
            "[ig_shortcode] couldn't extract shortcode from permalink=%r",
            permalink,
        )
        return None

    _CACHE[media_id] = shortcode
    return shortcode


def cache_size() -> int:
    """Test-only helper exposing cache size. Useful for asserting
    that cache-hit tests don't issue a second HTTP call."""
    return len(_CACHE)


def reset_cache_for_tests() -> None:
    """Clear the module-level cache. ONLY for tests."""
    _CACHE.clear()
