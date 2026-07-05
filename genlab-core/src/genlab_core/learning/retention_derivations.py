"""Derive per-window retention proxies from raw platform metrics.

Platform Insights APIs expose various raw signals (plays, reach,
avg_view_duration, total_interactions, likes, saves). None of them
directly give us the per-second retention curve we'd need for exact
per-dimension reward attribution. This module computes proxies that
are monotonic in the right direction — good enough for bandit signal.

Six derived metrics, mapped to the transformation dimensions per the
architecture (see [[intelligent-transformation-architecture-2026-07-05]]):

| Derived metric        | Dimension consumers        | Source signals                         |
|-----------------------|----------------------------|----------------------------------------|
| ``hold_3s``           | hook_framing, intro_animation | avg_view_duration >= 3s → 1.0         |
| ``hold_15s``          | music_mood, caption_pacing | avg_view_duration >= 15s → 1.0        |
| ``hold_avg_3s_15s``   | caption_style, highlight_moment | mean of hold_3s + hold_15s       |
| ``completion``        | pan_zoom, outro_cta, music_visual_sync | avg_view_duration / duration  |
| ``sends_per_reach``   | (composite scalar) | (total_interactions - likes - comments - saves) / reach |
| ``saved_rate``        | audio_ducking              | saved / reach                          |
| ``engagement_quality`` | caption_emphasis_color    | (likes + 2*comments) / reach           |

Every derived metric is clamped to [0, 1] for direct use as bandit
posterior update value.

Fallback semantics: derivations that lack the necessary raw signals
return ``None`` — the caller (transformation_reward_router) treats
None as "use the scalar composite reward" so learning still happens,
just without per-dim discrimination for that dimension.

Related sprint context:
- PR 5 (#669) — router that consumes these derivations
- Fetcher extensions in this PR make ``total_interactions`` available
  in the Instagram metrics dict for the sends_per_reach derivation
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _clamp01(x: float | int | None) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, v))


def _safe_div(num: float | int | None, den: float | int | None) -> float | None:
    """Return num/den or None if den is missing/zero/invalid."""
    if num is None or den is None:
        return None
    try:
        d = float(den)
    except (TypeError, ValueError):
        return None
    if d <= 0:
        return None
    try:
        return float(num) / d
    except (TypeError, ValueError):
        return None


def _derive_hold(avg_watch_seconds: float | None, threshold_s: float) -> float | None:
    """Proxy: cumulative hold rate past ``threshold_s``.

    We can't observe true past-Ns dropoff directly from most platforms.
    Approximation: if average watch time equals or exceeds the
    threshold, treat as 100% held; below the threshold, linearly
    interpolate (a viewer who averaged 1.5s cannot have been past 3s
    for even 50% of impressions).

    This underestimates in the tail — an average of 5s can mean
    "80% held 3s, 20% held 0s" (giving true hold_3s ≈ 0.80) rather
    than the proxy's clamped 1.0. That underestimation is acceptable
    because the bandit only needs monotonic-in-quality signal, not
    absolute correctness.
    """
    if avg_watch_seconds is None:
        return None
    try:
        v = float(avg_watch_seconds)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return 0.0
    return min(1.0, v / threshold_s)


def _extract_avg_watch_seconds(metrics: dict[str, Any], platform: str) -> float | None:
    """Extract average watch duration in SECONDS regardless of platform units.

    Instagram: ``ig_reels_avg_watch_time`` is in milliseconds (from
    fetcher line 138 aliased to ``avg_watch_time``).

    YouTube: ``avg_view_duration`` is in seconds (from Analytics v2).

    Facebook: ``minutes_viewed`` / plays gives us the average in
    minutes; convert to seconds. Falls back to None if plays is 0.
    """
    if platform == "instagram":
        v = metrics.get("avg_watch_time")
        if v is None:
            return None
        try:
            return float(v) / 1000.0  # ms → s
        except (TypeError, ValueError):
            return None
    if platform in ("youtube", "youtube_shorts"):
        v = metrics.get("avg_view_duration")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    if platform == "facebook":
        minutes = metrics.get("minutes_viewed")
        plays = metrics.get("plays") or metrics.get("views")
        if minutes is None or not plays:
            return None
        try:
            return (float(minutes) * 60.0) / float(plays)
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return None


def derive_retention_metrics(
    metrics: dict[str, Any],
    platform: str,
    *,
    video_duration_s: float | None = None,
) -> dict[str, float | None]:
    """Compute per-dimension retention proxies from raw platform metrics.

    Args:
        metrics: raw platform metrics dict returned by fetchers.
        platform: 'instagram' / 'youtube' / 'facebook' / etc. Determines
            which fields are available + unit conversions.
        video_duration_s: total video length for completion rate. When
            None, ``completion`` is derived as ``hold_15s`` fallback
            (rough proxy: reels ≥15s are treated as "completed" if
            avg watch ≥ 15s).

    Returns:
        Dict with all 7 derived metric keys. Values may be None if the
        underlying signals aren't present. Router treats None as
        "fall back to scalar reward for this dim."
    """
    avg_watch_s = _extract_avg_watch_seconds(metrics, platform)

    hold_3s = _derive_hold(avg_watch_s, 3.0)
    hold_15s = _derive_hold(avg_watch_s, 15.0)

    if hold_3s is None and hold_15s is None:
        hold_avg_3s_15s: float | None = None
    else:
        # Blend the two, treating a missing side as equal to the other
        # (rather than 0, which would falsely penalise).
        parts = [v for v in (hold_3s, hold_15s) if v is not None]
        hold_avg_3s_15s = sum(parts) / len(parts) if parts else None

    if video_duration_s is not None and video_duration_s > 0 and avg_watch_s is not None:
        completion = _clamp01(avg_watch_s / video_duration_s)
    else:
        # Fall back to hold_15s as a rough completion proxy for
        # standard-length reels (most niches target 15-30s).
        completion = hold_15s

    # Engagement-based derivations from raw counts.
    reach = metrics.get("reach") or metrics.get("impressions")
    likes = metrics.get("likes")
    comments = metrics.get("comments")
    saves = metrics.get("saves") or metrics.get("saved")
    total_interactions = metrics.get("total_interactions")

    # sends_per_reach proxy: 2026 IG algorithm weights DM shares 3-5x
    # over likes. Meta doesn't expose sends directly on Insights, but
    # ``total_interactions - (likes + comments + saves)`` approximates
    # shares + follows + profile visits. It's noisy but monotonic —
    # good enough to give the bandit a signal that's better-aligned
    # with what IG actually rewards.
    if total_interactions is not None and reach:
        residual = float(total_interactions)
        for f in (likes, comments, saves):
            if f is not None:
                try:
                    residual -= float(f)
                except (TypeError, ValueError):
                    pass
        residual = max(0.0, residual)
        sends_per_reach = _clamp01(residual / float(reach))
    else:
        sends_per_reach = None

    saved_rate = _clamp01(_safe_div(saves, reach))

    # engagement_quality: weighted likes+comments per reach. Comments
    # weighted 2x since they're higher-effort signals.
    if reach and (likes is not None or comments is not None):
        raw = 0.0
        if likes is not None:
            try:
                raw += float(likes)
            except (TypeError, ValueError):
                pass
        if comments is not None:
            try:
                raw += 2.0 * float(comments)
            except (TypeError, ValueError):
                pass
        engagement_quality = _clamp01(raw / float(reach))
    else:
        engagement_quality = None

    return {
        "hold_3s": hold_3s,
        "hold_15s": hold_15s,
        "hold_avg_3s_15s": hold_avg_3s_15s,
        "completion": completion,
        "sends_per_reach": sends_per_reach,
        "saved_rate": saved_rate,
        "engagement_quality": engagement_quality,
    }


__all__ = ["derive_retention_metrics"]
