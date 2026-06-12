"""Instagram metrics fetcher (canonical implementation).

Uses ``graph.facebook.com`` (never ``graph.instagram.com`` — the EAA page
token model requires the FB-scoped surface; see CLAUDE.md security rules).
Per-niche credentials are resolved through :mod:`genlab_core.publishing.niche_credentials`,
the single chokepoint that prevents cross-channel token leakage.

Returns a :class:`PlatformMetrics` with the canonical keys
``views/likes/comments/shares/saves/reach/impressions/watch_time_ms/engagement``.

Replaces the script and metric-collector copies that drifted on shape
(``saved`` vs ``saves``, ``watch_time_minutes`` vs ``watch_time_ms``).
"""

from __future__ import annotations

import logging
from typing import Final

import requests

from genlab_core.platforms.meta_api import META_GRAPH_BASE_URL
from genlab_core.platforms.meta_metric_deprecation import (
    record_observation,
    warn_if_deprecated,
)

from .types import PlatformMetrics

logger = logging.getLogger(__name__)

_API_BASE: Final[str] = META_GRAPH_BASE_URL
_TIMEOUT_S: Final[int] = 15

# v22.0+ canonical IG Reels insights metrics. `views` (added Jan 2025)
# replaces the deprecated `plays` / `impressions` / `ig_reels_aggregated_all_plays_count`.
# `reach` is unique-accounts (NOT views — that conflation was the v22 fetcher bug
# this module replaces). `ig_reels_avg_watch_time` complements the existing
# `ig_reels_video_view_total_time`. Order in the request string is irrelevant
# but kept stable for log/grep readability.
_REELS_METRICS: Final[str] = (
    "views,reach,likes,comments,shares,saved,total_interactions,"
    "ig_reels_avg_watch_time,ig_reels_video_view_total_time"
)


def _resolve_media_id(shortcode_or_id: str, *, token: str, ig_user_id: str) -> str | None:
    """Convert an IG shortcode to a numeric media ID via the user's ``/media``
    endpoint.

    The publisher stores shortcodes (e.g. ``DWif8mKEjst``) but the Graph API
    insights endpoints require numeric media IDs (e.g. ``18054219725706846``).
    If the input is already numeric, returns it unchanged.
    """
    if shortcode_or_id.isdigit():
        return shortcode_or_id
    if not ig_user_id:
        return None

    try:
        resp = requests.get(
            f"{_API_BASE}/{ig_user_id}/media",
            params={
                "fields": "id,shortcode",
                "limit": 50,
                "access_token": token,
            },
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning(
            "[platforms.metrics.instagram] media resolve failed for %s: %s",
            shortcode_or_id,
            exc,
        )
        return None

    if resp.status_code != 200:
        return None

    for item in resp.json().get("data", []):
        if item.get("shortcode") == shortcode_or_id:
            return item.get("id")

    logger.debug(
        "[platforms.metrics.instagram] shortcode %r not in recent media",
        shortcode_or_id,
    )
    return None


def _resolve_credentials(niche_id: str) -> tuple[str, str]:
    """Return ``(ig_access_token, ig_user_id)`` for ``niche_id`` or ``("", "")``."""
    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    creds = resolve_meta_credentials(niche_id)
    return creds.get("ig_access_token", ""), creds.get("ig_user_id", "")


def fetch_instagram(
    post_id: str,
    *,
    token: str | None = None,
    ig_user_id: str | None = None,
    niche_id: str = "",
) -> PlatformMetrics | None:
    """Return a :class:`PlatformMetrics` for the given IG ``post_id``.

    ``post_id`` may be a shortcode or a numeric media ID; shortcodes are
    resolved via the user's recent-media list. Returns ``None`` on missing
    credentials, unresolvable shortcode, non-200, or network failure.

    Credentials are resolved from ``niche_id`` unless ``token`` is passed
    explicitly (for callers that already have them in hand).
    """
    if token is None or ig_user_id is None:
        resolved_token, resolved_user_id = _resolve_credentials(niche_id)
        token = token or resolved_token
        ig_user_id = ig_user_id or resolved_user_id

    if not token:
        logger.warning(
            "[platforms.metrics.instagram] no IG token for niche %r — analytics missing",
            niche_id,
        )
        return None

    media_id = _resolve_media_id(post_id, token=token, ig_user_id=ig_user_id or "")
    if not media_id:
        return None

    # Basic metrics: like_count + comments_count (Reels insights sometimes
    # under-report these; the basic endpoint is the source of truth.)
    try:
        basic_resp = requests.get(
            f"{_API_BASE}/{media_id}",
            params={"fields": "like_count,comments_count,media_type", "access_token": token},
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning("[platforms.metrics.instagram] basic fetch failed: %s", exc)
        return None

    if basic_resp.status_code != 200:
        logger.warning(
            "[platforms.metrics.instagram] basic %d: %s",
            basic_resp.status_code,
            basic_resp.text[:200],
        )
        return None
    basic = basic_resp.json()

    # Reels insights — best-effort. If this fails we still return what the
    # basic endpoint gave us.
    warn_if_deprecated(_REELS_METRICS, context=f"ig_reels:{niche_id}")

    insights: dict[str, int] = {}
    try:
        ins_resp = requests.get(
            f"{_API_BASE}/{media_id}/insights",
            params={"metric": _REELS_METRICS, "access_token": token},
            timeout=_TIMEOUT_S,
        )
        if ins_resp.status_code == 200:
            for item in ins_resp.json().get("data", []):
                name = item.get("name", "")
                values = item.get("values", [{}])
                v = int(values[0].get("value", 0) or 0) if values else 0
                insights[name] = v
                record_observation(name, v, scope="ig_reels")
        else:
            logger.warning(
                "[platforms.metrics.instagram] insights %d for %s: %s",
                ins_resp.status_code,
                media_id,
                ins_resp.text[:200],
            )
    except requests.RequestException as exc:
        logger.debug("[platforms.metrics.instagram] insights non-fatal: %s", exc)

    # v22.0+ canonical: `views` is the real play count; `reach` is unique-accounts
    # and was wrongly aliased as views in the pre-fix implementation. Fall back to
    # reach only when views is empty (very new posts) so legacy rows still see
    # something rather than 0.
    views = insights.get("views", 0)
    reach = insights.get("reach", 0)
    return PlatformMetrics(
        views=views or reach,
        reach=reach,
        impressions=reach,
        likes=insights.get("likes", int(basic.get("like_count") or 0)),
        comments=insights.get("comments", int(basic.get("comments_count") or 0)),
        shares=insights.get("shares", 0),
        saves=insights.get("saved", 0),
        watch_time_ms=insights.get("ig_reels_video_view_total_time", 0),
        engagement=insights.get(
            "total_interactions",
            insights.get("likes", 0)
            + insights.get("comments", 0)
            + insights.get("shares", 0)
            + insights.get("saved", 0),
        ),
    )
