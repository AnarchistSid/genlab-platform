"""Meta Graph API metric deprecation registry + helpers.

Audit ref: R-44 part 2 — "request these fields and will silently
zero out, starving the learning-loop reward inputs".

The audit's core risk wasn't "wrong metric names today"; it was
**silent failure tomorrow**. Meta deprecates insight metrics
quarterly: a previously-working ``post_impressions`` request keeps
returning 200 OK with an empty data array (or a 0 value), and
nothing in our pipeline notices until the bandit's reward signal
slowly drifts to zero.

This module provides the two observability primitives:

1. :data:`DEPRECATED_METRICS` — a static registry of metric names
   the Meta team has marked deprecated. Callers consult it via
   :func:`warn_if_deprecated` before constructing a request.
2. :func:`record_zero_response` — record one observation of a
   metric returning 0. Callers consult it AFTER each insights
   response. When the same metric returns 0 across N consecutive
   observations, an ERROR is logged (so a deprecation that wasn't
   in the static registry still surfaces loudly).

Both primitives are stateless / process-local for now (the silent-
zero counter lives in a dict here). A future v2 could persist
counts in Postgres so observability survives process restarts,
but the daily metric_collector cycle ensures any deprecation
shows up within a few runs even with process-local state.

Registry sources: Meta's quarterly deprecation notices at
https://developers.facebook.com/docs/graph-api/changelog.
Update this file when new deprecations are announced.

Last updated: 2026-06-10 (v22.0 baseline).
"""

from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Static deprecation registry
# ---------------------------------------------------------------------------

# Map of {metric_name: (deprecation_note, replacement_hint)}.
# A metric appears here once Meta's changelog announces deprecation,
# even if the field still returns data — the warning is the early
# signal that lets us migrate before silent zero-out.
#
# Note: ``replacement_hint`` is intentionally a free-form string,
# not a callable mapping. The replacement often isn't a 1:1 swap
# (different units, different aggregation window) — humans need
# to look at the destination metric before flipping caller code.
DEPRECATED_METRICS: Final[dict[str, tuple[str, str]]] = {
    # Instagram media insights.
    "plays": (
        "deprecated for IG media in v22.0 (returns 0 for posts after the transition window).",
        "use `views` instead; semantics differ for Reels vs static posts.",
    ),
    "impressions": (
        "deprecated for IG media insights in v22.0; for FB page-level "
        "metrics the timeline is different (retiring ~mid-2026).",
        "for IG: switch to `views`. For FB page-level: switch to "
        "`page_impressions_organic_v2` or migrate to post-level "
        "`post_impressions`.",
    ),
    # FB post insights — most still work under v22 but flagged by
    # Meta as candidates for removal in upcoming versions.
    "post_video_views": (
        "semantics shifted in v23.0 (no longer cross-device-deduped); "
        "true deprecation expected late 2026.",
        "pair with `post_video_view_time` for a stable engagement proxy.",
    ),
    "post_engaged_users": (
        "deprecated for new posts in v22.0; legacy posts still return.",
        "compute engagement from `post_reactions_*` + `post_comments` + `post_shares` instead.",
    ),
    # Carousel-specific.
    "carousel_album_impressions": (
        "deprecated; IG carousel insights are now reported per-card.",
        "iterate child media via /{ig_id}/children and fetch insights per child.",
    ),
}


def warn_if_deprecated(metric_names: str | list[str], *, context: str = "") -> None:
    """Log a WARNING for each requested metric in the deprecation registry.

    ``metric_names`` accepts either a comma-separated string (matching
    Meta's ``?metric=a,b,c`` request shape) or a list. ``context`` is
    appended to the log line so the operator can grep by call site.

    Idempotent — called once per request. No-op when none of the
    requested names are deprecated.
    """
    if isinstance(metric_names, str):
        names = [n.strip() for n in metric_names.split(",") if n.strip()]
    else:
        names = list(metric_names)

    for name in names:
        if name not in DEPRECATED_METRICS:
            continue
        note, hint = DEPRECATED_METRICS[name]
        suffix = f" [{context}]" if context else ""
        logger.warning(
            "[meta-metric-deprecation] `%s` is deprecated: %s Replacement: %s%s",
            name,
            note,
            hint,
            suffix,
        )


# ---------------------------------------------------------------------------
# Silent zero-out detector
# ---------------------------------------------------------------------------

# How many consecutive zero-value observations of the same metric
# before we promote the DEBUG counter to a loud ERROR. A few zeros
# per metric are normal (a niche with no engagement); a sustained
# zero across many observations is the silent-zero-out signal.
_ZERO_OUT_THRESHOLD: Final[int] = 25

# Process-local counter of {(metric_name, scope): consecutive_zeros}.
# ``scope`` distinguishes e.g. FB-feed vs FB-video insights so a
# legitimately-zero metric for one product doesn't poison the
# counter for another.
_zero_counts: dict[tuple[str, str], int] = {}


def record_observation(
    metric_name: str,
    value: float | int,
    *,
    scope: str = "default",
) -> None:
    """Record one observation of a metric's value.

    A value of 0 increments the per-metric counter; any non-zero
    value resets it. When the counter crosses
    :data:`_ZERO_OUT_THRESHOLD`, an ERROR is logged exactly once
    (the threshold itself is rounded to an emit boundary so we
    don't spam).

    Call this right after parsing an insights response, before the
    value flows into reward shaping. Cheap (one dict access per
    call); safe to call at hot paths.
    """
    key = (metric_name, scope)
    if value:
        # Reset on any non-zero. Single source of truth.
        if _zero_counts.get(key, 0) >= _ZERO_OUT_THRESHOLD:
            logger.info(
                "[meta-metric-deprecation] `%s` (scope=%s) recovered to "
                "non-zero value %s after %d consecutive zeros.",
                metric_name,
                scope,
                value,
                _zero_counts[key],
            )
        _zero_counts[key] = 0
        return

    _zero_counts[key] = _zero_counts.get(key, 0) + 1
    if _zero_counts[key] == _ZERO_OUT_THRESHOLD:
        logger.error(
            "[meta-metric-deprecation] SILENT ZERO-OUT: `%s` (scope=%s) "
            "has returned 0 for %d consecutive observations. The metric "
            "may have been deprecated by Meta. Check "
            "https://developers.facebook.com/docs/graph-api/changelog "
            "and update the deprecation registry.",
            metric_name,
            scope,
            _zero_counts[key],
        )


def reset_counters() -> None:
    """Drop the counter dict. Used in tests; not for production callers."""
    _zero_counts.clear()


def get_counter(metric_name: str, *, scope: str = "default") -> int:
    """Read the current consecutive-zero count for one metric. Used in tests."""
    return _zero_counts.get((metric_name, scope), 0)
