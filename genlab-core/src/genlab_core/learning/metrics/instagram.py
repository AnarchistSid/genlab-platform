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
from genlab_core.platforms.meta_http import get_shared_session as _get_shared_session

# 2026-07-22 anti-fingerprint extension: metric collectors call
# graph.facebook.com to fetch reel insights. Reusing the shared
# Meta HTTP session gives us (1) identified User-Agent header + (2)
# X-App-Usage capture via the response hook. Same rationale as
# platforms/facebook.py adopted in 8c02b266.
_META_SESSION = _get_shared_session()
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
    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    token = resolve_meta_credentials(niche_id).get("ig_access_token", "")
    if not token:
        return {}

    from genlab_core.platforms.meta_metric_deprecation import (
        record_observation,
        warn_if_deprecated,
    )

    # Try Reels-compatible metrics first (Break 3 fix)
    # 2026-07-05 (PR 6/16): added ``total_interactions`` to the primary
    # set for the sends_per_reach proxy consumed by
    # transformation_reward_router.compute_dimension_reward. Meta doesn't
    # expose direct sends/DM-shares on Insights; the proxy is
    # ``total_interactions - (likes + comments + saves)`` = residual
    # weight ≈ shares + follows + profile visits. Falls back cleanly to
    # the legacy metric sets when total_interactions isn't accepted.
    # 2026-08-11: Meta deprecated `plays` in Graph API v22 for IG media
    # (returns 400 Bad Request on the primary sets that include it).
    # `views` is the v22+ replacement — semantically the same for Reels
    # (total view count). Order sets by likelihood of success on the
    # majority of accounts: `views` first, then `plays` fallback for
    # legacy accounts / older post_ids that haven't transitioned.
    for metric_set in [
        "views,reach,likes,comments,shares,saved,total_interactions",
        "views,reach,likes,comments,shares,saved",
        "plays,reach,likes,comments,shares,saved,total_interactions",
        "plays,reach,likes,comments,shares,saved",
        "reach,saved,comments,shares,likes",  # neither plays nor views
        "impressions,reach",  # minimal fallback (impressions itself
                              # also deprecated per meta_metric_deprecation
                              # but still returns for some post types)
    ]:
        warn_if_deprecated(metric_set, context="ig_basic")
        try:
            resp = _META_SESSION.get(
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
                if name == "views":
                    # v22+ canonical view count for both Reels + static.
                    metrics["views"] = val
                elif name == "plays":
                    # Legacy field — kept as fallback for accounts that
                    # haven't transitioned. Use setdefault so `views`
                    # (which we prefer per Meta's deprecation notice)
                    # wins if both are returned.
                    metrics.setdefault("views", val)
                elif name == "impressions":
                    metrics.setdefault("views", val)
                elif name == "saved":
                    metrics["saves"] = val
                elif name == "total_interactions":
                    # 2026-07-05: consumed by retention_derivations for the
                    # sends_per_reach proxy. Preserve when present.
                    metrics["total_interactions"] = val
                elif name in ("reach", "likes", "comments", "shares"):
                    metrics[name] = val
            # dm_send_rate and skip_rate aren't directly available from the
            # basic insights endpoints. Leaving them OUT of the dict (rather
            # than stubbing 0.0) lets compute_reward redistribute their
            # weight to metrics we actually observe — otherwise their 0.3 +
            # -0.05 weight share becomes a permanent dead zone in the IG
            # reward signal.
            return metrics
        except Exception as exc:
            # Per-set failure gets a WARNING so a Meta metric deprecation
            # mid-fallback surfaces before the whole cascade exhausts.
            # Prior state (bare `continue`) silently absorbed permission,
            # deprecation, and network failures into the same "no data" tail.
            logger.warning(
                "[metric_collector] IG metric-set '%s' failed for %s: %s",
                metric_set,
                post_id,
                exc,
            )
            continue

    logger.warning("[metric_collector] All IG metric sets failed for %s", post_id)
    return {}


def _fetch_instagram_reels_6h(post_id: str, niche_id: str = "") -> dict:
    """IG Reels-specific metrics for early 6h skip-rate signal."""
    from genlab_core.publishing.niche_credentials import resolve_meta_credentials

    token = resolve_meta_credentials(niche_id).get("ig_access_token", "")
    if not token:
        return {}
    from genlab_core.platforms.meta_metric_deprecation import (
        record_observation,
        warn_if_deprecated,
    )

    # 2026-08-11: `plays` deprecated in v22 — was causing every IG
    # reels 6h fetch to 400 (verified via journal grep, several dozen
    # WARN entries per hour). Cascade sets analogous to _fetch_instagram
    # so a single API version rev doesn't kill the whole path.
    for metric_set in [
        "ig_reels_avg_watch_time,ig_reels_video_view_total_time,views",
        "ig_reels_avg_watch_time,ig_reels_video_view_total_time,plays",
        # Minimal fallback when Reels metrics + views/plays both fail
        # (e.g. non-Reels post or unavailable measurement).
        "ig_reels_avg_watch_time,ig_reels_video_view_total_time",
    ]:
        warn_if_deprecated(metric_set, context="ig_reels_6h")
        try:
            resp = _META_SESSION.get(
                f"{META_GRAPH_BASE_URL}/{post_id}/insights",
                params={
                    "metric": metric_set,
                    "access_token": token,
                },
                timeout=15,
            )
            if resp.status_code == 400:
                continue  # try next set
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[metric_collector] IG reels 6h metric-set %r failed for %s: %s",
                metric_set, post_id, exc,
            )
            continue

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
            elif name == "views":
                # v22+ canonical view count (post-deprecation of `plays`).
                metrics["views"] = val
            elif name == "plays":
                # Legacy fallback — `views` wins via setdefault.
                metrics.setdefault("views", val)
        return metrics

    logger.warning("[metric_collector] All IG reels 6h metric sets failed for %s", post_id)
    return {}


__all__ = ["_fetch_instagram", "_fetch_instagram_reels_6h"]
