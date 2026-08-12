"""Facebook metric fetchers — extracted from learning/metric_collector.py.

Three coordinated fetchers for the Facebook reward shape. GenLab posts only
Reels to FB, but Reels metrics are scattered across three Graph API surfaces:

* **/{post_id}/insights** (page-post insights) — impressions, reach,
  video views, avg watch time. Often 400s on Reel video IDs (the
  endpoint was designed for traditional page posts; Meta has not
  deprecated it for Reels, only made it unreliable).

* **/{post_id}?fields=likes.summary,comments.summary,views,length**
  (video object) — direct counts that page-post /insights doesn't expose
  for video IDs. Always works when the token has page-read scope.

* **/{post_id}/video_insights** with the Reels-specific metric set —
  `post_video_view_time`, `post_video_avg_time_watched`,
  `post_video_social_actions` (the ONLY surface that exposes shares for
  Reels). Without this layer, FB rewards collapse to just reach (~10%
  of the full BASE_WEIGHTS magnitude — meaningful signal loss).

``_fetch_facebook`` orchestrates the three calls + layering. Each soft-fails
to the previous layer's stub so partial data still moves the bandit.

Reward-shape specialisation (see metric_collector.py docstring): aligned
with ``RewardShaper.BASE_WEIGHTS["facebook"]`` — minutes_viewed, shares,
completion_rate, reach.

Migration note (P5a phase 3, 2026-06-19): third per-platform module in the
metric_collector split. Backward-compat via re-export shim in
metric_collector.py. The 3 helpers move together because they form a
coherent unit — they all share the same token + endpoint base.
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


def _fetch_facebook_reel_insights(reel_id: str, token: str) -> dict:
    """Pull Reel-specific metrics via /{reel_id}/video_insights.

    Three Reels metrics that the page-post insights endpoint never
    returned (and that the /{video_id}?fields= query can't reach):
      * post_video_view_time — total ms watched across all viewers
      * post_video_avg_time_watched — avg ms per play
      * post_video_social_actions — dict like {"COMMENT": N, "SHARE": N,
        "REACTION": N} of lifetime engagement actions

    Returns a dict with the parsed values; empty dict on failure.
    Used to compute completion_rate (avg_time / length) and recover
    the shares signal that's not exposed on the video object directly.
    """
    metrics_param = "post_video_view_time,post_video_avg_time_watched,post_video_social_actions"
    try:
        resp = _META_SESSION.get(
            f"{META_GRAPH_BASE_URL}/{reel_id}/video_insights",
            params={"metric": metrics_param, "access_token": token},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(
                "[metric_collector] fb reel insights soft-fail %d for %s",
                resp.status_code,
                reel_id,
            )
            return {}
        body = resp.json()
    except Exception as exc:
        # WARNING (not DEBUG) — silent failure here becomes a synthetic
        # zero reward for the bandit. Same class-of-bug as YT #578.
        logger.warning(
            "[metric_collector] fb reel insights fetch failed for %s: %s",
            reel_id,
            exc,
        )
        return {}

    out: dict[str, Any] = {}
    for item in body.get("data", []):
        name = item.get("name", "")
        vals = item.get("values", [{}])
        val = vals[0].get("value", 0) if vals else 0
        if name == "post_video_view_time":
            out["total_view_time_ms"] = float(val)
        elif name == "post_video_avg_time_watched":
            out["avg_view_time_ms"] = float(val)
        elif name == "post_video_social_actions":
            # val is a dict keyed by action type: COMMENT, SHARE, REACTION, ...
            if isinstance(val, dict):
                out["shares"] = int(val.get("SHARE", 0))
                out["reactions"] = int(val.get("REACTION", 0))
                # COMMENT here matches comments.summary on the video object;
                # keep both for cross-validation but prefer the explicit one.
                out["social_comments"] = int(val.get("COMMENT", 0))
    return out


def _fetch_facebook_video_object(post_id: str, token: str) -> dict:
    """Fetch views/likes/comments/length directly from the FB video object.

    GenLab publishes only Reels, and the stored ``platform_post_id`` is a
    video object ID (e.g. ``1579623663118068``), not a page-post story ID.
    The legacy page-post ``/insights`` endpoint 400s on video IDs; the
    video object itself exposes the engagement counts directly.

    Returns ``{"views": int, "likes": int, "comments": int,
    "video_length_s": float}`` or empty dict on failure.

    NOTE: ``shares`` is not exposed on the video object for Reels.  The
    page-level Reels Insights endpoint surfaces it under a different
    auth surface — wiring that is a separate task.
    """
    try:
        resp = _META_SESSION.get(
            f"{META_GRAPH_BASE_URL}/{post_id}",
            params={
                "fields": "likes.summary(true).limit(0),comments.summary(true).limit(0),views,length",
                "access_token": token,
            },
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(
                "[metric_collector] fb video object soft-fail %d for %s",
                resp.status_code,
                post_id,
            )
            return {}
        body = resp.json()
    except Exception as exc:
        # WARNING (not DEBUG) — synthetic-zero risk. See reel insights above.
        logger.warning("[metric_collector] fb video object fetch failed for %s: %s", post_id, exc)
        return {}

    likes_block = body.get("likes") or {}
    comments_block = body.get("comments") or {}
    return {
        "views": int(body.get("views", 0)),
        "likes": int((likes_block.get("summary") or {}).get("total_count", 0)),
        "comments": int((comments_block.get("summary") or {}).get("total_count", 0)),
        "video_length_s": float(body.get("length", 0)),
    }


def _fetch_facebook(post_id: str, niche_id: str = "") -> dict:
    """Fetch Facebook post insights + shares + completion_rate.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["facebook"]``:
    ``minutes_viewed, shares, completion_rate, reach``.

    Three Graph API round trips: /insights, /{post}?fields=shares,
    /{post}/attachments → /{video_id}?fields=length. Each layer
    soft-fails to the existing stub zero rather than crashing the
    fetch — keeps the engagement bandit moving on partial data.
    """
    from genlab_core.publishing.niche_credentials import resolve_fb_credentials

    token, _page_id = resolve_fb_credentials(niche_id)
    if not token:
        return {}

    metrics: dict[str, Any] = {}
    impressions = 0
    reach = 0
    video_views = 0
    avg_watch_time_ms = 0.0

    from genlab_core.platforms.meta_metric_deprecation import (
        record_observation,
        warn_if_deprecated,
    )

    # /insights — Meta deprecates metrics aggressively; if the call 400s we
    # still want shares + completion_rate to populate from the post object.
    #
    # PR #518 (2026-06-24): added fb_reels_total_plays alongside
    # post_video_views. Per the deprecation registry, post_video_views
    # is RETIRED in v23.0 for FB Reels (replaced by
    # fb_reels_total_plays). We're on v22 today, so post_video_views
    # still works — but the moment Meta cuts over, the metric silently
    # returns 0 and our reward signal stalls. Requesting BOTH means:
    #   - v22: post_video_views returns; fb_reels_total_plays may also
    #     return for Reels posts. Parsing prefers fb_reels_total_plays
    #     when present so the migration is gradual.
    #   - v23+: post_video_views returns 0 / nothing for Reels;
    #     fb_reels_total_plays is the only signal. We seamlessly handle
    #     the cutover.
    # If fb_reels_total_plays isn't valid for the account's posts
    # (non-Reels videos), Meta returns it with value 0 — we fall back
    # to post_video_views which is the right semantic for non-Reels.
    fb_feed_metrics = (
        "post_impressions,post_impressions_unique,"
        "post_engaged_users,post_video_views,fb_reels_total_plays,"
        "post_video_avg_time_watched"
    )
    warn_if_deprecated(fb_feed_metrics, context="fb_feed_insights")
    reels_total_plays: int | None = None  # tracked so we can prefer it
    try:
        resp = _META_SESSION.get(
            f"{META_GRAPH_BASE_URL}/{post_id}/insights",
            params={
                "metric": fb_feed_metrics,
                "access_token": token,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            for item in resp.json().get("data", []):
                name = item.get("name", "")
                vals = item.get("values", [{}])
                val = vals[0].get("value", 0) if vals else 0
                record_observation(name, val, scope="fb_feed_insights")
                if name == "post_impressions":
                    impressions = int(val)
                elif name == "post_impressions_unique":
                    reach = int(val)
                elif name == "post_engaged_users":
                    metrics["engaged_users"] = val
                elif name == "post_video_views":
                    video_views = int(val)
                    metrics["video_views"] = val
                elif name == "fb_reels_total_plays":
                    # PR #518: v23 replacement for post_video_views.
                    # Only meaningful when > 0 (Reels post); for
                    # non-Reels posts Meta returns 0 here.
                    if val:
                        reels_total_plays = int(val)
                elif name == "post_video_avg_time_watched":
                    avg_watch_time_ms = float(val)
                    metrics["avg_watch_time"] = val
        else:
            logger.debug(
                "[metric_collector] fb insights soft-fail %d for %s",
                resp.status_code,
                post_id,
            )
    except Exception as exc:
        # WARNING (not DEBUG) — insights failure produces synthetic-zero
        # metrics{"impressions"/"reach"/"minutes_viewed"} → reward_shaper
        # trains bandit on false-negative. Same class-of-bug as YT #578.
        logger.warning("[metric_collector] fb insights fetch failed for %s: %s", post_id, exc)

    # PR #518: prefer fb_reels_total_plays over post_video_views when
    # the post IS a Reel. Migration-friendly: works on both v22 (both
    # metrics return) and v23+ (only fb_reels_total_plays).
    if reels_total_plays is not None:
        video_views = reels_total_plays
        metrics["video_views"] = reels_total_plays

    metrics["impressions"] = impressions
    metrics["reach"] = reach or impressions  # fall back to impressions if reach unavailable
    # avg_watch_time is in milliseconds from Graph API; convert to minutes
    metrics["minutes_viewed"] = round((video_views * avg_watch_time_ms) / 60_000.0, 2)

    # Reel-era fallback: query the video object directly for real
    # engagement counts when /insights returned nothing.  GenLab posts
    # are video Reels; their stored ID is a video object ID and the
    # page-post insights endpoint 400s on those IDs.
    video_obj = _fetch_facebook_video_object(post_id, token)
    if video_obj:
        # Don't overwrite non-zero insights data with video-object data:
        # prefer insights when available (more granular), fall back to
        # video object when insights returned nothing.
        if metrics["impressions"] == 0:
            metrics["impressions"] = video_obj.get("views", 0)
            metrics["reach"] = video_obj.get("views", 0)
        if "video_views" not in metrics:
            metrics["video_views"] = video_obj.get("views", 0)
        metrics["likes"] = video_obj.get("likes", 0)
        metrics["comments"] = video_obj.get("comments", 0)
        if video_obj.get("video_length_s", 0) > 0:
            metrics["video_length_s"] = video_obj["video_length_s"]

    # Reel-specific insights — shares, total watch time, avg watch time.
    # These come from a third endpoint (/video_insights with reel-specific
    # metric set), separate from the page-post /insights and the video
    # object query.  Without these the FB reward computed only from reach
    # (~10% of full BASE_WEIGHTS magnitude).
    reel_insights = _fetch_facebook_reel_insights(post_id, token)
    if reel_insights:
        metrics["shares"] = reel_insights.get("shares", 0)
        # minutes_viewed: prefer total_view_time over the (views × avg)
        # estimate since it's the authoritative total.
        if reel_insights.get("total_view_time_ms", 0) > 0:
            metrics["minutes_viewed"] = round(
                reel_insights["total_view_time_ms"] / 60_000.0,
                2,
            )
        # completion_rate: avg_watch_ms / (length_s × 1000), clamped [0,1].
        avg_ms = reel_insights.get("avg_view_time_ms", 0.0)
        length_s = metrics.get("video_length_s", 0)
        if avg_ms > 0 and length_s > 0:
            completion = min(1.0, (avg_ms / 1000.0) / length_s)
            metrics["completion_rate"] = round(completion, 4)
        # Surface reactions as a bonus signal (not in RewardShaper yet).
        if "reactions" in reel_insights:
            metrics["reactions"] = reel_insights["reactions"]

    metrics.setdefault("shares", 0)
    # 2026-08-12: removed `metrics.setdefault("completion_rate", 0.0)`.
    # Prior behavior stamped 0.0 whenever the reel-insights endpoint
    # didn't return avg_view_time_ms → reward-shaper trained bandit on
    # a synthetic zero (facebook has completion_rate weight 0.20; ~5%
    # reward penalty per post from this alone). Better: leave the
    # metric ABSENT so redistribution scales up the observed metrics
    # rather than compute reward on false-negative data.

    # 2026-08-12: compute VTR (view-through-rate) as a derived signal.
    # `reach` = unique users the algorithm showed the post to.
    # `video_views` = plays. VTR measures "of the people who saw it,
    # how many watched?" — content-quality signal orthogonal to raw
    # reach. Reward-shaper picks this up via the new `vtr` weight in
    # BASE_WEIGHTS["facebook"]. Same logic as instagram sibling.
    reach_val = int(metrics.get("reach", 0) or 0)
    views_val = int(metrics.get("video_views", 0) or 0)
    if reach_val > 0 and views_val > 0:
        metrics["vtr"] = min(1.0, views_val / reach_val)

    # PR #523 (2026-06-24): v23 synthetic fallback for engaged_users.
    # post_engaged_users is deprecated for new posts in v22.0 and will
    # silently return 0 once Meta cuts over. Per the registry's
    # replacement hint ("compute engagement from post_reactions_* +
    # post_comments + post_shares instead"), synthesize the field from
    # the components we already collect when the direct fetch yields 0.
    #
    # Migration-friendly (same shape as PR #518's fb_reels_total_plays
    # fallback):
    #   * v22 with post_engaged_users still returning: keep the direct
    #     value; the synthetic computes the same number from parts so
    #     comparing the two helps the operator audit drift.
    #   * v22 with post_engaged_users returning 0 (already happens on
    #     newer posts per the deprecation note): synthetic kicks in
    #     and we keep a usable signal.
    #   * v23+ where post_engaged_users always returns 0: synthetic
    #     is the only path.
    #
    # We only synthesize when (a) the direct value is 0/missing AND
    # (b) at least one of the components is non-zero — otherwise the
    # synthetic 0 is indistinguishable from the missing-data 0 and
    # adds nothing.
    # Only the synthetic path is tagged so existing v22 dict shapes are
    # preserved bit-for-bit. Absence of the tag → direct value (the
    # historical default); presence of "synthetic_v23" → derived from
    # reactions+comments+shares so the operator can audit migration.
    direct_engaged = metrics.get("engaged_users", 0)
    if not direct_engaged:
        reactions = int(metrics.get("reactions", 0) or 0)
        comments = int(metrics.get("comments", 0) or 0)
        shares = int(metrics.get("shares", 0) or 0)
        components_sum = reactions + comments + shares
        if components_sum > 0:
            metrics["engaged_users"] = components_sum
            metrics["engaged_users_source"] = "synthetic_v23"
            logger.debug(
                "[fb] synthesized engaged_users=%d from reactions=%d "
                "+ comments=%d + shares=%d (post_engaged_users was %s)",
                components_sum,
                reactions,
                comments,
                shares,
                "0" if direct_engaged == 0 else "missing",
            )

    return metrics


__all__ = [
    "_fetch_facebook",
    "_fetch_facebook_reel_insights",
    "_fetch_facebook_video_object",
]
