"""Ensemble decision-making (Intervention 6, 2026-07-01).

Collects the decision-time signals from the five independent components
we ship — bandit posterior, hook classifier, Bayesian gate, conformal
router, and (optionally) an LLM judge — into a single ``EnsembleDecision``
with a weighted score AND a disagreement metric. High-agreement
decisions proceed with elevated confidence; high-disagreement decisions
route to a dedicated "worth-your-look" operator queue.

Why an ensemble matters
=======================

Each component is confident individually but can be confidently wrong in
its own blind-spot region. The 2026-06-30 audit found gaming's
auto-approval gate agrees with the operator only 22.4% of the time —
78% of the gate's "reject" verdicts get overridden. If the bandit
posterior + hook classifier + Bayesian gate + conformal router disagree
with the auto_approval_gate on the same blueprint, that disagreement is
a stronger signal than any single component's verdict.

Design
======

* **Pure function.** ``ensemble_decide`` never writes to Postgres, never
  fires the LLM by default, never mutates the blueprint. Callers that
  want to persist or act on the ensemble verdict do so themselves.
* **Feature-flagged.** ``GENLAB_ENSEMBLE_DECISION_ENABLED`` gates the
  real path. When off, the module returns a "disabled" verdict with
  ``score=None`` — safe for any caller to consume as no-op.
* **Fail-open per component.** Each vote is wrapped so a broken
  component (missing model, empty state file, DB unreachable) yields
  ``score=None`` for that component but doesn't torpedo the ensemble.
  When N components vote and M fail, the disagreement / weighted score
  is computed over the N-M that actually voted.
* **Additive layer.** ``auto_approval_gate.evaluate`` is unchanged. The
  ensemble is called ALONGSIDE the rule gate; the caller decides how to
  combine (e.g. "trust rule gate unless ensemble ``worth_your_look``").
  This keeps the existing auto-approval path shipped and rollback-safe.

Vote → score conventions
========================

Every vote's ``score`` lives in ``[0.0, 1.0]`` where 0 = "reject this
blueprint" and 1 = "approve this blueprint". Components that natively
speak that language (bandit posterior_mean, hook_classifier proba,
bayesian_gate posterior mean) go through unchanged. Components with
categorical outputs (conformal_router action) get mapped:

* ``auto_approve``  → 0.85 (high but not saturated so LLM disagreement
  can still swing the ensemble)
* ``auto_reject``   → 0.15
* ``route_to_operator``, ``disabled``, or unknown → None (abstains)

The LLM judge is only fired when the ensemble is already in the
borderline band (0.4-0.6) AND ``enable_llm_judge=True`` at the
call site AND ``GENLAB_LLM_JUDGE_ENABLED=1`` — same triple-gate as the
existing ``auto_approval_gate._llm_judge_borderline``.
"""

from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass, field
from typing import Final

logger = logging.getLogger(__name__)

# ── Feature flag ────────────────────────────────────────────────────

_ENABLE_ENV_VAR: Final[str] = "GENLAB_ENSEMBLE_DECISION_ENABLED"


def _integration_enabled() -> bool:
    """Exact-match feature flag. Only ``true`` / ``TRUE`` / ``True`` activates
    the ensemble. Any other value (including ``1``, ``yes``, ``on``,
    empty, unset) keeps the ensemble in its stub / disabled state.

    Same fail-closed pattern as :mod:`learning.late_reward`,
    :mod:`learning.ips_replay` (DR), and the rest of the intelligence
    package. Deliberately stricter than ``os.environ.get(...).lower()
    in {"1","true","yes"}`` — an accidental ``ENABLED=1`` in a shell
    export shouldn't flip on a system-level decision layer.
    """
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


# ── Weights ─────────────────────────────────────────────────────────

# Default component weights. Sum to 1.0. Order chosen so the two
# components with the strongest theoretical grounding (bandit posterior
# = direct reward evidence; Bayesian gate = per-niche operator agreement
# posterior) carry the most weight. Hook classifier is well-calibrated
# but only sees hook text, not the full blueprint. Conformal is
# distribution-free (theoretically strong) but currently trained on
# small samples per niche.
#
# Tuning path: once the ensemble has >100 decisions of calibration data
# (ensemble score vs operator action), fit a logistic regression to
# discover optimal weights per niche. Until then defaults are chosen
# by intuition + component maturity, not data.
_DEFAULT_WEIGHTS: Final[dict[str, float]] = {
    "bandit": 0.25,
    "hook_classifier": 0.20,
    "bayesian_gate": 0.25,
    "conformal_router": 0.20,
    "llm_judge": 0.10,
}


# Recommendation thresholds. Tuned to be conservative — the current
# shipped auto_approval_gate stays as the "hard" decision, and the
# ensemble's contribution is to identify HIGH-DISAGREEMENT cases the
# rule gate can't see.
#
# Threshold semantics:
#   * ``score >= _AUTO_APPROVE_THRESHOLD`` AND disagreement low
#     → recommend AUTO_APPROVE
#   * ``score <= _AUTO_REJECT_THRESHOLD`` AND disagreement low
#     → recommend AUTO_REJECT
#   * disagreement >= _WORTH_YOUR_LOOK_DISAGREEMENT
#     → recommend WORTH_YOUR_LOOK (elevated operator queue)
#   * else → recommend ROUTE_TO_OPERATOR (normal queue)
_AUTO_APPROVE_THRESHOLD: Final[float] = 0.65
_AUTO_REJECT_THRESHOLD: Final[float] = 0.35
_WORTH_YOUR_LOOK_DISAGREEMENT: Final[float] = 0.05
_LLM_JUDGE_BORDERLINE_LO: Final[float] = 0.40
_LLM_JUDGE_BORDERLINE_HI: Final[float] = 0.60


# ── Data classes ────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnsembleVote:
    """One component's contribution to the ensemble.

    ``score`` is ``None`` when the component was unable to vote (missing
    model, empty state, deps not installed, exception). Abstain-votes
    are excluded from the ensemble score AND the disagreement variance
    — they neither help nor hurt the aggregate.

    ``weight`` is the raw configured weight (before normalisation over
    the actually-voting subset). Useful for the operator to see "the
    Bayesian gate would have weighed 0.25 but abstained".
    """

    component: str
    score: float | None
    weight: float
    reason: str = ""


@dataclass(frozen=True)
class EnsembleDecision:
    """Aggregate ensemble result.

    Fields:
        score: Weighted [0, 1] score. ``None`` when the ensemble is
            disabled OR fewer than 2 components voted (can't compute
            meaningful disagreement with 1 vote).
        disagreement: Variance of the voting components' scores. Higher
            = more disagreement between components. 0.0 when fewer than
            2 components voted.
        n_voters: How many components had a non-None score. Below 2
            means the ensemble abstains.
        votes: Every component's vote (including abstain-votes with
            ``score=None``). Exposes the audit trail.
        recommendation: One of
            ``"auto_approve"``, ``"auto_reject"``, ``"worth_your_look"``,
            ``"route_to_operator"``, ``"disabled"``, ``"insufficient_data"``.
        reasons: Human-readable audit lines. Consumed by the dashboard
            badge tooltip.
    """

    score: float | None
    disagreement: float
    n_voters: int
    votes: list[EnsembleVote] = field(default_factory=list)
    recommendation: str = "disabled"
    reasons: list[str] = field(default_factory=list)


# ── Component adapters ─────────────────────────────────────────────


def _vote_bandit(blueprint: dict, niche_id: str) -> EnsembleVote:
    """Bandit posterior mean for the blueprint's ``arm_id``.

    Multi-arm blueprints (style + source + platform arms) surface the
    style arm as the blueprint's ``arm_id`` field. If no arm is
    recorded, or the arm isn't present in the niche's bandit_arms
    table, this vote abstains.
    """
    weight = _DEFAULT_WEIGHTS["bandit"]
    arm_id = (blueprint.get("arm_id") or "").strip()
    if not arm_id:
        return EnsembleVote("bandit", None, weight, "no arm_id on blueprint")

    try:
        # Lazy import — arm_loader hits Postgres via BacklogClient,
        # keeping it out of module-init keeps this fast to import.
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms

        client = BacklogClient()
        proxy = getattr(client, "bandit_arms", None)
        if proxy is None:
            return EnsembleVote("bandit", None, weight, "no bandit_arms proxy")
        arms = load_all_arms(proxy, niche_id)
    except Exception as exc:
        logger.debug("[ensemble] bandit load failed: %s", exc)
        return EnsembleVote("bandit", None, weight, f"load error: {exc}")

    row = arms.get(arm_id)
    if row is None:
        return EnsembleVote("bandit", None, weight, f"arm {arm_id!r} not in niche state")

    alpha, beta = row
    denom = alpha + beta
    if denom <= 0:
        return EnsembleVote("bandit", None, weight, "alpha+beta=0")
    posterior_mean = alpha / denom
    return EnsembleVote(
        "bandit",
        posterior_mean,
        weight,
        f"posterior_mean={posterior_mean:.3f} (α={alpha:.1f}, β={beta:.1f})",
    )


def _vote_hook_classifier(blueprint: dict, niche_id: str) -> EnsembleVote:
    """Hook classifier XGBoost score. Model loaded lazily + cached."""
    weight = _DEFAULT_WEIGHTS["hook_classifier"]
    hook = (blueprint.get("hook_text") or "").strip()
    if not hook:
        return EnsembleVote("hook_classifier", None, weight, "no hook_text")

    try:
        from genlab_core.learning.hook_classifier import HookClassifier

        # HookClassifier.__init__ tries to load the niche's model from
        # ``genlab-core/models/hook_classifier_<niche>.json``; when the
        # file is missing it stays with ``_loaded=False`` and
        # ``score_hook`` returns 0.5 (the abstain sentinel).
        clf = HookClassifier(niche_id=niche_id)
        if not clf._loaded:
            return EnsembleVote("hook_classifier", None, weight, "no trained model")
        score = clf.score_hook(hook)
        # score_hook returns 0.5 on any internal error. We treat 0.5
        # as "abstain" here because a coin-flip vote doesn't move the
        # ensemble but appears in disagreement variance as noise.
        if abs(score - 0.5) < 1e-6:
            return EnsembleVote("hook_classifier", None, weight, "abstain (score=0.5)")
        return EnsembleVote("hook_classifier", float(score), weight, f"proba={score:.3f}")
    except Exception as exc:
        logger.debug("[ensemble] hook_classifier vote failed: %s", exc)
        return EnsembleVote("hook_classifier", None, weight, f"error: {exc}")


def _vote_bayesian_gate(blueprint: dict, niche_id: str) -> EnsembleVote:
    """Bayesian gate posterior-mean p(approve). Returns None when the
    gate is env-flag-disabled OR the niche has too few calibration
    rows OR numpy/scipy is unavailable."""
    weight = _DEFAULT_WEIGHTS["bayesian_gate"]
    try:
        from genlab_core.learning.bayesian_gate import sample_preference

        _sampled, p_mean = sample_preference(blueprint, niche_id)
        # (0.5, 0.5) is the sentinel for "gate is disabled or degenerate"
        # — see docstring. Treat as abstain.
        if abs(p_mean - 0.5) < 1e-6:
            return EnsembleVote("bayesian_gate", None, weight, "abstain (mean=0.5)")
        return EnsembleVote("bayesian_gate", float(p_mean), weight, f"p_mean={p_mean:.3f}")
    except Exception as exc:
        logger.debug("[ensemble] bayesian_gate vote failed: %s", exc)
        return EnsembleVote("bayesian_gate", None, weight, f"error: {exc}")


def _vote_conformal_router(blueprint: dict, niche_id: str) -> EnsembleVote:
    """Conformal router action → score mapping.

    Conformal returns a categorical action (auto_approve / auto_reject
    / route_to_operator). We map to a scalar so it can vote in the
    ensemble average:

    * ``auto_approve`` (singleton {"approve"})     → 0.85
    * ``auto_reject``  (singleton {"reject"})      → 0.15
    * ``route_to_operator`` (doubleton or empty)   → None (abstain)
    * ``disabled`` / unknown                       → None (abstain)

    0.85 rather than 1.0 keeps head-room so LLM disagreement can still
    swing a saturated conformal vote. Symmetric on the reject side.
    """
    weight = _DEFAULT_WEIGHTS["conformal_router"]
    try:
        from genlab_core.scheduling.conformal_router import route_blueprint

        result = route_blueprint(blueprint)
        action = result.action
        if action == "auto_approve":
            return EnsembleVote(
                "conformal_router",
                0.85,
                weight,
                f"auto_approve (q_hat={result.q_hat:.3f})",
            )
        if action == "auto_reject":
            return EnsembleVote(
                "conformal_router",
                0.15,
                weight,
                f"auto_reject (q_hat={result.q_hat:.3f})",
            )
        return EnsembleVote(
            "conformal_router",
            None,
            weight,
            f"abstain ({action}: {result.reason[:60]})",
        )
    except Exception as exc:
        logger.debug("[ensemble] conformal vote failed: %s", exc)
        return EnsembleVote("conformal_router", None, weight, f"error: {exc}")


def _vote_llm_judge(
    blueprint: dict,
    niche_id: str,
    interim_score: float,
) -> EnsembleVote:
    """LLM judge — only invoked when ``interim_score`` is in the
    borderline band AND both ``enable_llm_judge`` (caller) and
    ``GENLAB_LLM_JUDGE_ENABLED`` (env) are on. This mirrors the
    existing borderline-only escalation policy in auto_approval_gate
    to keep spend bounded.
    """
    weight = _DEFAULT_WEIGHTS["llm_judge"]
    if not (_LLM_JUDGE_BORDERLINE_LO <= interim_score <= _LLM_JUDGE_BORDERLINE_HI):
        return EnsembleVote(
            "llm_judge",
            None,
            weight,
            f"skipped (interim {interim_score:.2f} not borderline)",
        )
    if os.environ.get("GENLAB_LLM_JUDGE_ENABLED", "0") != "1":
        return EnsembleVote("llm_judge", None, weight, "GENLAB_LLM_JUDGE_ENABLED != 1")

    try:
        # Reuse the existing borderline-judge implementation. It returns
        # an updated AutoApprovalDecision (or None). We map the returned
        # decision's confidence back to a scalar vote.
        from genlab_core.scheduling.auto_approval_gate import (
            AutoApprovalDecision,
            _llm_judge_borderline,
        )

        # Build a synthetic rule_decision the judge can reason over —
        # we're using the interim ensemble score as a proxy for
        # rule-gate confidence so the judge prompt gets a signal that
        # the ensemble is truly in the borderline band.
        seed = AutoApprovalDecision(
            approved=interim_score >= 0.5,
            confidence=interim_score,
        )
        judged = _llm_judge_borderline(blueprint, seed)
        if judged is None:
            return EnsembleVote("llm_judge", None, weight, "judge returned None")
        # The judge's confidence lives in [0, 1] with the same semantics
        # as our ensemble votes — "how sure is the judge this should
        # be approved". Direct passthrough.
        return EnsembleVote(
            "llm_judge",
            float(judged.confidence),
            weight,
            f"judge said {'approve' if judged.approved else 'reject'} "
            f"(confidence={judged.confidence:.3f})",
        )
    except Exception as exc:
        logger.debug("[ensemble] llm_judge vote failed: %s", exc)
        return EnsembleVote("llm_judge", None, weight, f"error: {exc}")


# ── Aggregation ────────────────────────────────────────────────────


def _aggregate(votes: list[EnsembleVote]) -> tuple[float | None, float, int]:
    """Compute (weighted_score, disagreement_variance, n_voters).

    Weights are RE-normalised over the subset of voters (so a missing
    component doesn't silently shrink the score toward 0). If fewer
    than 2 voters have non-None scores, the ensemble abstains and
    returns ``(None, 0.0, n_voters)``.
    """
    valid = [v for v in votes if v.score is not None]
    n_voters = len(valid)
    if n_voters < 2:
        return None, 0.0, n_voters

    total_weight = sum(v.weight for v in valid)
    if total_weight <= 0:
        return None, 0.0, n_voters

    weighted = sum((v.weight / total_weight) * v.score for v in valid)
    # Variance across the voters' raw scores (unweighted — variance is
    # a disagreement measure, not a signal aggregation).
    disagreement = statistics.pvariance([v.score for v in valid])
    return weighted, disagreement, n_voters


def _recommend(score: float | None, disagreement: float) -> str:
    """Threshold logic that turns (score, disagreement) into an
    actionable label the caller can route on."""
    if score is None:
        return "insufficient_data"
    if disagreement >= _WORTH_YOUR_LOOK_DISAGREEMENT:
        # High disagreement is more informative than the score itself —
        # even if the weighted score is above threshold, the fact that
        # components disagree means the confident-wrong risk is elevated.
        return "worth_your_look"
    if score >= _AUTO_APPROVE_THRESHOLD:
        return "auto_approve"
    if score <= _AUTO_REJECT_THRESHOLD:
        return "auto_reject"
    return "route_to_operator"


# ── Public entry point ─────────────────────────────────────────────


def ensemble_decide(
    blueprint: dict,
    niche_id: str,
    *,
    enable_llm_judge: bool = False,
) -> EnsembleDecision:
    """Compute the ensemble verdict for a blueprint.

    Args:
        blueprint: The blueprint dict (same shape auto_approval_gate.evaluate consumes).
        niche_id: Niche identifier used by each component adapter.
        enable_llm_judge: When True, allow the LLM judge to be fired in
            the borderline band. Even then the judge only actually fires
            if ``GENLAB_LLM_JUDGE_ENABLED=1`` — the flag is the hard
            gate, the kwarg is the caller's opt-in.

    Returns:
        An :class:`EnsembleDecision`. When the ensemble is env-flag
        disabled or fewer than 2 components can vote, ``score`` is
        ``None`` — safe for callers to treat as no-op.
    """
    if not _integration_enabled():
        return EnsembleDecision(
            score=None,
            disagreement=0.0,
            n_voters=0,
            votes=[],
            recommendation="disabled",
            reasons=[f"{_ENABLE_ENV_VAR} not set to 'true'"],
        )

    # Run the four component adapters. Order is arbitrary — each is
    # independent — but stable so telemetry / logs stay comparable
    # across calls.
    votes: list[EnsembleVote] = [
        _vote_bandit(blueprint, niche_id),
        _vote_hook_classifier(blueprint, niche_id),
        _vote_bayesian_gate(blueprint, niche_id),
        _vote_conformal_router(blueprint, niche_id),
    ]

    interim_score, _, _ = _aggregate(votes)

    # LLM judge only fires when the interim ensemble is borderline
    # (0.4-0.6). This mirrors auto_approval_gate's escalation policy
    # and keeps spend bounded to the ~20% of blueprints where the
    # other four components together produced an unclear signal.
    if enable_llm_judge and interim_score is not None:
        votes.append(_vote_llm_judge(blueprint, niche_id, interim_score))

    score, disagreement, n_voters = _aggregate(votes)
    recommendation = _recommend(score, disagreement)

    reasons = [v.reason for v in votes if v.reason]
    reasons.append(
        f"n_voters={n_voters} score={score:.3f} disagreement={disagreement:.4f} → {recommendation}"
        if score is not None
        else f"n_voters={n_voters} — {recommendation}"
    )

    return EnsembleDecision(
        score=score,
        disagreement=disagreement,
        n_voters=n_voters,
        votes=votes,
        recommendation=recommendation,
        reasons=reasons,
    )


__all__ = [
    "EnsembleVote",
    "EnsembleDecision",
    "ensemble_decide",
]
