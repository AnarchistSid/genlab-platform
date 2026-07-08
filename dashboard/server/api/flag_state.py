"""Feature-flag aggregator endpoint (L11, 2026-07-08 audit).

Single endpoint returning the state of every GENLAB_* env flag known
to the codebase. Solves the operator problem the audit surfaced:
26+ flags scattered across the codebase, no single view of which
are on / off / dormant / mis-set. Meant to pair with a future
FlagStateCard on Mission Control; this PR ships the endpoint.

The value-add over `env` on the box
-----------------------------------
* **Case-sensitivity guard**: the H2 audit found that
  ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED="true"`` silently no-ops
  because the reader checks for the literal string ``"1"``. This
  endpoint flags every raw value that's set but doesn't match any
  common truthy convention — surfacing the class of bug before it
  causes silent regressions.
* **Grouped for humans**: flags are grouped by capability domain
  (learning / intelligence / observability / etc.) so a card can
  render categorized rows instead of an alphabetical wall of text.
* **Read-only**: the endpoint NEVER writes to env. Toggling flags
  is intentionally an SSH+.env exercise; this is observability only.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint("flag_state_api", __name__, url_prefix="/api/v1/flags")


# ---------------------------------------------------------------------------
# Flag catalog. Keep alphabetical within groups so a new addition lands in
# the obvious spot. When you add a NEW flag anywhere in genlab-core, add
# an entry here too — otherwise the operator's card won't show it. The
# catalog is intentionally maintained by-hand rather than derived from a
# grep of the codebase: the compiler sometimes optimizes strings, and
# the audit found a "phantom" flag (GENLAB_OPTIMAL_TIME_BANDIT_ENABLED)
# that was grep-visible but not actually read. Explicit list = truth.
# ---------------------------------------------------------------------------

_FLAG_CATALOG: list[dict[str, str]] = [
    # === Learning stack ==================================================
    {
        "name": "GENLAB_BAYESIAN_GATE_ENABLED",
        "group": "learning",
        "role": "Enables Bayesian LR gate for content approval",
    },
    {
        "name": "GENLAB_CONFORMAL_ROUTER_ENABLED",
        "group": "learning",
        "role": "Enables conformal router for auto-approve vs. operator",
    },
    {
        "name": "GENLAB_ENSEMBLE_DECISION_ENABLED",
        "group": "learning",
        "role": "Enables ensemble decision-making across gate + bandit",
    },
    {
        "name": "GENLAB_LINUCB_PICK_ENABLED",
        "group": "learning",
        "role": "Enables LinUCB contextual bandit for arm selection",
    },
    {
        "name": "GENLAB_LINUCB_STOCHASTIC_ENABLED",
        "group": "learning",
        "role": "Enables stochastic softmax mode for LinUCB",
    },
    {
        "name": "GENLAB_MULTI_WINDOW_REWARD_ENABLED",
        "group": "learning",
        "role": "Enables 6h/24h/48h/168h multi-window reward tracking",
    },
    {
        "name": "GENLAB_PER_PLATFORM_BANDIT_ENABLED",
        "group": "learning",
        "role": "Enables per-platform bandit arms",
    },
    {
        "name": "GENLAB_RATIONALE_CLASSIFIER_ENABLED",
        "group": "learning",
        "role": "Enables click-rationale classifier",
    },
    {
        "name": "GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED",
        "group": "learning",
        "role": "Enables rationale-category weighted reward shaping",
    },
    {
        "name": "GENLAB_TEMPORAL_CONTEXT_ENABLED",
        "group": "learning",
        "role": "Enables v2 cyclical time context for LinUCB",
    },
    # === Intelligence engines ===========================================
    {
        "name": "GENLAB_ANTICIPATION_ACCURACY_ENABLED",
        "group": "intelligence",
        "role": "Enables anticipation-accuracy validation runner",
    },
    {
        "name": "GENLAB_ANTICIPATION_NEWS_ENABLED",
        "group": "intelligence",
        "role": "Enables news_lead signal for trend anticipation",
    },
    {
        "name": "GENLAB_ANTICIPATION_REDDIT_ENABLED",
        "group": "intelligence",
        "role": "Enables social_velocity (Reddit) signal for anticipation",
    },
    {
        "name": "GENLAB_ANTICIPATION_YT_ENABLED",
        "group": "intelligence",
        "role": "Enables creator_pickup (YouTube) signal for anticipation",
    },
    {
        "name": "GENLAB_COUNTERFACTUAL_REPLAY_ENABLED",
        "group": "intelligence",
        "role": "Enables counterfactual IPS/DR replay",
    },
    {
        "name": "GENLAB_CROSS_NICHE_TRANSFER_ENABLED",
        "group": "intelligence",
        "role": "Enables cross-niche hierarchical Bayes prior transfer",
    },
    {
        "name": "GENLAB_STRATEGIST_INTEGRATION_ENABLED",
        "group": "intelligence",
        "role": "Enables Strategist proposal-driven pipeline steering",
    },
    {
        "name": "GENLAB_TOP_CREATOR_PRIORS_ENABLED",
        "group": "intelligence",
        "role": "Enables top-creator structural feature priors",
    },
    {
        "name": "GENLAB_TOP_CREATORS_ENABLED",
        "group": "intelligence",
        "role": "Enables top-creator watchlist polling",
    },
    {
        "name": "GENLAB_TREND_ANTICIPATION_ENABLED",
        "group": "intelligence",
        "role": "Enables trend-anticipation pipeline reordering",
    },
    # === Content quality =================================================
    {
        "name": "GENLAB_HOOK_CRITIC_ENABLED",
        "group": "content_quality",
        "role": "Enables LLM grounding critic on hooks",
    },
    {
        "name": "GENLAB_HOOK_DIVERSITY_ENABLED",
        "group": "content_quality",
        "role": "Enables hook-diversity gate",
    },
    {
        "name": "GENLAB_HOOK_REWRITER_ENABLED",
        "group": "content_quality",
        "role": "Enables LLM hook rewriter on borderline scores",
    },
    {
        "name": "GENLAB_INTELLIGENT_TRANSFORM_ENABLED",
        "group": "content_quality",
        "role": "Enables 11-dim transformation orchestrator",
    },
    {
        "name": "GENLAB_LLM_JUDGE_ENABLED",
        "group": "content_quality",
        "role": "Enables LLM-as-judge for gate borderline cases",
    },
    {
        "name": "GENLAB_RENDER_QC_ENABLED",
        "group": "content_quality",
        "role": "Enables render-QC pass on composite videos",
    },
    {
        "name": "GENLAB_REPLY_CRITIC_ENABLED",
        "group": "content_quality",
        "role": "Enables reply-critic on engagement auto-replies",
    },
    {
        "name": "GENLAB_VIDEO_ANALYSIS_ENABLED",
        "group": "content_quality",
        "role": "Enables video-analysis LLM pass",
    },
    {
        "name": "GENLAB_VIDEO_NICHE_FIT_ENABLED",
        "group": "content_quality",
        "role": "Enables niche-fit checker on video candidates",
    },
    {
        "name": "GENLAB_VISION_JUDGE_ENABLED",
        "group": "content_quality",
        "role": "Enables vision-based LLM judge on rendered frames",
    },
    # === Monetization ====================================================
    {
        "name": "GENLAB_PRODUCT_SELECTOR_ENABLED",
        "group": "monetization",
        "role": "Enables product-selector bandit on affiliate matcher",
    },
    # === Experimentation =================================================
    {
        "name": "GENLAB_EXPERIMENTATION_ENABLED",
        "group": "experimentation",
        "role": "Enables experimentation harness",
    },
    {
        "name": "GENLAB_POST_RCA_ENABLED",
        "group": "experimentation",
        "role": "Enables post-mortem root-cause analysis",
    },
    # === Infrastructure ==================================================
    {
        "name": "GENLAB_AUTO_APPROVE_DISABLED",
        "group": "infrastructure",
        "role": "Global kill-switch for auto-approval worker (DISABLE flag)",
        "polarity": "disable",
    },
    {
        "name": "GENLAB_PROMPT_CACHE_DISABLED",
        "group": "infrastructure",
        "role": "Disables Anthropic prompt caching (rarely used)",
        "polarity": "disable",
    },
]


# Common truthy value conventions across the codebase. If a raw env
# value doesn't match ANY of these but is nonetheless set, that's a
# case-sensitivity or typo warning worth surfacing to the operator.
_TRUTHY = frozenset({"1", "true", "yes", "on", "y", "t"})
# Values that unambiguously mean "off" — we DON'T warn on these even
# though they're not in _TRUTHY.
_FALSY = frozenset({"0", "false", "no", "off", "n", "f", ""})


def _classify_value(raw: str | None) -> tuple[str, str | None]:
    """Return (status, warning) for one flag's env value.

    status: one of ``"active"``, ``"dormant"``, ``"disabled_by_flag"``
      (for polarity=disable), ``"mis_set"``.
    warning: human-readable message, or None.

    We can't know for CERTAIN whether a flag will activate without
    reading the specific site's comparison. What we CAN do is surface
    every raw value that isn't obviously truthy OR obviously falsy —
    that's the class the H2 audit found silently no-oping.
    """
    if raw is None or raw == "":
        return "dormant", None
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return "active", None
    if normalized in _FALSY:
        return "dormant", None
    # Set to something that's neither truthy nor falsy → mis-set.
    return (
        "mis_set",
        f'value "{raw}" doesn\'t match a known truthy/falsy convention '
        f"— may silently no-op depending on reader",
    )


@bp.route("/state", methods=["GET"])
def get_flag_state():
    """Return the state of every catalogued GENLAB_* env flag.

    Response shape::

        {
          "status": "success",
          "data": {
            "flags": [
              {
                "name": "GENLAB_BAYESIAN_GATE_ENABLED",
                "group": "learning",
                "role": "Enables Bayesian LR gate...",
                "polarity": "enable",
                "raw_value": "1" | "true" | null,
                "status": "active" | "dormant" | "mis_set",
                "warning": "..." | null
              },
              ...
            ],
            "summary": {
              "total": 36,
              "active": 8,
              "dormant": 27,
              "mis_set": 1
            }
          }
        }

    Never 500s — a missing env var is dormant, a value we can't
    classify is `mis_set` (with a warning attached).
    """
    entries: list[dict[str, Any]] = []
    counters = {"active": 0, "dormant": 0, "mis_set": 0}

    for spec in _FLAG_CATALOG:
        raw = os.environ.get(spec["name"])
        # Redact BEFORE classification so a leaked-token env value can't
        # sneak out through the warning message. `_classify_value`
        # includes the raw string in its warning; if we redact after,
        # the warning still holds the full secret.
        raw_for_display = (
            raw if raw is None or len(raw) <= 32 else "…"
        )
        status, warning = _classify_value(raw_for_display)
        polarity = spec.get("polarity", "enable")

        # A "disable" flag inverts semantic: active means "feature
        # is DISABLED by operator". Rename for card-friendliness so
        # the frontend can render a red badge instead of a green one.
        if polarity == "disable" and status == "active":
            status = "disabled_by_flag"

        counters[status] = counters.get(status, 0) + 1

        entries.append(
            {
                "name": spec["name"],
                "group": spec["group"],
                "role": spec["role"],
                "polarity": polarity,
                # Already-redacted for display; never emit the raw
                # secret even in the audit trail.
                "raw_value": raw_for_display,
                "status": status,
                "warning": warning,
            }
        )

    return jsonify(
        {
            "status": "success",
            "data": {
                "flags": entries,
                "summary": {
                    "total": len(entries),
                    **counters,
                },
            },
        }
    )
