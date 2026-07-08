"""YouTube metric fetcher — extracted from learning/metric_collector.py.

Two YouTube APIs are consulted per post:

* **Data API v3** (``/youtube/v3/videos``) — always-current viewCount,
  likeCount, commentCount. Uses OAuth refresh-token auth via the
  per-niche YouTube refresh token (NOT a Data API key — same OAuth
  flow that the publisher uses to upload).

* **Analytics API v2** (``/youtubeanalytics/v2/reports``) — aggregated
  avg_view_duration, subscribers_gained, minutes_viewed, shares. Uses
  a SEPARATE per-niche Analytics refresh token (each channel needs its
  own OAuth consent — manager relationships don't extend Analytics
  access). Soft-fails on 400/403 (typical at the 6h/24h windows when
  Analytics hasn't propagated yet) — snapshot fields stay authoritative.

Result shape aligns with ``RewardShaper.BASE_WEIGHTS["youtube"]``:
``views, avg_view_duration, subscriber_gained, like_rate, comment_rate``.

Migration note (P5a, 2026-06-19): moved out of metric_collector.py as the
first per-platform module in the metrics/ subpackage split. Module-level
caches (``_yt_token_cache``, ``_yt_analytics_token_cache``) moved with
the functions — they're internal implementation details, not public API.
``metric_collector.py`` re-exports the public symbols so existing imports
keep working unchanged.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)


# ── Module-level YouTube token caches ────────────────────────────────────────

# Data API access token cache (avoids re-refreshing on every metric call).
# Single-entry by design — the Data API call uses whichever niche's refresh
# token last hit the cache. Each per-niche metric collection call refreshes
# this cache if niche_id changes.
_yt_token_cache: dict[str, Any] = {"token": "", "niche": "", "ts": 0.0}
_YT_TOKEN_TTL = 2400.0  # 40 minutes (tokens last ~60 min)

# Per-niche Analytics token cache. Each entry is keyed by niche_id and stores
# the most recently minted access token + timestamp. YouTube Analytics requires
# per-channel tokens (manager-relationship parent accounts get 403 on managed
# channels' analytics — verified 2026-05-21), so the cache cannot be shared.
_yt_analytics_token_cache: dict[str, dict[str, Any]] = {}

# Per-niche-id set of analytics-warning-emitted flags. Operators get ONE
# warning per niche per process when the Analytics refresh token is missing
# (the per-call log would flood at the 6h/24h windows when the token isn't
# configured for that niche). Populated lazily inside ``_get_yt_analytics_access_token``.
_yt_analytics_warning_emitted: set[str] = set()


# ── Analytics v2 access token (per-niche, cached) ────────────────────────────


def _get_yt_analytics_access_token(niche_id: str = "") -> str:
    """Mint and cache an access token from the per-niche Analytics refresh token.

    Returns "" if no analytics refresh token is configured for the niche —
    _fetch_youtube_analytics_extras then short-circuits to {} and the
    snapshot fields remain authoritative.
    """
    import time as _time

    import requests

    from genlab_core.publishing.niche_credentials import (
        resolve_youtube_analytics_credentials,
    )

    creds = resolve_youtube_analytics_credentials(niche_id)
    cid = creds.get("client_id", "")
    csec = creds.get("client_secret", "")
    rt = creds.get("refresh_token", "")
    if not all([cid, csec, rt]):
        # Warn once per niche-id per process so operators can see WHICH
        # channels are missing tokens without flooding logs per-call.
        # Without the analytics refresh token the YT reward signal is
        # missing avg_view_duration + subscribers_gained + minutes_viewed
        # + shares — the most valuable engagement metrics for the
        # learning loop.
        if niche_id not in _yt_analytics_warning_emitted:
            logger.warning(
                "[metric_collector] YouTube Analytics v2 disabled for "
                "niche=%s: %s missing. YT rewards will use snapshot "
                "views/likes only — avg_view_duration/"
                "subscribers_gained/minutes_viewed/shares will be "
                "omitted.",
                niche_id or "<unset>",
                "refresh_token" if (cid and csec) else "client_id+secret+refresh_token",
            )
            _yt_analytics_warning_emitted.add(niche_id)
        return ""

    now = _time.monotonic()
    slot = _yt_analytics_token_cache.get(niche_id)
    if slot and slot.get("token") and (now - slot.get("ts", 0.0)) < _YT_TOKEN_TTL:
        return slot["token"]

    try:
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": cid,
                "client_secret": csec,
                "refresh_token": rt,
                "grant_type": "refresh_token",
            },
            timeout=15,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token", "")
        _yt_analytics_token_cache[niche_id] = {"token": token, "ts": now}
        return token
    except Exception as exc:
        logger.debug(
            "[metric_collector] yt analytics token refresh failed for niche=%s: %s",
            niche_id or "<unset>",
            exc,
        )
        return ""


# ── Analytics v2 extras fetch ────────────────────────────────────────────────


def _fetch_youtube_analytics_extras(
    video_id: str,
    channel_id: str,
    niche_id: str = "",
) -> dict:
    """Pull avg_view_duration + subscribers_gained from YouTube Analytics v2.

    Uses the per-niche Analytics refresh token (see
    resolve_youtube_analytics_credentials). Each channel needs its own
    OAuth consent — manager relationships do NOT extend Analytics access
    (verified 2026-05-21 with anarchistsid@gmail.com parent + brand
    accounts: 403 on managed channels, 200 on owned channels).

    Returns a dict with keys ``avg_view_duration``, ``subscriber_gained``,
    ``minutes_viewed``, ``shares``.  Empty dict if:
      * no analytics refresh token configured for the niche
      * channel_id missing (niche not provisioned)
      * 400/403 (scope missing, video too new, channel mismatch)
      * empty row set (data not yet aggregated)
      * any other exception
    """
    from datetime import timedelta

    import requests

    if not channel_id:
        return {}

    access_token = _get_yt_analytics_access_token(niche_id)
    if not access_token:
        return {}

    now = datetime.now(UTC)
    start_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
    end_date = now.strftime("%Y-%m-%d")

    try:
        resp = requests.get(
            "https://youtubeanalytics.googleapis.com/v2/reports",
            params={
                "ids": f"channel=={channel_id}",
                "startDate": start_date,
                "endDate": end_date,
                "metrics": ("estimatedMinutesWatched,averageViewDuration,subscribersGained,shares"),
                "filters": f"video=={video_id}",
                "dimensions": "video",
            },
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        # 403 = scope missing or not a channel manager; 400 = video too new.
        if resp.status_code in (400, 403):
            logger.debug(
                "[metric_collector] yt analytics soft-fail %d for %s",
                resp.status_code,
                video_id,
            )
            return {}
        resp.raise_for_status()
        body = resp.json()
        rows = body.get("rows", [])
        if not rows:
            return {}
        headers_meta = [col.get("name") for col in body.get("columnHeaders", [])]
        data = dict(zip(headers_meta, rows[0], strict=False))
        return {
            "minutes_viewed": float(data.get("estimatedMinutesWatched", 0)),
            "avg_view_duration": float(data.get("averageViewDuration", 0)),
            "subscriber_gained": int(data.get("subscribersGained", 0)),
            "shares": int(data.get("shares", 0)),
        }
    except Exception as exc:
        logger.debug("[metric_collector] yt analytics fetch failed for %s: %s", video_id, exc)
        return {}


# ── Public fetcher (Data API + Analytics layered) ────────────────────────────


def _fetch_youtube(post_id: str, niche_id: str = "") -> dict:
    """YouTube Data API v3 snapshot + Analytics v2 aggregates.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["youtube"]``:
    ``views, avg_view_duration, subscriber_gained, like_rate, comment_rate``.

    Data API gives the always-current viewCount/likeCount/commentCount.
    Analytics API fills in avg_view_duration + subscriber_gained from
    aggregated reports.  If Analytics hasn't propagated yet (typical at
    the 6h/24h windows), those keys stay at 0 and the engagement bandit
    learns from the snapshot signals only.
    """
    import time as _time

    import requests

    from genlab_core.publishing.niche_credentials import resolve_youtube_credentials

    creds = resolve_youtube_credentials(niche_id)
    client_id = creds.get("client_id", "")
    client_secret = creds.get("client_secret", "")
    refresh_token = creds.get("refresh_token", "")
    if not all([client_id, client_secret, refresh_token]):
        return {}

    # Reuse cached token if same niche and not expired
    now = _time.monotonic()
    if (
        _yt_token_cache["token"]
        and _yt_token_cache["niche"] == niche_id
        and (now - _yt_token_cache["ts"]) < _YT_TOKEN_TTL
    ):
        access_token = _yt_token_cache["token"]
    else:
        # Token refresh — wrapped so upstream 4xx/5xx or transport errors
        # never propagate to the orchestrator. Round-2 audit (2026-07-08)
        # found this unwrapped `raise_for_status()` was the single largest
        # source of missing YT reward writes: any 401 on token refresh or
        # transient network blip cascaded to orchestrator's bare
        # `except Exception` which returned `{}`, silently skipping
        # `reward_48h` compute. 82% of YT `pending_feedback` rows had
        # NULL `reward_48h`. See `audit-deep-dive-2026-07-08.md`.
        try:
            token_resp = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=15,
            )
            token_resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001 — never crash the reward pass
            logger.warning(
                "[metric_collector] YouTube token refresh failed for %s: %s",
                niche_id,
                exc,
            )
            return {}
        access_token = token_resp.json()["access_token"]
        _yt_token_cache["token"] = access_token
        _yt_token_cache["niche"] = niche_id
        _yt_token_cache["ts"] = now

    # Video statistics fetch — same hardening rationale as token refresh above.
    # A 404 on a deleted video, 403 on a private one, or transient 5xx must
    # NOT kill the reward pass; log + return {} so the orchestrator can move on.
    try:
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "statistics", "id": post_id},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — never crash the reward pass
        logger.warning(
            "[metric_collector] YouTube stats fetch failed for %s: %s",
            post_id,
            exc,
        )
        return {}
    items = resp.json().get("items", [])
    if not items:
        return {}

    stats = items[0].get("statistics", {})
    views = int(stats.get("viewCount", 0))
    likes = int(stats.get("likeCount", 0))
    comments = int(stats.get("commentCount", 0))
    like_rate = (likes / views) if views > 0 else 0.0
    comment_rate = (comments / views) if views > 0 else 0.0

    result: dict[str, Any] = {
        "views": views,
        "likes": likes,
        "comments": comments,
        "like_rate": round(like_rate, 4),
        "comment_rate": round(comment_rate, 4),
        # Stubs — will be overwritten by Analytics call below when data has
        # propagated (typically ≥48h post-publish).
        "avg_view_duration": 0.0,
        "subscriber_gained": 0,
    }

    # Layer Analytics extras on top. Uses the per-niche analytics refresh
    # token + per-niche {PREFIX}_YT_CHANNEL_ID for the channel== filter.
    # Each channel needs its own OAuth consent (manager relationships
    # don't extend Analytics API access — verified 2026-05-21). Empty
    # dict on early-window calls or scope/API failures keeps the stub
    # zeros.
    from genlab_core.publishing.niche_credentials import resolve_youtube_channel_id

    channel_id = resolve_youtube_channel_id(niche_id)
    extras = _fetch_youtube_analytics_extras(post_id, channel_id, niche_id)
    if extras:
        result["avg_view_duration"] = round(extras.get("avg_view_duration", 0.0), 2)
        result["subscriber_gained"] = extras.get("subscriber_gained", 0)
        result["minutes_viewed"] = extras.get("minutes_viewed", 0.0)
        result["shares"] = extras.get("shares", 0)

    return result


__all__ = [
    "_fetch_youtube",
    "_fetch_youtube_analytics_extras",
    "_get_yt_analytics_access_token",
]
