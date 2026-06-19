"""Instagram metric fetchers — extracted from learning/metric_collector.py.

Two related fetchers per IG post:

* ``_fetch_instagram`` — the standard post-publish snapshot. Cascades through
  3 metric sets (Reels-compatible first, then without ``plays``, then minimal
  ``impressions,reach``) because the Graph API ``/insights`` endpoint rejects
  different fields for different post types (Reel vs photo vs carousel) and
  the operator can't easily predict which set will succeed.

* ``_fetch_instagram_reels_6h`` — Reels-specific 6h skip-rate signal. Uses
  ``ig_reels_avg_watch_time`` + ``ig_reels_video_view_total_time`` + ``plays``.
  Called from ``fetch_platform_metrics`` at the 6h window only (other windows
  use the standard fetcher).

Reward-shape specialisation (see metric_collector.py module docstring):
``_fetch_instagram`` deliberately OMITS unobservable fields (``dm_send_rate``,
``skip_rate``) from its return dict rather than stubbing 0.0 — that lets
``RewardShaper.compute_reward`` redistribute their weight instead of pinning
to a fake zero.

Migration note (P5a phase 2, 2026-06-19): second module in the per-platform
split (after metrics/youtube.py). Backward compatibility via re-export shim
in metric_collector.py.
"""

from __future__ import annotations

import logging
from typing import Any

from genlab_core.platforms.meta_api import META_GRAPH_BASE_URL

logger = logging.getLogger(__name__)


def _fetch_instagram(post_id: str, niche_id: str = "") -> dict:
    """Fetch Instagram metrics — tries Reels metrics first, falls back to standard.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["instagram"]``:
    ``views, saves, dm_send_rate, shares, skip_rate``.
    Graph API ``saved`` is returned as ``saves``. ``dm_send_rate`` and
    ``skip_rate`` aren't directly available from the basic insights
    endpoints; stubbed as 0.
    """
    import requests

    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    token = resolve_meta_credentials(niche_id).get("ig_access_token", "")
    if not token:
        return {}

    from genlab_core.platforms.meta_metric_deprecation import (
        record_observation,
        warn_if_deprecated,
    )

    # Try Reels-compatible metrics first (Break 3 fix)
    for metric_set in [
        "plays,reach,likes,comments,shares,saved",
        "reach,saved,comments,shares,likes",  # without 'plays' (some posts reject it)
        "impressions,reach",  # minimal fallback
    ]:
        warn_if_deprecated(metric_set, context="ig_basic")
        try:
            resp = requests.get(
                f"{META_GRAPH_BASE_URL}/{post_id}/insights",
                params={"metric": metric_set, "access_token": token},
                timeout=15,
            )
            if resp.status_code == 400:
                continue  # try next metric set
            resp.raise_for_status()
            metrics: dict[str, Any] = {}
            for item in resp.json().get("data", []):
                name = item.get("name", "")
                vals = item.get("values", [{}])
                val = vals[0].get("value", 0) if vals else 0
                # Observability hook (R-44 part 2): record every parsed
                # value so a slow zero-out across many posts surfaces
                # as a loud ERROR after `_ZERO_OUT_THRESHOLD` hits.
                record_observation(name, val, scope="ig_basic")
                if name == "plays":
                    metrics["views"] = val
                elif name == "impressions":
                    metrics.setdefault("views", val)
                elif name == "saved":
                    metrics["saves"] = val
                elif name in ("reach", "likes", "comments", "shares"):
                    metrics[name] = val
            # dm_send_rate and skip_rate aren't directly available from the
            # basic insights endpoints. Leaving them OUT of the dict (rather
            # than stubbing 0.0) lets compute_reward redistribute their
            # weight to metrics we actually observe — otherwise their 0.3 +
            # -0.05 weight share becomes a permanent dead zone in the IG
            # reward signal.
            return metrics
        except Exception:
            continue

    logger.warning("[metric_collector] All IG metric sets failed for %s", post_id)
    return {}


def _fetch_instagram_reels_6h(post_id: str, niche_id: str = "") -> dict:
    """IG Reels-specific metrics for early 6h skip-rate signal."""
    import requests

    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    token = resolve_meta_credentials(niche_id).get("ig_access_token", "")
    if not token:
        return {}
    from genlab_core.platforms.meta_metric_deprecation import (
        record_observation,
        warn_if_deprecated,
    )

    metric_set = "ig_reels_avg_watch_time,ig_reels_video_view_total_time,plays"
    warn_if_deprecated(metric_set, context="ig_reels_6h")
    resp = requests.get(
        f"{META_GRAPH_BASE_URL}/{post_id}/insights",
        params={
            "metric": metric_set,
            "access_token": token,
        },
        timeout=15,
    )
    resp.raise_for_status()
    metrics: dict[str, Any] = {}
    for item in resp.json().get("data", []):
        name = item.get("name", "")
        vals = item.get("values", [{}])
        val = vals[0].get("value", 0) if vals else 0
        record_observation(name, val, scope="ig_reels_6h")
        if name == "ig_reels_avg_watch_time":
            metrics["avg_watch_time"] = val
        elif name == "ig_reels_video_view_total_time":
            metrics["total_watch_time"] = val
        elif name == "plays":
            metrics["views"] = val
    return metrics


__all__ = ["_fetch_instagram", "_fetch_instagram_reels_6h"]
