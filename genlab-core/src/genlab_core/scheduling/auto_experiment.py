"""Auto-experiment scaffold — read/write auto_experiments table.

Motivating problem: strategist causal_hypotheses each carry a
``testable_prediction`` field like "reward >0.20 after n≥5
observations". These sit unused because setting up A/B experiments
requires operator YAML edits to config/experiments.yaml.

This module provides:
* ``queue_pending_experiment`` — write a spec row that a future
  scheduler picks up
* ``start_pending_experiments`` — move status pending → running
  (called when an experiment is due to begin)
* ``check_running_experiments`` — evaluate whether running
  experiments have concluded, compute the metric, write to
  auto_experiments.result
* ``list_experiments`` — dashboard consumer

Scaffold-only in this first iteration: the testable_prediction →
spec parser is left as ``TODO``. Operator or a future LLM pass
populates the spec JSON. Downstream code + dashboards can be built
against a stable schema.

See:
* ``k8g9h0i1j2k3_auto_experiments_table.py`` — migration
* Rule #22 sibling: experiment outcomes are OBSERVATIONS. They
  inform future proposals but don't auto-apply.

Fail-open on every DB error path.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

logger = logging.getLogger(__name__)


_ENABLE_ENV_VAR: Final[str] = "GENLAB_AUTO_EXPERIMENT_ENABLED"

# Default duration for A/B experiments when the spec doesn't specify.
DEFAULT_DURATION_DAYS: Final[int] = 7


def is_enabled() -> bool:
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


@dataclass
class ExperimentSpec:
    """Structured specification for one A/B experiment.

    Fields
    ------
    arms : list[str]
        Bandit arm IDs participating in the experiment (usually 2).
    niche_id : str
        Which niche the experiment runs for. Empty = all niches.
    expected_metric_shift : float
        Strategist's expected reward-lift threshold. Result compares
        against this.
    duration_days : int
        How long to run before evaluating. Default 7.
    notes : str
        Free-form context.
    """

    arms: list[str] = field(default_factory=list)
    niche_id: str = ""
    expected_metric_shift: float = 0.0
    duration_days: int = DEFAULT_DURATION_DAYS
    notes: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "arms": self.arms,
                "niche_id": self.niche_id,
                "expected_metric_shift": self.expected_metric_shift,
                "duration_days": self.duration_days,
                "notes": self.notes,
            }
        )


def queue_pending_experiment(
    conn,
    source_report_id: str,
    hypothesis_index: int,
    niche_id: str,
    spec: ExperimentSpec,
    notes: str = "",
) -> str | None:
    """Insert a pending experiment. Returns the row's UUID or None on error.

    Idempotent via ``uq_auto_experiments_source_hypo`` — re-inserting
    the same (source_report_id, hypothesis_index) pair is a no-op.
    """
    try:
        row = conn.execute(
            """
            INSERT INTO auto_experiments
                (source_report_id, hypothesis_index, niche_id, spec, notes)
            VALUES (%s, %s, %s, %s::jsonb, %s)
            ON CONFLICT (source_report_id, hypothesis_index) DO NOTHING
            RETURNING id::text
            """,
            (source_report_id, hypothesis_index, niche_id, spec.to_json(), notes),
        ).fetchone()
        return row[0] if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[auto_experiment] queue failed for report=%s hypo=%s: %s",
            source_report_id,
            hypothesis_index,
            exc,
        )
        return None


def start_pending_experiments(conn) -> int:
    """Advance pending → running for experiments whose spec.arms is
    populated. Returns the count started.
    """
    try:
        result = conn.execute(
            """
            UPDATE auto_experiments
            SET status = 'running',
                started_at = NOW()
            WHERE status = 'pending'
              AND spec != '{}'::jsonb
              AND jsonb_array_length(COALESCE(spec->'arms', '[]'::jsonb)) >= 2
            RETURNING id
            """
        ).fetchall()
        return len(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[auto_experiment] start_pending failed: %s", exc)
        return 0


def check_running_experiments(conn) -> list[dict[str, Any]]:
    """Return running experiments that have exceeded their duration.

    Caller computes the metric, writes to result JSONB, and advances
    the status. Kept as a read function so the metric-computation is
    delegated to a caller with domain context.
    """
    try:
        rows = conn.execute(
            """
            SELECT id::text AS id,
                   source_report_id::text AS source_report_id,
                   hypothesis_index,
                   niche_id,
                   spec,
                   started_at
            FROM auto_experiments
            WHERE status = 'running'
              AND started_at IS NOT NULL
              AND started_at < NOW() - make_interval(
                days => COALESCE((spec->>'duration_days')::int, 7)
              )
            ORDER BY started_at ASC
            """
        ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            if isinstance(r, dict):
                result.append(r)
            else:
                # Positional row tuple.
                result.append(
                    {
                        "id": r[0],
                        "source_report_id": r[1],
                        "hypothesis_index": r[2],
                        "niche_id": r[3],
                        "spec": r[4],
                        "started_at": r[5],
                    }
                )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("[auto_experiment] check_running failed: %s", exc)
        return []


def complete_experiment(
    conn,
    experiment_id: str,
    result: dict[str, Any],
) -> bool:
    """Mark an experiment complete with the observed result.

    Result JSON typically carries: observed_reward_arm_a,
    observed_reward_arm_b, n_samples_per_arm, met_threshold (bool).
    """
    try:
        conn.execute(
            """
            UPDATE auto_experiments
            SET status = 'completed',
                completed_at = NOW(),
                result = %s::jsonb
            WHERE id = %s
              AND status = 'running'
            """,
            (json.dumps(result), experiment_id),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[auto_experiment] complete failed for %s: %s",
            experiment_id,
            exc,
        )
        return False


def list_experiments(
    conn,
    *,
    status: str | None = None,
    niche_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read auto_experiments rows for dashboard/CLI display."""
    where = ["1=1"]
    params: list[Any] = []
    if status:
        where.append("status = %s")
        params.append(status)
    if niche_id:
        where.append("niche_id = %s")
        params.append(niche_id)
    where_sql = " AND ".join(where)
    params.append(limit)
    try:
        rows = conn.execute(
            f"""
            SELECT id::text AS id, source_report_id::text AS source_report_id,
                   hypothesis_index, niche_id, spec, status, result,
                   created_at, started_at, completed_at
            FROM auto_experiments
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            tuple(params),
        ).fetchall()
        return [dict(r) if hasattr(r, "keys") else _row_to_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[auto_experiment] list failed: %s", exc)
        return []


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Fallback row-to-dict for tuple-cursors."""
    columns = [
        "id", "source_report_id", "hypothesis_index", "niche_id",
        "spec", "status", "result", "created_at", "started_at",
        "completed_at",
    ]
    return {c: row[i] for i, c in enumerate(columns) if i < len(row)}


# Minimum reward samples per arm before ``met_threshold`` can be True.
# 5 is the conservative minimum where 48h-window Beta posteriors have
# meaningful separation. Below that a treatment "win" is likely noise.
MIN_SAMPLES_PER_ARM: Final[int] = 5


def measure_experiment_result(
    conn,
    experiment: dict[str, Any],
) -> dict[str, Any]:
    """Compute per-arm reward from pending_feedback within the
    experiment's time window. Returns a result dict ready to persist
    via ``complete_experiment``.

    Shape:
        {
          "arm_rewards": {
            "<arm_id>": {"observed_reward": <float>, "n_samples": <int>}
          },
          "expected_metric_shift": <float>,
          "observed_lift": <float | null>,  # arm[1] - arm[0]
          "met_threshold": <bool>,
          "sufficient_samples": <bool>,
          "min_samples_required": <int>,
          "window_start": <ISO 8601>,
          "window_end": <ISO 8601>,
        }

    Discipline:
    * observed_reward per arm = AVG(reward_48h) where the reward was
      finalised within the experiment window.
    * met_threshold requires BOTH sufficient_samples AND
      observed_lift >= expected_metric_shift.
    * Fail-open: any DB error returns a partial result with met_threshold
      = False so the row can still be marked completed.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    spec = experiment.get("spec") or {}
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception:
            spec = {}
    arms = spec.get("arms") or []
    if not isinstance(arms, list):
        arms = []
    niche_id = experiment.get("niche_id") or spec.get("niche_id") or ""
    expected_shift = float(spec.get("expected_metric_shift", 0.0) or 0.0)
    started_at = experiment.get("started_at")

    # Normalise started_at to ISO if it came back as a datetime.
    if hasattr(started_at, "isoformat"):
        window_start_iso = started_at.isoformat()
    else:
        window_start_iso = str(started_at) if started_at else ""
    window_end_iso = _dt.now(_UTC).isoformat()

    arm_rewards: dict[str, dict[str, Any]] = {}
    for arm_id in arms:
        arm_str = str(arm_id).strip()
        if not arm_str:
            continue
        try:
            row = conn.execute(
                """
                SELECT AVG(reward_48h)::float AS avg_r,
                       COUNT(*)::int AS n
                FROM pending_feedback
                WHERE niche_id = %s
                  AND arm_id = %s
                  AND reward_48h IS NOT NULL
                  AND updated_at >= %s
                  AND updated_at <= %s
                """,
                (niche_id, arm_str, window_start_iso, window_end_iso),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[auto_experiment] reward query failed for arm=%s: %s",
                arm_str,
                exc,
            )
            arm_rewards[arm_str] = {"observed_reward": None, "n_samples": 0}
            continue
        if row is None:
            arm_rewards[arm_str] = {"observed_reward": None, "n_samples": 0}
            continue
        if hasattr(row, "get"):
            avg_r = row.get("avg_r")
            n = int(row.get("n") or 0)
        else:
            avg_r = row[0]
            n = int(row[1] or 0)
        arm_rewards[arm_str] = {
            "observed_reward": None if avg_r is None else float(avg_r),
            "n_samples": n,
        }

    # Compute observed_lift as arm[1] - arm[0] (treatment - control).
    observed_lift: float | None = None
    sufficient_samples = False
    met_threshold = False
    if len(arms) >= 2:
        control = arm_rewards.get(str(arms[0]).strip(), {})
        treatment = arm_rewards.get(str(arms[1]).strip(), {})
        c_reward = control.get("observed_reward")
        t_reward = treatment.get("observed_reward")
        c_n = control.get("n_samples", 0)
        t_n = treatment.get("n_samples", 0)
        sufficient_samples = (
            c_n >= MIN_SAMPLES_PER_ARM and t_n >= MIN_SAMPLES_PER_ARM
        )
        if c_reward is not None and t_reward is not None:
            observed_lift = float(t_reward) - float(c_reward)
            met_threshold = (
                sufficient_samples and observed_lift >= expected_shift
            )

    return {
        "arm_rewards": arm_rewards,
        "expected_metric_shift": expected_shift,
        "observed_lift": observed_lift,
        "met_threshold": met_threshold,
        "sufficient_samples": sufficient_samples,
        "min_samples_required": MIN_SAMPLES_PER_ARM,
        "window_start": window_start_iso,
        "window_end": window_end_iso,
    }


def promote_verdict_to_proposal(
    conn, experiment: dict[str, Any]
) -> tuple[str | None, str]:
    """Auto-accept the strategist proposal that seeded a completed
    experiment when the reward measurement confirmed the hypothesis.

    Verdict-confirmed promotion runs iff BOTH:
      * ``result.met_threshold`` is True
      * ``result.sufficient_samples`` is True

    Linkage: the experiment's ``spec.arms[1]`` (treatment) is matched
    against ``strategist_reports.proposals[i].proposed.arm_id``. Since
    the experiment was queued FROM a strategist testable_prediction,
    a proposal for the same arm typically exists in the same report.

    Confidence handling: the experiment-verdict is EMPIRICAL evidence
    (measured reward lift with n>=5/arm), which is stronger than the
    strategist's tagged confidence. So we call ``classify_arm_add``
    with ``proposal_confidence="high"`` unconditionally — the
    verdict IS the confidence. Shape guards still apply (only
    style: / transform__ / hook_type: variants of EXISTING dimensions
    auto-accept; new dimensions still need operator review).

    Returns:
        (arm_id, reason) — arm_id is None when no action was taken;
        reason is a short human-readable summary. Fail-open on any DB
        error — never blocks the lifecycle.
    """
    # Lazy import to avoid circular dep: this module already lives in
    # scheduling/, and proposal_auto_accept is a sibling.
    from genlab_core.scheduling.proposal_auto_accept import classify_arm_add

    exp_id = experiment.get("id")
    result = experiment.get("result") or {}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}

    if not (result.get("met_threshold") and result.get("sufficient_samples")):
        return (None, "skip:verdict_not_met_or_low_n")

    spec = experiment.get("spec") or {}
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except Exception:
            spec = {}
    arms = spec.get("arms") or []
    if len(arms) < 2:
        return (None, "skip:not_two_arm_experiment")
    winning_arm = str(arms[1]).strip()
    if not winning_arm:
        return (None, "skip:empty_winning_arm")

    report_id = experiment.get("source_report_id")
    if not report_id:
        return (None, "skip:no_source_report")
    niche_id = experiment.get("niche_id") or spec.get("niche_id") or ""

    # Load the source report's proposals list.
    try:
        row = conn.execute(
            """
            SELECT proposals, COALESCE(proposals_accepted, '[]'::jsonb) AS accepted
            FROM strategist_reports
            WHERE id = %s
            """,
            (report_id,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[auto_experiment] promote: report lookup failed %s: %s",
            exp_id,
            exc,
        )
        return (None, "skip:report_load_error")
    if row is None:
        return (None, "skip:report_missing")

    if hasattr(row, "get"):
        proposals = row.get("proposals") or []
        accepted = row.get("accepted") or []
    else:
        proposals = row[0] or []
        accepted = row[1] or []
    if isinstance(proposals, str):
        try:
            proposals = json.loads(proposals)
        except Exception:
            proposals = []
    if isinstance(accepted, str):
        try:
            accepted = json.loads(accepted)
        except Exception:
            accepted = []
    if not isinstance(proposals, list) or not isinstance(accepted, list):
        return (None, "skip:malformed_proposals_or_accepted")

    # Find the proposal whose proposed.arm_id matches the winning arm.
    target_index: int | None = None
    target_proposal: dict[str, Any] | None = None
    for i, p in enumerate(proposals):
        if not isinstance(p, dict) or p.get("type") != "arm_add":
            continue
        proposed = p.get("proposed") or {}
        if isinstance(proposed, dict) and str(
            proposed.get("arm_id", "")
        ).strip() == winning_arm:
            target_index = i
            target_proposal = p
            break

    if target_index is None or target_proposal is None:
        return (None, f"skip:no_matching_proposal (arm={winning_arm})")

    if target_index in accepted:
        return (None, "skip:already_accepted")

    # Load existing arm_ids for the niche — the classifier needs
    # them to distinguish new-dimension vs variant-of-existing.
    try:
        arm_rows = conn.execute(
            "SELECT arm_id FROM bandit_arms WHERE niche_id = %s",
            (niche_id,),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[auto_experiment] promote: arm_ids lookup failed %s: %s",
            exp_id,
            exc,
        )
        return (None, "skip:arm_ids_load_error")
    existing_arm_ids: frozenset[str] = frozenset(
        str(r.get("arm_id") if hasattr(r, "get") else r[0]) for r in arm_rows
    )

    # Classify with confidence forced to "high" — the empirical
    # verdict is stronger than the strategist tag.
    decision = classify_arm_add(
        target_proposal,
        existing_arm_ids=existing_arm_ids,
        proposal_confidence="high",
    )
    if not decision.should_auto_accept:
        return (None, f"skip:classifier_declined ({decision.reason})")

    # Write the promotion — mirror _append_auto_accepted's shape from
    # scripts/auto_accept_strategist_proposals.py:95-111 so downstream
    # apply_strategist_actions picks it up unchanged.
    try:
        conn.execute(
            """
            UPDATE strategist_reports
            SET proposals_accepted = COALESCE(proposals_accepted, '[]'::jsonb)
                  || %s::jsonb,
                extra = COALESCE(extra, '{}'::jsonb) || jsonb_build_object(
                    'auto_accepted_indices',
                    COALESCE(extra->'auto_accepted_indices', '[]'::jsonb) || %s::jsonb,
                    'verdict_promoted_experiment_ids',
                    COALESCE(extra->'verdict_promoted_experiment_ids', '[]'::jsonb)
                        || %s::jsonb
                )
            WHERE id = %s
            """,
            (
                json.dumps([target_index]),
                json.dumps([target_index]),
                json.dumps([str(exp_id)] if exp_id else []),
                report_id,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[auto_experiment] promote: append_accepted failed %s: %s",
            exp_id,
            exc,
        )
        return (None, "skip:append_write_error")

    return (winning_arm, decision.reason)


__all__ = [
    "DEFAULT_DURATION_DAYS",
    "MIN_SAMPLES_PER_ARM",
    "ExperimentSpec",
    "check_running_experiments",
    "complete_experiment",
    "is_enabled",
    "list_experiments",
    "measure_experiment_result",
    "promote_verdict_to_proposal",
    "queue_pending_experiment",
    "start_pending_experiments",
]
