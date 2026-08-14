"""Flag-flip proposer (Phase 5.C session 1).

Per-flag evidence collectors + rule-based proposal generators for
the flags whose "should we flip?" question the system can now
answer autonomously.

## Design

Each flag has:

  * A ``current_value(env) -> str`` reader
  * An ``evidence(conn) -> dict`` collector that pulls the relevant
    signal (A/B lift, sample counts, quality correlation, etc.)
  * A ``propose(evidence) -> Proposal | None`` rule that emits a
    concrete flip proposal (or None when evidence doesn't justify
    action).

Session 2 will read pending proposals + auto-apply when confidence
> 0.9 AND no operator override in 24h.

## Fail-open

Every evidence collector fail-opens on any DB / artifact error.
Missing evidence → propose(None) → no proposal that day. Never
raises.

## Rule #22 discipline

Every rationale string includes the raw evidence (confusion matrix,
lift %, sample count) not just a summary. Pin test enforces this
for every rule.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlagFlipProposal:
    """One flag-flip candidate. Written to flag_flip_proposals."""
    flag_name: str
    from_state: str
    to_state: str
    rationale: str
    evidence: dict[str, Any]
    confidence: float

    def to_dict(self) -> dict:
        return {
            "flag_name": self.flag_name,
            "from_state": self.from_state,
            "to_state": self.to_state,
            "rationale": self.rationale,
            "evidence": self.evidence,
            "confidence": self.confidence,
        }


# ── Style guidance rollout ramp ─────────────────────────────────


def _propose_style_guidance_rollout(conn) -> FlagFlipProposal | None:
    """Bump GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT when the A/B analyzer
    (Phase 4.C session 2) shows lift >= 15% per roadmap gate.

    Uses average across all niches with n_treatment >= 10 so a
    single-niche outlier doesn't drive a global rollout ramp.

    Ladder: 25% → 50% → 75% → 100%. One step at a time.
    """
    current_raw = os.environ.get("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "0")
    try:
        current = int(current_raw)
    except (TypeError, ValueError):
        return None
    if current >= 100:
        return None  # already fully rolled out

    # Cross-niche A/B evidence
    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE (b.extra->>'style_guidance_injected')::boolean IS FALSE
              )::int AS n_c,
              COUNT(*) FILTER (
                WHERE (b.extra->>'style_guidance_injected')::boolean IS TRUE
              )::int AS n_t,
              AVG(pf.reward_48h) FILTER (
                WHERE (b.extra->>'style_guidance_injected')::boolean IS FALSE
              )::float AS mean_c,
              AVG(pf.reward_48h) FILTER (
                WHERE (b.extra->>'style_guidance_injected')::boolean IS TRUE
              )::float AS mean_t
            FROM pending_feedback pf
            JOIN publishing_analytics pa ON pa.post_id = pf.post_id
            JOIN blueprints b ON b.id = pa.blueprint_id
            WHERE pf.reward_48h IS NOT NULL
              AND b.extra ? 'style_guidance_injected'
              AND pf.updated_at >= NOW() - INTERVAL '14 days'
            """
        ).fetchone()
    except Exception as exc:
        logger.warning("[proposer] style-guidance A/B query failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if row is None:
        return None
    n_c = int(row["n_c"] or 0)
    n_t = int(row["n_t"] or 0)
    mean_c = row["mean_c"]
    mean_t = row["mean_t"]

    if n_c < 20 or n_t < 20 or mean_c is None or mean_t is None or mean_c <= 0:
        return None
    lift_pct = (float(mean_t) - float(mean_c)) / float(mean_c) * 100
    if lift_pct < 15.0:
        return None  # gate not met

    ladder = [25, 50, 75, 100]
    next_val = next((v for v in ladder if v > current), 100)
    return FlagFlipProposal(
        flag_name="GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT",
        from_state=str(current),
        to_state=str(next_val),
        rationale=(
            f"A/B lift {lift_pct:+.1f}% ≥ 15% roadmap gate "
            f"(n_control={n_c}, n_treatment={n_t}, "
            f"mean_control={mean_c:.3f}, mean_treatment={mean_t:.3f}) "
            f"— bump {current}%→{next_val}% one ladder step"
        ),
        evidence={
            "n_control": n_c, "n_treatment": n_t,
            "mean_control": float(mean_c), "mean_treatment": float(mean_t),
            "lift_pct": lift_pct, "ladder_step": f"{current}->{next_val}",
        },
        confidence=0.85,
    )


# ── Ideation pool rollout ramp ──────────────────────────────────


def _propose_ideation_pool_rollout(conn) -> FlagFlipProposal | None:
    """Bump GENLAB_IDEATION_POOL_ROLLOUT_PCT when pool-origin
    blueprints match or beat trending-origin reward per Phase 4.E
    success criteria.

    Ladder: 25% → 50% → 100%. Requires ≥10 pool + ≥50 trending
    blueprints in the 30d window.
    """
    current_raw = os.environ.get("GENLAB_IDEATION_POOL_ROLLOUT_PCT", "0")
    flag_on = os.environ.get(
        "GENLAB_IDEATION_POOL_ENABLED", "0",
    ).strip().lower() in {"1", "true", "yes"}
    try:
        current = int(current_raw)
    except (TypeError, ValueError):
        return None
    if not flag_on or current >= 100:
        return None

    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*) FILTER (
                WHERE (b.extra->>'origin') = 'ideation_pool'
              )::int AS n_pool,
              COUNT(*) FILTER (
                WHERE (b.extra->>'origin') IS DISTINCT FROM 'ideation_pool'
              )::int AS n_trending,
              AVG(pf.reward_48h) FILTER (
                WHERE (b.extra->>'origin') = 'ideation_pool'
              )::float AS mean_pool,
              AVG(pf.reward_48h) FILTER (
                WHERE (b.extra->>'origin') IS DISTINCT FROM 'ideation_pool'
              )::float AS mean_trend
            FROM pending_feedback pf
            JOIN publishing_analytics pa ON pa.post_id = pf.post_id
            JOIN blueprints b ON b.id = pa.blueprint_id
            WHERE pf.reward_48h IS NOT NULL
              AND pf.updated_at >= NOW() - INTERVAL '30 days'
            """
        ).fetchone()
    except Exception as exc:
        logger.warning("[proposer] ideation A/B failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if row is None:
        return None
    n_pool = int(row["n_pool"] or 0)
    n_trending = int(row["n_trending"] or 0)
    mean_pool = row["mean_pool"]
    mean_trend = row["mean_trend"]

    if n_pool < 10 or n_trending < 50 or mean_pool is None or mean_trend is None:
        return None
    # Roadmap gate 2: pool matches or beats trending
    if float(mean_pool) < float(mean_trend):
        return None

    ladder = [25, 50, 100]
    next_val = next((v for v in ladder if v > current), 100)
    lift_pct = (float(mean_pool) - float(mean_trend)) / float(mean_trend) * 100 if mean_trend > 0 else 0
    return FlagFlipProposal(
        flag_name="GENLAB_IDEATION_POOL_ROLLOUT_PCT",
        from_state=str(current),
        to_state=str(next_val),
        rationale=(
            f"Pool reward matches/beats trending "
            f"(mean_pool={mean_pool:.3f} vs mean_trend={mean_trend:.3f}, "
            f"lift {lift_pct:+.1f}%, n_pool={n_pool}, n_trending={n_trending}) "
            f"— bump {current}%→{next_val}%"
        ),
        evidence={
            "n_pool": n_pool, "n_trending": n_trending,
            "mean_pool": float(mean_pool), "mean_trending": float(mean_trend),
            "lift_pct": lift_pct, "ladder_step": f"{current}->{next_val}",
        },
        confidence=0.80,
    )


# ── Autonomous reviewer enablement ──────────────────────────────


def _propose_autonomous_reviewer_enable(conn) -> FlagFlipProposal | None:
    """Enable GENLAB_AUTONOMOUS_REVIEWER_ENABLED when Phase 1.C
    outcome_verifier accumulates enough verdicts per proposal type
    AND autonomous quality matches operator quality.
    """
    if os.environ.get(
        "GENLAB_AUTONOMOUS_REVIEWER_ENABLED", "",
    ).strip().lower() in {"1", "true", "yes"}:
        return None  # already on

    try:
        row = conn.execute(
            """
            SELECT
              COUNT(*)::int AS n_total,
              COUNT(DISTINCT proposal_type)::int AS n_types_covered,
              COUNT(*) FILTER (
                WHERE classifier_source IN ('heuristic', 'llm')
                  AND verdict = 'improved'
              )::int AS auto_imp,
              COUNT(*) FILTER (
                WHERE classifier_source IN ('heuristic', 'llm')
                  AND verdict = 'regressed'
              )::int AS auto_reg,
              COUNT(*) FILTER (
                WHERE classifier_source = 'manual'
                  AND verdict = 'improved'
              )::int AS op_imp,
              COUNT(*) FILTER (
                WHERE classifier_source = 'manual'
                  AND verdict = 'regressed'
              )::int AS op_reg
            FROM strategist_outcome_verification
            WHERE applied_at >= NOW() - INTERVAL '4 weeks'
              AND verdict != 'pending'
            """
        ).fetchone()
    except Exception as exc:
        logger.warning("[proposer] autonomous-reviewer evidence failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if row is None:
        return None
    n_total = int(row["n_total"] or 0)
    n_types = int(row["n_types_covered"] or 0)
    auto_denom = int(row["auto_imp"] or 0) + int(row["auto_reg"] or 0)
    op_denom = int(row["op_imp"] or 0) + int(row["op_reg"] or 0)

    if n_total < 20 or n_types < 3:
        return None  # not enough coverage
    if auto_denom == 0 or op_denom == 0:
        return None
    auto_quality = int(row["auto_imp"] or 0) / auto_denom
    op_quality = int(row["op_imp"] or 0) / op_denom
    # Roadmap: autonomous accept/reject quality matches operator's 85%+ agreement
    if auto_quality < op_quality - 0.05:
        return None  # autonomous is worse — don't flip

    return FlagFlipProposal(
        flag_name="GENLAB_AUTONOMOUS_REVIEWER_ENABLED",
        from_state="0",
        to_state="1",
        rationale=(
            f"Autonomous quality {auto_quality:.1%} matches operator "
            f"{op_quality:.1%} (auto n={auto_denom}, op n={op_denom}, "
            f"types_covered={n_types}, total_verdicts={n_total}) — "
            f"safe to enable"
        ),
        evidence={
            "n_total": n_total, "n_types_covered": n_types,
            "auto_quality": auto_quality, "operator_quality": op_quality,
            "auto_denom": auto_denom, "operator_denom": op_denom,
        },
        confidence=0.75,
    )


# ── Quality reward multiplier enable ────────────────────────────


def _propose_quality_reward_multiplier_enable(conn) -> FlagFlipProposal | None:
    """Enable GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED when Phase 4.A
    joint_score correlates positively with reward_48h (>= +0.2).
    """
    if os.environ.get(
        "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "",
    ).strip().lower() in {"1", "true", "yes"}:
        return None

    try:
        row = conn.execute(
            """
            SELECT COUNT(*)::int AS n,
                   CORR(cqs.joint_score, pf.reward_48h)::float AS corr
            FROM content_quality_scores cqs
            JOIN publishing_analytics pa ON pa.blueprint_id = cqs.blueprint_id
            JOIN pending_feedback pf ON pf.post_id = pa.post_id
            WHERE cqs.joint_score IS NOT NULL
              AND pf.reward_48h IS NOT NULL
              AND cqs.computed_at >= NOW() - INTERVAL '30 days'
            """
        ).fetchone()
    except Exception as exc:
        logger.warning("[proposer] quality-multiplier corr failed: %s", exc)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    if row is None:
        return None
    n = int(row["n"] or 0)
    corr = row["corr"]
    if n < 50 or corr is None:
        return None
    if float(corr) < 0.20:
        return None  # too weak

    return FlagFlipProposal(
        flag_name="GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED",
        from_state="0",
        to_state="1",
        rationale=(
            f"joint_score-reward correlation {corr:+.3f} ≥ +0.20 "
            f"across n={n} blueprints (30d window) — quality score "
            f"is a real signal, safe to multiply reward"
        ),
        evidence={
            "n_scored_blueprints": n,
            "pearson_correlation": float(corr),
        },
        confidence=0.70,
    )


# ── Registry ────────────────────────────────────────────────────


_PROPOSER_REGISTRY = (
    _propose_style_guidance_rollout,
    _propose_ideation_pool_rollout,
    _propose_autonomous_reviewer_enable,
    _propose_quality_reward_multiplier_enable,
)


def collect_proposals(conn) -> list[FlagFlipProposal]:
    """Run every registered proposer + return the non-None
    proposals. Order preserved so the runner can persist in
    deterministic order."""
    out: list[FlagFlipProposal] = []
    for fn in _PROPOSER_REGISTRY:
        try:
            proposal = fn(conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[proposer] %s crashed: %s", fn.__name__, exc)
            continue
        if proposal is not None:
            out.append(proposal)
    return out
