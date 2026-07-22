"""Facebook metrics fetcher (canonical implementation).

Facebook Reels and the legacy crosspost surface require different insights
endpoints: ``/{post_id}/insights`` returns HTTP 400 on Reels, so this caller
uses ``/{post_id}/video_insights`` which Meta uses for the Reels family.

v23.0 / June 2026 update — Meta's `/video_insights` requires the `metric=`
query parameter (without it, the endpoint returns error 3001/subcode 1504028
or an empty data array, silently). Pre-v23 the legacy Reels metric name
`post_video_views` was also retired in favor of `fb_reels_total_plays` and
`blue_reels_play_count` (initial-play count). Comments + shares no longer
have individual `/video_insights` metrics; they live under the combined
`post_video_social_actions` (counts both) or require a sibling
`?fields=comments.summary(true),shares` call on the post node.

Returns a :class:`PlatformMetrics` with ``views/likes/comments/shares/reach/watch_time_ms/engagement``.
"""

from __future__ import annotations

import logging
from typing import Any, Final

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

# v23.0+ canonical Reels metrics. `fb_reels_total_plays` is the primary
# views metric (counts replays); `blue_reels_play_count` is initial-plays
# only and serves as a fallback when the former is empty.
# `post_video_social_actions` lumps comments + shares together — for a
# breakdown we hit the post node separately.
#
# 2026-07-22: REMOVED `post_impressions_unique`. Meta v22 API rejects it
# on the `/video_insights` endpoint with HTTP 400 code 100 "The value must
# be a valid insights metric" — and because Meta rejects the ENTIRE batch
# when any one metric is invalid, the whole insights call was returning 400
# → PlatformMetrics=None → publishing_analytics stayed at SUCCESS forever
# for every FB row across all 5 niches (0/8 SUCCESS→INSIGHTS transitions
# in the 7-day pre-fix window). Verified via per-metric probe against
# facebook:1726457395148308 (ai_creators, 2026-07-22 fire):
#     fb_reels_total_plays            -> 200
#     blue_reels_play_count           -> 200
#     post_impressions_unique         -> 400 (#100) invalid metric
#     post_video_likes_by_reaction_type -> 200
#     post_video_social_actions       -> 200
#     post_video_view_time            -> 200
#     post_video_avg_time_watched     -> 200
# Downstream fallback at line 207 `reach = reach_unique or views` means
# reach silently degrades to views (conservative, correct choice) rather
# than crashing anything. If Meta re-exposes a Reels-reach metric under a
# new name, add it here.
_REELS_INSIGHTS_METRICS: Final[str] = ",".join(
    [
        "fb_reels_total_plays",
        "blue_reels_play_count",
        "post_video_likes_by_reaction_type",
        "post_video_social_actions",
        "post_video_view_time",
        "post_video_avg_time_watched",
    ]
)

# Sibling post-node fields to break out comments vs shares (the
# /video_insights endpoint can't separate them as of v23.0).
_POST_NODE_FIELDS: Final[str] = "comments.summary(true),shares,reactions.summary(true)"


def _resolve_credentials(niche_id: str) -> str:
    """Return the per-niche FB page access token or ``""``."""
    from genlab_core.publishing.niche_credentials import resolve_fb_credentials

    token, _page_id = resolve_fb_credentials(niche_id)
    return token


def _fetch_post_breakdown(post_id: str, token: str) -> tuple[int, int]:
    """Fetch comments + shares from the post node.

    Returns ``(comments, shares)``. Both 0 on any failure — this is a
    best-effort breakdown call; the primary insights call should have
    already given us combined `post_video_social_actions`.
    """
    try:
        resp = requests.get(
            f"{_API_BASE}/{post_id}",
            params={"fields": _POST_NODE_FIELDS, "access_token": token},
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning(
            "[platforms.metrics.facebook] post-node breakdown failed for "
            "post %s — comments+shares silently reported as 0 (reward "
            "pipeline sees no engagement for this post): %s",
            post_id,
            exc,
            exc_info=True,
        )
        return 0, 0

    if resp.status_code != 200:
        return 0, 0

    body = resp.json()
    comments_total = body.get("comments", {}).get("summary", {}).get("total_count", 0) or 0
    shares_count = body.get("shares", {}).get("count", 0) or 0
    return int(comments_total), int(shares_count)


def fetch_facebook(
    post_id: str,
    *,
    token: str | None = None,
    niche_id: str = "",
) -> PlatformMetrics | None:
    """Return a :class:`PlatformMetrics` for the given FB ``post_id``.

    ``post_id`` is the FB-native id (e.g. ``page_id_postnumber``). Returns
    ``None`` on missing token, non-200, or network failure.

    Credentials are resolved from ``niche_id`` unless ``token`` is passed
    explicitly.
    """
    if token is None:
        token = _resolve_credentials(niche_id)

    if not token:
        logger.warning(
            "[platforms.metrics.facebook] no FB token for niche %r — analytics missing",
            niche_id,
        )
        return None

    # Surface any registered deprecation hints in the request log so a
    # silent rename in a future Meta version trips an early warning.
    warn_if_deprecated(_REELS_INSIGHTS_METRICS, context=f"fb_reels:{niche_id}")

    try:
        resp = requests.get(
            f"{_API_BASE}/{post_id}/video_insights",
            params={
                "metric": _REELS_INSIGHTS_METRICS,
                "access_token": token,
            },
            timeout=_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        logger.warning("[platforms.metrics.facebook] request failed for %s: %s", post_id, exc)
        return None

    if resp.status_code != 200:
        logger.warning(
            "[platforms.metrics.facebook] video_insights %d for %s: %s",
            resp.status_code,
            post_id,
            resp.text[:200],
        )
        return None

    fb_reels_total_plays = 0
    blue_reels_play_count = 0
    reach_unique = 0
    watch_time_ms = 0
    avg_watch_time_ms = 0
    likes = 0
    social_actions_combined = 0

    for item in resp.json().get("data", []):
        name = item.get("name", "")
        values = item.get("values", [{}])
        val: Any = values[0].get("value", 0) if values else 0
        if name == "post_video_likes_by_reaction_type" and isinstance(val, dict):
            # Sum all reaction types (Like, Love, Wow, …) into the canonical
            # ``likes`` bucket.
            likes = sum(int(v or 0) for v in val.values())
            record_observation(name, likes, scope="fb_reels")
        elif name == "fb_reels_total_plays":
            fb_reels_total_plays = int(val or 0)
            record_observation(name, fb_reels_total_plays, scope="fb_reels")
        elif name == "blue_reels_play_count":
            blue_reels_play_count = int(val or 0)
            record_observation(name, blue_reels_play_count, scope="fb_reels")
        elif name == "post_impressions_unique":
            reach_unique = int(val or 0)
            record_observation(name, reach_unique, scope="fb_reels")
        elif name == "post_video_view_time":
            watch_time_ms = int(val or 0)
            record_observation(name, watch_time_ms, scope="fb_reels")
        elif name == "post_video_avg_time_watched":
            avg_watch_time_ms = int(val or 0)
            record_observation(name, avg_watch_time_ms, scope="fb_reels")
        elif name == "post_video_social_actions":
            social_actions_combined = int(val or 0)
            record_observation(name, social_actions_combined, scope="fb_reels")

    # Prefer total-plays (includes replays) as the canonical "views" number.
    # Fall back to initial-play count if the former is empty (rare but
    # possible on very new posts).
    views = fb_reels_total_plays or blue_reels_play_count

    # Separate comments / shares: best-effort via the post node. If that
    # call fails we keep the combined social-actions count in `shares`
    # so the reward signal isn't lost (a future audit can split it).
    comments, shares = _fetch_post_breakdown(post_id, token)
    if comments == 0 and shares == 0 and social_actions_combined > 0:
        # Fold the combined number into `shares` so downstream sums still
        # see engagement, even though we can't attribute it precisely.
        shares = social_actions_combined

    return PlatformMetrics(
        views=views,
        # FB Reels reach is the unique-impressions count when the metric
        # is present; otherwise we alias to views (the conservative choice).
        reach=reach_unique or views,
        likes=likes,
        comments=comments,
        shares=shares,
        watch_time_ms=watch_time_ms,
        engagement=likes + comments + shares,
    )
