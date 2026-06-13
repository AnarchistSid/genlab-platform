"""Multi-publish gate: consults the optimal-time learner to recommend
how many publishes per (niche × platform × UTC day) is justified.

Task #33b (2026-06-13). The owner's 2026-06-12 decision was: "agent
also pick optimal publish-times per platform and can publish more than
once if required." The optimal_time_learner ships the first half; this
gate ships the second.

Design:

- The gate ONLY RECOMMENDS — it never overrides DailyCapEnforcer's
  yaml-declared cap unless the operator has opted in via the
  ``multi_publish.enabled`` flag in platform_caps.yaml. Without the
  opt-in, behavior is unchanged from the pre-fix "1 reel per channel
  per UTC day" rule (CLAUDE.md non-negotiable).

- When opt-in is set, the gate's ``recommended_max_per_day`` returns
  the number of learner hours whose Bayesian-shrunk confidence
  exceeds ``_MIN_CONFIDENCE_FOR_MULTI``. A single high-confidence
  hour → cap stays at 1 (no point publishing twice if only one hour
  is good). Two or more → cap rises proportionally, bounded above by
  the operator's ``max_per_day_ceiling`` yaml field.

- Defaults are deliberately conservative so flipping ``enabled: true``
  doesn't immediately spam every channel. The operator still controls
  the ceiling per platform.

The DailyCapEnforcer composes its effective cap as:
    effective_cap = min(max_per_day_ceiling, max(daily_post_cap,
                                                 recommended_max_per_day))
when ``multi_publish.enabled`` is true, falling back to
``daily_post_cap`` when false (current behavior).
"""

from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)

# Minimum Bayesian-shrunk confidence required for an hour to count as a
# multi-publish-justifying slot. Picked from the 2026-06-13 prod probe:
# real signals (movies-IG 10UTC conf=384, anime-FB 10UTC conf=103) sit
# in the 100-400 range; pure noise sits near the global avg. 100 picks
# up the noisier signals (anime-FB at conf=103 just barely clears) so
# multi-publish is enabled aggressively when the learner has real data.
# Operators can raise this in code when they want a stricter filter.
_MIN_CONFIDENCE_FOR_MULTI: Final[float] = 100.0


def recommended_max_per_day(
    niche_id: str,
    platform: str,
    *,
    min_confidence: float = _MIN_CONFIDENCE_FOR_MULTI,
    top_n: int = 3,
) -> int:
    """Recommend the maximum publishes-per-day for (niche, platform).

    Returns:
      - 1 when the optimal_time_learner has no signal (cold start, DB
        unreachable, < min_observations per hour bucket) OR when no
        hour meets ``min_confidence``. This is the "safe default" —
        never recommend more than the current 1/day rule.
      - 2-3 when 2-3 hours meet ``min_confidence``, scaling with the
        number of justified slots. Bounded above by ``top_n`` to
        prevent runaway recommendations on flat-distribution niches.

    This is a RECOMMENDATION. DailyCapEnforcer composes it with the
    operator's ceiling and the yaml's `daily_post_cap` before deciding
    the effective cap (see the module docstring for the formula).
    """
    if not niche_id or not platform:
        return 1

    try:
        from genlab_core.scheduling.optimal_time_learner import get_optimal_hours

        hours = get_optimal_hours(niche_id, platform, top_n=top_n)
    except Exception as exc:
        logger.warning(
            "[multi_publish_gate] learner failed for %s/%s: %s; recommending 1",
            niche_id,
            platform,
            exc,
        )
        return 1

    if not hours:
        return 1

    qualified = [h for h in hours if h.confidence >= min_confidence]
    if not qualified:
        return 1

    # Cap at top_n — even if every hour cleared the confidence floor we
    # shouldn't recommend publishing 24× in a day. (Hypothetical, since
    # top_n=3 by default.)
    return min(top_n, max(1, len(qualified)))


def is_multi_publish_enabled(
    caps_config: dict | None = None,
    *,
    platform: str = "",
) -> bool:
    """Read the operator's opt-in flag.

    Returns True when ``multi_publish.enabled`` is set to true in
    platform_caps.yaml AND (when ``platform`` is provided) that platform
    is in the ``multi_publish.platforms`` allowlist (or the allowlist
    is empty meaning "all platforms").

    Defaults to False (current behavior) when the section is missing.
    """
    cfg = (caps_config or {}).get("multi_publish", {})
    if not cfg.get("enabled", False):
        return False
    if platform:
        allowed = cfg.get("platforms", [])
        if allowed and platform.lower() not in {p.lower() for p in allowed}:
            return False
    return True


def effective_cap(
    niche_id: str,
    platform: str,
    *,
    daily_post_cap: int,
    max_per_day_ceiling: int,
    multi_publish_enabled: bool,
) -> int:
    """Compose the effective cap from yaml settings + learner recommendation.

    Formula:
      - When multi_publish_enabled is False → daily_post_cap (current
        "1 per day" behavior; learner ignored).
      - When True → min(max_per_day_ceiling,
                        max(daily_post_cap, recommended_max_per_day))
        Operator's ceiling always wins as an upper bound — the agent
        cannot go above it. The learner can raise the cap from the
        conservative `daily_post_cap` toward the ceiling when signal
        justifies it.

    Always returns a positive integer.
    """
    daily_post_cap = max(1, int(daily_post_cap))
    max_per_day_ceiling = max(daily_post_cap, int(max_per_day_ceiling))

    if not multi_publish_enabled:
        return daily_post_cap

    recommended = recommended_max_per_day(niche_id, platform)
    return min(max_per_day_ceiling, max(daily_post_cap, recommended))
