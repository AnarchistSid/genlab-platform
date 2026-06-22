"""DecisionRouter — per-decision-stakes tier selection.

Sibling to ``model_router.py``:
- ``model_router.get_model(task)`` routes by static task type
- ``model_router.get_model_with_budget(task)`` cascades cheaper as
  the budget burns
- ``decision_router.route_decision(decision_type, confidence, ...)``
  routes by per-CALL signal (how confident is the rule-based step?
  how big is the context window we need? what stakes are at play?)

The three primitives compose: budget pressure can still downgrade an
upgraded decision; decision stakes can upgrade what the task would
otherwise be. ``route_decision()`` is the wrapper most call sites
should use when the decision has stakes-dependent quality needs.

Why this matters (the 2026-06-22 audit finding):
  Today every LLM call uses Haiku regardless of decision importance.
  An auto-approval gate's LLM judge on a 0.45-confidence borderline
  blueprint deserves Sonnet's better reasoning; a routine "is this
  hook < 60 chars" check is fine with Haiku. The DecisionRouter
  encodes this stakes-aware routing.

Tier ladder:
  cheap     = Haiku (~$0.0002/call)         — trivial decisions
  balanced  = Sonnet 4.6 (~$0.003/call)     — borderline, high-stakes
  expensive = Opus 4.7 (~$0.015/call)       — cross-run reflection,
                                              huge contexts (1M tokens)

Decision types (closed set; add new ones in the per-type config):
  - "gate_judge"          — auto-approval gate's LLM judge on borderline
  - "hook_critic"         — post-generation hook quality check (Lever K)
  - "reply_critic"        — engagement reply quality check (PR #464)
  - "render_qc"           — multi-frame video quality vision ensemble
  - "cross_run_reflection" — weekly scratchpad / monthly RCA over
                             full episodic_events context (1M tokens)
  - "default"             — anything else: routes to cheap

Fail-OPEN at every error path: if the config is missing, the
decision_type is unknown, or any logic raises, we fall through to
the model_router's get_model(task='default') — always safer to
ship with the configured task model than to crash a gate.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

ModelTier = Literal["cheap", "balanced", "expensive"]

# Default tier mapping when decision_tiers.yaml is missing. These
# defaults are conservative (mostly cheap) so the router is safe to
# call from anywhere — costs only increase when an operator opts into
# the higher tiers via the YAML.
_DEFAULT_TIER_MODELS: dict[ModelTier, str] = {
    "cheap": "claude-haiku-4-5-20251001",
    "balanced": "claude-sonnet-4-6",
    "expensive": "claude-opus-4-7",
}

# Per-decision-type default tier + escalation rules. Operators can
# override the entire mapping via decision_tiers.yaml (see schema in
# _load_decision_tier_config docstring).
_DEFAULT_DECISION_RULES: dict[str, dict[str, Any]] = {
    "gate_judge": {
        # Auto-approval gate's LLM-as-judge on borderline blueprints.
        # 0.4-0.6 confidence band = genuinely uncertain → escalate.
        "default_tier": "cheap",
        "escalate_when": {"confidence_in": [0.4, 0.6], "tier": "balanced"},
    },
    "hook_critic": {
        # Lever K — most hooks are routine; escalate only when the
        # classifier is very unsure.
        "default_tier": "cheap",
        "escalate_when": {"confidence_in": [0.35, 0.55], "tier": "balanced"},
    },
    "reply_critic": {
        # PR #464 reply quality judge — cheap by default.
        "default_tier": "cheap",
        # No escalation by default — reply quality is the high-volume
        # surface; per-call escalation would explode cost.
    },
    "render_qc": {
        # Vision-judge ensemble on 3 frames; cheap is fine for QC.
        "default_tier": "cheap",
    },
    "cross_run_reflection": {
        # Weekly/monthly LLM summary of full episodic_events. Needs
        # the 1M context window + better reasoning to spot patterns.
        # Cost: ~1-4 calls/month. Worth the spend.
        "default_tier": "expensive",
    },
    "default": {"default_tier": "cheap"},
}


def _config_path() -> Path:
    """Resolve decision_tiers.yaml path. Operators can override via
    DECISION_TIERS_CONFIG env var (used in tests + alt deployments)."""
    custom = os.environ.get("DECISION_TIERS_CONFIG")
    if custom:
        return Path(custom)
    # genlab_core/cost/decision_router.py → cost/ → genlab_core/ → src/ → genlab-core/ → configs/
    return Path(__file__).resolve().parent.parent.parent.parent / "configs" / "decision_tiers.yaml"


@lru_cache(maxsize=1)
def _load_decision_tier_config(path_str: str) -> dict[str, Any]:
    """Load decision_tiers.yaml. Returns empty dict on missing/error
    so the call site falls back to ``_DEFAULT_DECISION_RULES``.

    Schema:
        tier_models:
          cheap: claude-haiku-4-5-20251001
          balanced: claude-sonnet-4-6
          expensive: claude-opus-4-7

        decisions:
          gate_judge:
            default_tier: cheap
            escalate_when:
              confidence_in: [0.4, 0.6]
              tier: balanced
          # ... more decision types
    """
    path = Path(path_str)
    if not path.exists():
        return {}
    try:
        with path.open() as f:
            return yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001 — config load is non-essential
        logger.warning("[decision_router] config load failed (%s); using defaults", exc)
        return {}


def _get_tier_models() -> dict[str, str]:
    """Resolve tier → model mapping (config-overridable)."""
    cfg = _load_decision_tier_config(str(_config_path()))
    tier_models = cfg.get("tier_models")
    if not isinstance(tier_models, dict):
        return dict(_DEFAULT_TIER_MODELS)
    # Merge: config wins, missing keys fall back to defaults
    out = dict(_DEFAULT_TIER_MODELS)
    out.update({k: v for k, v in tier_models.items() if isinstance(v, str)})
    return out


def _get_decision_rules() -> dict[str, dict[str, Any]]:
    """Resolve decision_type → rules mapping (config-overridable)."""
    cfg = _load_decision_tier_config(str(_config_path()))
    decisions = cfg.get("decisions")
    if not isinstance(decisions, dict):
        return dict(_DEFAULT_DECISION_RULES)
    # Merge: config wins, missing keys fall back to defaults
    out = dict(_DEFAULT_DECISION_RULES)
    for k, v in decisions.items():
        if isinstance(v, dict):
            out[k] = v
    return out


def route_decision(
    decision_type: str,
    *,
    confidence: float | None = None,
    context_size_tokens: int = 0,  # noqa: ARG001 — reserved for future Opus routing
) -> str:
    """Return the best model for a decision given its stakes.

    Args:
        decision_type: One of the closed-set decision types
            ("gate_judge", "hook_critic", "reply_critic", "render_qc",
            "cross_run_reflection", "default"). Unknown types fall
            through to "default" (cheap tier).
        confidence: When known, the rule-based step's confidence
            (0-1). Decisions with confidence in the escalation band
            for their type bump up one tier. None = no escalation.
        context_size_tokens: Reserved for future use — when the
            decision needs a 1M-token context (cross-run reflection),
            route to the reasoning tier regardless of other signals.

    Returns:
        The model name string to use in the next LLM call. Always
        returns a valid model — fail-OPEN to ``cheap`` tier on any
        config/logic error.

    Example::

        from genlab_core.cost.decision_router import route_decision

        # In auto_approval_gate's _llm_judge_borderline:
        model = route_decision("gate_judge", confidence=rule_decision.confidence)
        response = client.messages.create(model=model, ...)
    """
    try:
        rules = _get_decision_rules()
        decision_rule = rules.get(decision_type) or rules.get("default") or {}

        tier: ModelTier = decision_rule.get("default_tier", "cheap")

        # Confidence-based escalation
        escalate = decision_rule.get("escalate_when") or {}
        conf_band = escalate.get("confidence_in")
        if (
            confidence is not None
            and isinstance(conf_band, list)
            and len(conf_band) == 2
            and isinstance(conf_band[0], (int, float))
            and isinstance(conf_band[1], (int, float))
            and conf_band[0] <= confidence <= conf_band[1]
        ):
            escalated_tier = escalate.get("tier")
            if escalated_tier in ("cheap", "balanced", "expensive"):
                tier = escalated_tier

        tier_models = _get_tier_models()
        return tier_models.get(tier, _DEFAULT_TIER_MODELS["cheap"])
    except Exception as exc:  # noqa: BLE001 — fail-OPEN
        logger.debug(
            "[decision_router] routing failed for %s (%s); using cheap tier",
            decision_type,
            exc,
        )
        return _DEFAULT_TIER_MODELS["cheap"]


def reset_cache_for_tests() -> None:
    """Test-only: clear the lru_cache between tests that override
    DECISION_TIERS_CONFIG env or otherwise mutate the loaded config."""
    _load_decision_tier_config.cache_clear()
