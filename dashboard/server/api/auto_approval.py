"""Auto-approval gate stats + kill-switch endpoints.

AUTO #1b (2026-06-13): Surfaces the per-niche confusion matrix for the
AutoApprovalGate. Read by the dashboard to show:

- "would auto-approve · 78%" badge per blueprint (via blueprints API)
- agreement-rate card on Mission Control (this module)

D3.10 (2026-06-15, AUTO #2 runbook): Adds a file-backed global kill
switch endpoint so the operator can disable auto-approval from the
dashboard without SSH-editing env vars. The worker checks BOTH the
``GENLAB_AUTO_APPROVE_DISABLED`` env var AND this file flag — either
being set disables auto-approval globally for every niche.

Operators consult this before flipping AUTO #2's opt-in flag. Once a
niche shows ≥30 samples + ≥90% agreement, enforcement becomes
defensible.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("auto_approval_api", __name__, url_prefix="/api/v1/auto-approval")

# Niche IDs the dashboard supports. Whitelisted to prevent SQL injection
# via the niche_id param (defense-in-depth — calibration_logger.stats also
# parameterizes). If a future niche is added, append here.
_VALID_NICHES = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})


@bp.route("/calibration-stats", methods=["GET"])
def calibration_stats():
    """Return the per-niche agreement rate for a rolling window.

    Query params:
        niche_id (required): one of ai_creators, gaming, sports, movies, anime
        window_days (optional, default 7): rolling window size

    Response:
        {
          "niche_id": "gaming",
          "window_days": 7,
          "sample_count": 18,
          "agreement_count": 16,
          "agreement_rate": 0.889,
          "confusion_matrix": {
            "true_positives": 12,
            "true_negatives": 4,
            "false_positives": 1,
            "false_negatives": 1
          },
          "ready_for_enforcement": false  // <30 samples
        }
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id query param required", code=400)
    if niche_id not in _VALID_NICHES:
        return api_error(
            error=f"niche_id must be one of {sorted(_VALID_NICHES)}",
            code=400,
        )

    try:
        window_days = int(request.args.get("window_days", "7"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    try:
        from genlab_core.scheduling.calibration_logger import stats

        s = stats(niche_id=niche_id, window_days=window_days)
    except Exception as exc:
        # 2026-07-14 (dashboard audit F1): return zero-filled stats
        # instead of 500ing. The AutoApprovalCalibrationCard polls
        # every 60s; a 500 during cold-start (table empty, DB blip)
        # renders as error state instead of the natural "0 samples
        # yet" state. Sibling calibration_stats_all at :152-163
        # already uses the zero-filled pattern for cross-niche.
        logger.warning(
            "calibration-stats failed for %s (returning zero-filled): %s",
            niche_id,
            exc,
        )
        return api_success(
            data={
                "niche_id": niche_id,
                "window_days": window_days,
                "sample_count": 0,
                "agreement_count": 0,
                "agreement_rate": 0.0,
                "confusion_matrix": {
                    "true_positives": 0,
                    "true_negatives": 0,
                    "false_positives": 0,
                    "false_negatives": 0,
                },
                "ready_for_enforcement": False,
                "degraded": True,
                "degraded_reason": str(exc)[:200],
            }
        )

    return api_success(
        data={
            "niche_id": s.niche_id,
            "window_days": window_days,
            "sample_count": s.sample_count,
            "agreement_count": s.agreement_count,
            "agreement_rate": round(s.agreement_rate, 3),
            "confusion_matrix": {
                "true_positives": s.true_positives,
                "true_negatives": s.true_negatives,
                "false_positives": s.false_positives,
                "false_negatives": s.false_negatives,
            },
            "ready_for_enforcement": s.ready_for_enforcement,
        }
    )


@bp.route("/calibration-stats-all", methods=["GET"])
def calibration_stats_all():
    """Batch variant — return calibration stats for all 5 niches in ONE query.

    Mission Control's AutoApprovalCalibrationCard previously made 5
    parallel HTTP requests every 60s (one per niche). With this
    endpoint it makes 1 request → 5× reduction in dashboard-driven HTTP
    + SQL load for that card.

    Query params:
        window_days (optional, default 7): rolling window size (1..90)

    Response shape:
        {
          "window_days": 7,
          "niches": {
            "ai_creators": {  ...same shape as /calibration-stats... },
            "gaming": { ... },
            ...
          }
        }

    Niches with zero calibration samples in the window get a zero-filled
    CalibrationStats so the response always has all 5 keys (lets the
    frontend render every row without per-niche missing-data handling).
    """
    try:
        window_days = int(request.args.get("window_days", "7"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    try:
        from genlab_core.scheduling.calibration_logger import (
            CalibrationStats,
            stats_all_niches,
        )

        per_niche = stats_all_niches(window_days=window_days)
    except Exception as exc:
        logger.exception("calibration-stats-all failed")
        return api_error(error=f"Stats query failed: {exc}", code=500)

    # Fill missing niches with zeroed stats so the frontend always
    # has a consistent 5-key response shape.
    out: dict[str, dict] = {}
    for niche_id in _VALID_NICHES:
        s = per_niche.get(
            niche_id,
            CalibrationStats(
                niche_id=niche_id,
                sample_count=0,
                agreement_count=0,
                true_positives=0,
                true_negatives=0,
                false_positives=0,
                false_negatives=0,
            ),
        )
        out[niche_id] = {
            "niche_id": s.niche_id,
            "window_days": window_days,
            "sample_count": s.sample_count,
            "agreement_count": s.agreement_count,
            "agreement_rate": round(s.agreement_rate, 3),
            "confusion_matrix": {
                "true_positives": s.true_positives,
                "true_negatives": s.true_negatives,
                "false_positives": s.false_positives,
                "false_negatives": s.false_negatives,
            },
            "ready_for_enforcement": s.ready_for_enforcement,
            # Phase 1.D (2026-08-14) — rule #22-safe enrollment gate.
            # ready_for_enforcement above is the legacy heuristic
            # (samples + agreement% only) kept for backward compat.
            # enrollment_readiness adds confusion-matrix balance +
            # FN-rate checks so the 2026-07-17 gaming shape can't
            # slip through.
            "enrollment_readiness": s.enrollment_readiness,
            "enrollment_reason": s.readiness_reason,
        }

    return api_success(data={"window_days": window_days, "niches": out})


# ── Outcome-based readiness (2026-07-23) ────────────────────────────
#
# Complementary signal to /calibration-stats. The calibration ratchet
# is stuck 24 days because the operator hasn't clicked review since
# auto-approver started approving blueprints outright. This endpoint
# surfaces an INDEPENDENT signal: for auto-approved blueprints in the
# rolling window, what fraction had reward_48h clear a low bar? A high
# outcome-good rate validates the gate's decisions from real-world
# performance rather than operator agreement.
#
# READ-ONLY. Does NOT write to auto_approval_calibration. The auto-
# approver's advancement ladder currently ignores this signal —
# observability first per CLAUDE.md rollout discipline; operator
# eyeballs the numbers for ~1 week before the flag flip that lets it
# advance the ladder.


@bp.route("/outcome-readiness", methods=["GET"])
def outcome_readiness_endpoint():
    """Per-niche outcome-based readiness verdict.

    Query params:
        niche_id (optional): one of ai_creators, gaming, sports,
            movies, anime. If omitted, returns all 5.
        window_days (optional, default 14): rolling window for
            auto-approved blueprint lookup.

    Response shape (single niche):
        {
          "niche_id": "gaming",
          "window_days": 14,
          "sample_count": 22,
          "outcome_good_count": 18,
          "outcome_good_rate": 0.818,
          "threshold": 0.05,
          "ready": false          // sample_count < 30
        }

    Response shape (all niches — no ``niche_id`` param):
        {
          "window_days": 14,
          "niches": {
            "ai_creators": { ...same shape as single... },
            ...
          }
        }
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(error="DATABASE_URL not configured", code=503)

    try:
        window_days = int(request.args.get("window_days", "14"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    niche_id = (request.args.get("niche_id") or "").strip()
    if niche_id and niche_id not in _VALID_NICHES:
        return api_error(
            error=f"niche_id must be one of {sorted(_VALID_NICHES)}",
            code=400,
        )

    from psycopg.rows import dict_row

    from genlab_core.scheduling.outcome_readiness import (
        check_all_niches,
        check_outcome_readiness,
    )
    from genlab_core.storage.tenant_context import pg_connect

    def _to_dict(r) -> dict:
        return {
            "niche_id": r.niche_id,
            "window_days": r.window_days,
            "sample_count": r.sample_count,
            "outcome_good_count": r.outcome_good_count,
            "outcome_good_rate": round(r.outcome_good_rate, 3),
            "threshold": r.threshold,
            "ready": r.ready,
        }

    try:
        with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
            if niche_id:
                r = check_outcome_readiness(
                    conn, niche_id, window_days=window_days
                )
                return api_success(data=_to_dict(r))
            per_niche = check_all_niches(conn, window_days=window_days)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "outcome-readiness failed for niche=%r window=%d: %s",
            niche_id or "all",
            window_days,
            exc,
        )
        # Fail-open: zero-filled for the requested niche(s) rather
        # than 500 — matches calibration-stats convention.
        if niche_id:
            return api_success(
                data={
                    "niche_id": niche_id,
                    "window_days": window_days,
                    "sample_count": 0,
                    "outcome_good_count": 0,
                    "outcome_good_rate": 0.0,
                    "threshold": 0.05,
                    "ready": False,
                    "degraded": True,
                    "degraded_reason": str(exc)[:200],
                }
            )
        # Cross-niche zero-fill.
        return api_success(
            data={
                "window_days": window_days,
                "niches": {
                    n: {
                        "niche_id": n,
                        "window_days": window_days,
                        "sample_count": 0,
                        "outcome_good_count": 0,
                        "outcome_good_rate": 0.0,
                        "threshold": 0.05,
                        "ready": False,
                    }
                    for n in sorted(_VALID_NICHES)
                },
                "degraded": True,
                "degraded_reason": str(exc)[:200],
            }
        )

    return api_success(
        data={
            "window_days": window_days,
            "niches": {n: _to_dict(r) for n, r in per_niche.items()},
        }
    )


# ── Gate examination breakdown (2026-07-23) ─────────────────────────
#
# READ-ONLY. Aggregates gate_examinations rows so the operator can
# see which of the 5 auto-approval-gate checks (has_video, has_hook,
# qc_passed, composite_score, virality_score) is the constraint per
# niche. Unblocks the AUTO #2 ratchet's tuning problem — currently
# the gate approves ~0/1 blueprints per fire; without knowing which
# check is failing, the operator can't tune the threshold.


@bp.route("/gate-examinations", methods=["GET"])
def gate_examinations_endpoint():
    """Per-niche gate examination breakdown.

    Query params:
        niche_id (optional): filter to one niche. Omit for all 5.
        window_days (optional, default 7): rolling window.

    Response shape (all niches):
        {
          "window_days": 7,
          "niches": {
            "gaming": {
              "niche_id": "gaming",
              "examinations": N,
              "approved": M,
              "rejected": N - M,
              "approval_rate": M / N,
              "distinct_blueprints": K,      // dedupe re-examines
              "failed_check_counts": {
                "composite_score": 8,
                "virality_score": 4,
                ...
              },
              "top_failing_check": "composite_score"
            },
            ...
          }
        }
    """
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return api_error(error="DATABASE_URL not configured", code=503)

    try:
        window_days = int(request.args.get("window_days", "7"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    niche_id = (request.args.get("niche_id") or "").strip()
    if niche_id and niche_id not in _VALID_NICHES:
        return api_error(
            error=f"niche_id must be one of {sorted(_VALID_NICHES)}",
            code=400,
        )

    from psycopg.rows import dict_row

    from genlab_core.storage.tenant_context import pg_connect

    target_niches = [niche_id] if niche_id else sorted(_VALID_NICHES)

    try:
        with pg_connect(dsn, row_factory=dict_row, niche_id="all") as conn:
            out: dict[str, dict] = {}
            for nid in target_niches:
                out[nid] = _one_niche_breakdown(conn, nid, window_days)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "gate-examinations failed niche=%r window=%d: %s",
            niche_id or "all",
            window_days,
            exc,
        )
        # Fail-open zero-fill so the card renders without an error state.
        zero = {
            "examinations": 0,
            "approved": 0,
            "rejected": 0,
            "approval_rate": 0.0,
            "distinct_blueprints": 0,
            "failed_check_counts": {},
            "top_failing_check": None,
        }
        if niche_id:
            return api_success(
                data={
                    "niche_id": niche_id,
                    "window_days": window_days,
                    **zero,
                    "degraded": True,
                    "degraded_reason": str(exc)[:200],
                }
            )
        return api_success(
            data={
                "window_days": window_days,
                "niches": {
                    n: {"niche_id": n, "window_days": window_days, **zero}
                    for n in target_niches
                },
                "degraded": True,
                "degraded_reason": str(exc)[:200],
            }
        )

    if niche_id:
        return api_success(
            data={"niche_id": niche_id, "window_days": window_days, **out[niche_id]}
        )
    return api_success(
        data={
            "window_days": window_days,
            "niches": {
                n: {"niche_id": n, "window_days": window_days, **v}
                for n, v in out.items()
            },
        }
    )


def _one_niche_breakdown(conn, niche_id: str, window_days: int) -> dict:
    """Compute the aggregation for one niche. Split out so the
    endpoint stays a thin router."""
    # Top-line counts.
    row = conn.execute(
        """
        SELECT
            COUNT(*)::int AS examinations,
            COUNT(*) FILTER (WHERE approved = true)::int AS approved,
            COUNT(DISTINCT blueprint_id)::int AS distinct_blueprints
        FROM gate_examinations
        WHERE niche_id = %s
          AND examined_at > NOW() - make_interval(days => %s)
        """,
        (niche_id, window_days),
    ).fetchone()
    exam = int(row.get("examinations") or 0) if hasattr(row, "get") else int(row[0] or 0)
    approved = int(row.get("approved") or 0) if hasattr(row, "get") else int(row[1] or 0)
    distinct_bp = (
        int(row.get("distinct_blueprints") or 0)
        if hasattr(row, "get")
        else int(row[2] or 0)
    )

    # Failed check tally — jsonb_array_elements_text unpivots the
    # failed_checks arrays across all rejected rows, then GROUP BY
    # counts occurrences. Only rejected rows contribute; the top
    # failing check IS the ratchet's tuning target.
    check_rows = conn.execute(
        """
        SELECT check_name, COUNT(*)::int AS n
        FROM gate_examinations,
             jsonb_array_elements_text(failed_checks) AS check_name
        WHERE niche_id = %s
          AND approved = false
          AND examined_at > NOW() - make_interval(days => %s)
        GROUP BY check_name
        ORDER BY n DESC
        """,
        (niche_id, window_days),
    ).fetchall()
    failed_check_counts: dict[str, int] = {}
    for r in check_rows:
        name = r.get("check_name") if hasattr(r, "get") else r[0]
        n = int(r.get("n") or 0) if hasattr(r, "get") else int(r[1] or 0)
        if name:
            failed_check_counts[str(name)] = n

    top_check = next(iter(failed_check_counts), None)
    rejected = exam - approved

    # Percentile distribution + absolute-unlock estimate for the top
    # failing check's raw values. Operator's actual question is not
    # "what % of the failing set" but "how many more approvals per
    # week would this get me". The former can be 75% of 2 blueprints
    # (meaningless); the latter is a concrete number denominated in
    # blueprints published.
    threshold_suggestion = None
    score_distribution = None
    if top_check in ("composite_score", "virality_score"):
        score_distribution = _score_distribution(
            conn, niche_id, window_days, top_check
        )
        if score_distribution and score_distribution.get("p25") is not None:
            current = _CURRENT_GATE_THRESHOLDS.get(top_check)
            p25 = score_distribution["p25"]
            # Suggest ONE tick below p25 so blueprints exactly at p25
            # pass. Round to 3 decimals matching the score precision.
            suggested = round(p25 - 0.001, 3)

            # Absolute count: rejected rows where extra->>{check} is
            # >= suggested_threshold. Reuses the same table so no
            # cross-join to blueprints needed.
            unlock_count = _count_unlockable_at_threshold(
                conn, niche_id, window_days, top_check, suggested
            )
            # Extrapolate to weekly rate.
            weekly_estimate = (
                round(unlock_count * 7.0 / max(window_days, 1), 1)
                if unlock_count is not None
                else None
            )

            # Confidence based on distribution sample size — the p25
            # estimate is only stable at sufficient n. Below 5 the
            # percentile IS the min IS the max in practice.
            n = score_distribution["n"]
            if n >= 10:
                confidence = "high"
            elif n >= 5:
                confidence = "medium"
            else:
                confidence = "low"

            threshold_suggestion = {
                "check": top_check,
                "current_threshold": current,
                "suggested_threshold": suggested,
                "would_unlock_count": unlock_count,
                "weekly_unlock_estimate": weekly_estimate,
                "confidence": confidence,
                "n_samples": n,
                "rationale": (
                    f"p25 of {top_check} values across {n} rejected "
                    f"blueprints. Setting threshold to {suggested} would "
                    f"unlock {unlock_count or 0} blueprints in the last "
                    f"{window_days}d "
                    f"({'~%.1f/wk' % weekly_estimate if weekly_estimate else 'unknown/wk'})."
                ),
            }

    rate = (approved / exam) if exam > 0 else 0.0
    return {
        "examinations": exam,
        "approved": approved,
        "rejected": rejected,
        "approval_rate": round(rate, 3),
        "distinct_blueprints": distinct_bp,
        "failed_check_counts": failed_check_counts,
        "top_failing_check": top_check,
        "score_distribution": score_distribution,
        "threshold_suggestion": threshold_suggestion,
    }


# Mirror of the gate's hardcoded thresholds — see
# ``genlab_core/scheduling/auto_approval_gate.py:evaluate``. Mirrored
# here to avoid an import into the endpoint's hot path AND to keep the
# response payload self-contained. If gate thresholds move, both this
# and the gate module must update.
_CURRENT_GATE_THRESHOLDS: dict[str, float] = {
    "composite_score": 0.3,
    "virality_score": 0.05,
}


def _score_distribution(
    conn, niche_id: str, window_days: int, check_name: str
) -> dict | None:
    """Return {min, p25, p50, p75, max, n} for a numeric check's
    values in gate_examinations.extra, restricted to rejected rows
    that failed THIS specific check. Returns None when no rejected
    rows carry the value."""
    # extra->>{check_name} → text, then ::float. Filter on failed_checks
    # containing the check name so we only aggregate the relevant subset.
    # ``failed_checks @> to_jsonb(ARRAY[%s])`` is the JSONB "contains"
    # operator — fast when the failed_checks JSONB has a GIN index in
    # the future; for now sequential scan is fine at this cardinality.
    try:
        row = conn.execute(
            f"""
            SELECT
                MIN((extra->>%s)::float) AS min_v,
                PERCENTILE_CONT(0.25) WITHIN GROUP (
                    ORDER BY (extra->>%s)::float
                ) AS p25,
                PERCENTILE_CONT(0.50) WITHIN GROUP (
                    ORDER BY (extra->>%s)::float
                ) AS p50,
                PERCENTILE_CONT(0.75) WITHIN GROUP (
                    ORDER BY (extra->>%s)::float
                ) AS p75,
                MAX((extra->>%s)::float) AS max_v,
                COUNT(*)::int AS n
            FROM gate_examinations
            WHERE niche_id = %s
              AND approved = false
              AND examined_at > NOW() - make_interval(days => %s)
              AND failed_checks @> to_jsonb(ARRAY[%s]::text[])
              AND (extra->>%s) IS NOT NULL
            """,
            (
                check_name, check_name, check_name, check_name, check_name,
                niche_id, window_days, check_name, check_name,
            ),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "gate-examinations: score dist query failed check=%s niche=%s: %s",
            check_name, niche_id, exc,
        )
        return None
    if row is None:
        return None
    getval = (lambda k: row.get(k)) if hasattr(row, "get") else (
        lambda k: row[["min_v", "p25", "p50", "p75", "max_v", "n"].index(k)]
    )
    n = int(getval("n") or 0)
    if n == 0:
        return None
    return {
        "check": check_name,
        "min": _r3(getval("min_v")),
        "p25": _r3(getval("p25")),
        "p50": _r3(getval("p50")),
        "p75": _r3(getval("p75")),
        "max": _r3(getval("max_v")),
        "n": n,
    }


def _r3(v):
    return None if v is None else round(float(v), 3)


def _count_unlockable_at_threshold(
    conn, niche_id: str, window_days: int, check_name: str, threshold: float
) -> int | None:
    """Count rejected gate_examinations rows whose extra.{check_name}
    is >= the given threshold. That's the concrete "how many more
    approvals" number — decision-relevant vs "% of failing set"."""
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*)::int AS n
            FROM gate_examinations
            WHERE niche_id = %s
              AND approved = false
              AND examined_at > NOW() - make_interval(days => %s)
              AND failed_checks @> to_jsonb(ARRAY[%s]::text[])
              AND (extra->>%s) IS NOT NULL
              AND (extra->>%s)::float >= %s
            """,
            (
                niche_id, window_days, check_name, check_name, check_name,
                float(threshold),
            ),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "gate-examinations: unlock count query failed check=%s niche=%s: %s",
            check_name, niche_id, exc,
        )
        return None
    if row is None:
        return 0
    return int(row.get("n") or 0) if hasattr(row, "get") else int(row[0] or 0)


# ── Lever B: Per-rejection-reason breakdown ─────────────────────────
#
# Operator clicks 1 of 6 categorical rejection reasons on every reject:
# ``weak_hook``, ``too_generic``, ``unsupported_claim``, ``bad_fit``,
# ``too_long``, ``low_value`` (plus the default-label fallbacks
# ``rejected_in_review`` / ``needs_revision``). Pre-Lever-B these were
# stored on ``blueprints.feedback_issue`` but never surfaced for
# analysis — no endpoint read them.
#
# This endpoint groups operator-rejected calibration rows by category
# and reports gate-agreement counts per group. The actionable signal:
# "of the times the operator rejected with reason X, how often did the
# gate already say no?" lets operators see WHICH gate signals to tune.
# E.g. a high "gate disagreed" count on weak_hook rejections means the
# gate's existing hook check is too lenient.


@bp.route("/rejection-breakdown", methods=["GET"])
def rejection_breakdown():
    """Per-rejection-reason breakdown of operator-rejected blueprints.

    Query params:
        niche_id (required): one of the 5 supported niches
        window_days (optional, default 30): rolling window size (1..90)

    Response:
        {
          "niche_id": "gaming",
          "window_days": 30,
          "categories": [
            {
              "feedback_category": "weak_hook",
              "count": 24,
              "gate_agreed": 4,
              "gate_disagreed": 20,
              "gate_disagreement_rate": 0.833
            },
            { "feedback_category": "too_generic", ... },
            ...
          ]
        }

    Empty ``categories`` list means no rejections-with-category in the
    window — either cold start (Lever B just shipped, no operator clicks
    yet had the field populated) OR a healthy niche with low reject
    volume. Frontend should render "no data yet" rather than an error.
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id query param required", code=400)
    if niche_id not in _VALID_NICHES:
        return api_error(
            error=f"niche_id must be one of {sorted(_VALID_NICHES)}",
            code=400,
        )

    try:
        window_days = int(request.args.get("window_days", "30"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    try:
        from genlab_core.scheduling.calibration_logger import breakdown_by_category

        entries = breakdown_by_category(niche_id=niche_id, window_days=window_days)
    except Exception as exc:
        logger.exception("rejection-breakdown failed for %s", niche_id)
        return api_error(error=f"Breakdown query failed: {exc}", code=500)

    return api_success(
        data={
            "niche_id": niche_id,
            "window_days": window_days,
            "categories": [
                {
                    "feedback_category": e.feedback_category,
                    "count": e.count,
                    "gate_agreed": e.gate_agreed,
                    "gate_disagreed": e.gate_disagreed,
                    # Rate of operator-vs-gate disagreement WITHIN this
                    # category. Computed server-side so the frontend
                    # gets a stable, rounded value.
                    "gate_disagreement_rate": (
                        round(e.gate_disagreed / e.count, 3) if e.count > 0 else 0.0
                    ),
                }
                for e in entries
            ],
        }
    )


# ── W4.4: Track-record endpoint (per-day agreement trend) ─────────────
#
# Closes W4.4 from the autonomy plan. ``calibration-stats`` returns a
# single-window snapshot — useful for the Day-8 readiness check but
# blind to TRENDS. If a niche's agreement rate is climbing day over
# day, the operator should see that signal earlier. If it's regressing,
# even earlier. This endpoint returns per-day bins so the dashboard
# can sparkline the trend.
#
# Mirrors calibration-stats' validation surface (whitelisted niche_id,
# 1..90 day window). Adds bin_days for granularity control — typically
# 1 (daily) but operators may want 7 (weekly) for a 90-day view.


@bp.route("/track-record", methods=["GET"])
def track_record():
    """Per-day (or per-bin) agreement-rate trend for a niche.

    Query params:
        niche_id (required): one of the 5 valid niches
        window_days (optional, default 30): rolling window size
        bin_days (optional, default 1): bin size — 1 = daily, 7 = weekly

    Response:
        {
          "niche_id": "gaming",
          "window_days": 30,
          "bin_days": 1,
          "bins": [
            {"date": "2026-05-20", "sample_count": 5, "agreement": 4,
             "rate": 0.80},
            ...
          ],
          "overall": {"sample_count": 105, "agreement": 95, "rate": 0.905}
        }

    Returns an empty bins list when no calibration data exists in
    the window — frontend renders a "no data yet" message.
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id query param required", code=400)
    if niche_id not in _VALID_NICHES:
        return api_error(
            error=f"niche_id must be one of {sorted(_VALID_NICHES)}",
            code=400,
        )

    try:
        window_days = int(request.args.get("window_days", "30"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    try:
        bin_days = int(request.args.get("bin_days", "1"))
    except (TypeError, ValueError):
        return api_error(error="bin_days must be an integer", code=400)
    if bin_days < 1 or bin_days > window_days:
        return api_error(error=f"bin_days must be 1..{window_days}", code=400)

    try:
        result = _compute_track_record(niche_id, window_days, bin_days)
    except _TrackRecordError as exc:
        return api_error(error=str(exc), code=exc.code)
    except Exception as exc:
        logger.exception("track-record failed for %s", niche_id)
        return api_error(error=f"Track-record query failed: {exc}", code=500)
    return api_success(data=result)


class _TrackRecordError(Exception):
    """Raised by `_compute_track_record` for caller-facing failures.

    `code` mirrors api_error()'s HTTP code arg so the route handler
    can surface 503 (no DB), 500 (unexpected DB error), etc. consistently
    whether called from the single-niche or batch endpoint.
    """

    def __init__(self, message: str, code: int = 500) -> None:
        super().__init__(message)
        self.code = code


def _compute_track_record(niche_id: str, window_days: int, bin_days: int) -> dict:
    """Compute the per-day (or per-bin) agreement-rate trend for ``niche_id``.

    Extracted from the route handler so both ``/track-record`` (per-niche)
    and ``/track-record-all`` (batch over 5 niches) can share the binning
    logic — they only differ in how many niches they loop over.

    Returns the dict the route would have wrapped in api_success(). Raises
    :class:`_TrackRecordError` for caller-facing failures (DB unreachable
    etc.) so both call sites surface the same error semantics.
    """
    try:
        import os

        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            raise _TrackRecordError("DATABASE_URL not configured", code=503)

        # Bin SQL: floor((NOW() - decided_at) / bin_days) gives the
        # bin index; date_trunc + interval would also work but the
        # arithmetic stays explicit so an operator can re-run it by
        # hand to sanity-check.
        # SR-A/C/D Tier-4 (2026-06-17): pg_connect sets app.niche_id
        # automatically, so the manual ``SET app.niche_id`` is removed.
        from genlab_core.storage.tenant_context import pg_connect

        with pg_connect(dsn, connect_timeout=5, row_factory=dict_row, niche_id=niche_id) as conn:
            # Agreement = TP + TN per the existing calibration_logger
            # convention. Aligns with calibration-stats' agreement_count.
            # Note: gate-vs-gate filtering happens at INSERT time in
            # ``calibration_logger.log()`` (S1 fix, 2026-06-15) — rows
            # whose ``action_taken_source`` matches an auto-approver
            # tag are simply never written. So no WHERE-clause filter
            # is needed here, and the table schema doesn't carry an
            # ``action_taken_source`` column at all (caught at deploy
            # 2026-06-17 evening when the prod schema diverged from a
            # docstring assumption).
            rows = conn.execute(
                """
                SELECT
                    (decided_at::date) AS day,
                    COUNT(*) AS sample_count,
                    COUNT(*) FILTER (
                        WHERE (gate_approved = true AND operator_action = 'approved')
                           OR (gate_approved = false AND operator_action IN
                                ('rejected', 'revised', 'skipped'))
                    ) AS agreement_count
                FROM auto_approval_calibration
                WHERE niche_id = %s
                  AND decided_at > NOW() - (%s || ' days')::interval
                GROUP BY 1
                ORDER BY 1
                """,
                (niche_id, window_days),
            ).fetchall()

            # W3 (2026-06-18): engagement signal per day. JOINs the
            # ``pending_feedback`` table via ``blueprints.candidate_id``
            # (NOT ``blueprints.id`` — pending_feedback's task_id
            # prefix is the candidate sha256 hash, not the blueprint
            # UUID; verified via direct DB query). LEFT JOIN so days
            # with no engagement-data still appear (zero rewards).
            #
            # Two metrics per day:
            #   * ``collected_count`` — how many pending_feedback rows
            #     across all platforms+blueprints have populated
            #     reward_48h (i.e. the 48h engagement window closed
            #     and Insights wrote back)
            #   * ``avg_reward_48h`` — mean across collected rows;
            #     NULL when collected_count = 0
            #
            # Operator value: pair calibration agreement (gate vs
            # operator alignment) with post-publish reality (did the
            # auto-approved posts actually earn engagement). A day
            # with high agreement BUT low avg_reward means the gate
            # +operator are aligned on garbage; a day with high
            # agreement AND high avg_reward means alignment-on-good.
            engagement_rows = conn.execute(
                """
                SELECT
                    (aac.decided_at::date) AS day,
                    COUNT(DISTINCT pf.id) AS collected_count,
                    ROUND(AVG(pf.reward_48h)::numeric, 4) AS avg_reward_48h
                FROM auto_approval_calibration aac
                LEFT JOIN blueprints b ON b.id::text = aac.blueprint_id
                LEFT JOIN pending_feedback pf
                       ON pf.task_id LIKE b.candidate_id || '__%%'
                      AND pf.niche_id = aac.niche_id
                      AND pf.reward_48h IS NOT NULL
                WHERE aac.niche_id = %s
                  AND aac.decided_at > NOW() - (%s || ' days')::interval
                GROUP BY 1
                """,
                (niche_id, window_days),
            ).fetchall()
            engagement_by_day = {
                r["day"]: {
                    "collected_count": int(r["collected_count"] or 0),
                    "avg_reward_48h": float(r["avg_reward_48h"])
                    if r["avg_reward_48h"] is not None
                    else None,
                }
                for r in engagement_rows
            }

        # Optionally re-bin into bin_days groups. Default bin_days=1
        # means "one bin per day"; bin_days=7 groups by week.
        def _eng_for_day(day_key):
            """Pull engagement metrics for a day key; default to empty
            when no calibration data ever fired for that day."""
            return engagement_by_day.get(day_key, {"collected_count": 0, "avg_reward_48h": None})

        if bin_days == 1:
            bins = []
            for r in rows:
                eng = _eng_for_day(r["day"])
                bins.append(
                    {
                        "date": r["day"].isoformat(),
                        "sample_count": int(r["sample_count"]),
                        "agreement": int(r["agreement_count"]),
                        "rate": round(r["agreement_count"] / r["sample_count"], 3)
                        if r["sample_count"]
                        else 0.0,
                        # W3 (2026-06-18): engagement enrichment
                        "collected_count": eng["collected_count"],
                        "avg_reward_48h": eng["avg_reward_48h"],
                    }
                )
        else:
            # Group consecutive days into bin_days-sized buckets.
            # We bin from the most-recent day backwards so the latest
            # bin always reflects the latest data even when the window
            # isn't a clean multiple of bin_days.
            from datetime import timedelta

            day_to_row = {r["day"]: r for r in rows}
            bins = []
            if rows:
                today = max(day_to_row)
                cursor = today
                while True:
                    bin_end = cursor
                    bin_start = bin_end - timedelta(days=bin_days - 1)
                    samples = 0
                    agreements = 0
                    # W3: accumulate engagement across the bin's days.
                    # avg_reward_48h is a weighted average across the
                    # days that HAD collected rewards (each day's
                    # contribution = day_collected * day_avg).
                    bin_collected = 0
                    bin_reward_sum = 0.0
                    d = bin_start
                    while d <= bin_end:
                        if d in day_to_row:
                            samples += int(day_to_row[d]["sample_count"])
                            agreements += int(day_to_row[d]["agreement_count"])
                        eng_d = _eng_for_day(d)
                        if eng_d["collected_count"] and eng_d["avg_reward_48h"] is not None:
                            bin_collected += eng_d["collected_count"]
                            bin_reward_sum += eng_d["collected_count"] * eng_d["avg_reward_48h"]
                        d += timedelta(days=1)
                    if samples:
                        bins.append(
                            {
                                "date": bin_end.isoformat(),
                                "sample_count": samples,
                                "agreement": agreements,
                                "rate": round(agreements / samples, 3),
                                "collected_count": bin_collected,
                                "avg_reward_48h": round(bin_reward_sum / bin_collected, 4)
                                if bin_collected
                                else None,
                            }
                        )
                    cursor = bin_start - timedelta(days=1)
                    if cursor < min(day_to_row):
                        break
                bins.reverse()  # chronological order

        total_samples = sum(b["sample_count"] for b in bins)
        total_agreement = sum(b["agreement"] for b in bins)
        # W3 engagement rollup across the whole window. Weighted-avg
        # (sum_of_rewards / total_collected) matches per-bin math.
        total_collected = sum(b.get("collected_count", 0) for b in bins)
        total_reward_sum = sum(
            b["collected_count"] * b["avg_reward_48h"]
            for b in bins
            if b.get("collected_count") and b.get("avg_reward_48h") is not None
        )
        overall = {
            "sample_count": total_samples,
            "agreement": total_agreement,
            "rate": round(total_agreement / total_samples, 3) if total_samples else 0.0,
            "collected_count": total_collected,
            "avg_reward_48h": round(total_reward_sum / total_collected, 4)
            if total_collected
            else None,
        }

        return {
            "niche_id": niche_id,
            "window_days": window_days,
            "bin_days": bin_days,
            "bins": bins,
            "overall": overall,
        }

    except _TrackRecordError:
        raise  # bubble caller-facing failures up to the route handler
    except Exception as exc:
        logger.exception("_compute_track_record failed for %s", niche_id)
        raise _TrackRecordError(f"Track-record query failed: {exc}", code=500) from exc


@bp.route("/track-record-all", methods=["GET"])
def track_record_all():
    """Batch variant — return track-record for ALL 5 niches in one HTTP request.

    Mission Control's TrackRecordCard previously made 5 parallel HTTP
    requests every 60s (one per niche). With this endpoint it collapses
    to 1 request → 5× reduction in dashboard-driven HTTP fan-out.

    Server-side still runs 5 sequential queries (the per-niche binning
    logic isn't trivially SQL-batchable), but eliminates 4 HTTP round-trips +
    4 Flask request-dispatch overheads per dashboard poll. Net effect on
    the user: a single longer response instead of 5 short ones — total
    user-perceived latency drops modestly, HTTP fan-out drops sharply.

    Query params:
        window_days (optional, default 30): rolling window (1..90)
        bin_days    (optional, default 1):  bin size (1..window_days)

    Response shape:
        {
          "window_days": 30,
          "bin_days": 1,
          "niches": {
            "ai_creators": {  ...same shape as /track-record... },
            "gaming":      { ... },
            ...
          }
        }
    """
    try:
        window_days = int(request.args.get("window_days", "30"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    try:
        bin_days = int(request.args.get("bin_days", "1"))
    except (TypeError, ValueError):
        return api_error(error="bin_days must be an integer", code=400)
    if bin_days < 1 or bin_days > window_days:
        return api_error(error=f"bin_days must be 1..{window_days}", code=400)

    out: dict[str, dict] = {}
    for niche_id in _VALID_NICHES:
        try:
            out[niche_id] = _compute_track_record(niche_id, window_days, bin_days)
        except _TrackRecordError as exc:
            # One niche failing shouldn't black out the whole card.
            # Surface an empty-shaped result for that niche; the card
            # renders "no data" for it while other niches still load.
            logger.warning(
                "track-record-all: %s failed (%s) — returning empty shape",
                niche_id,
                exc,
            )
            out[niche_id] = {
                "niche_id": niche_id,
                "window_days": window_days,
                "bin_days": bin_days,
                "bins": [],
                "overall": {
                    "sample_count": 0,
                    "agreement": 0,
                    "rate": 0.0,
                    "collected_count": 0,
                    "avg_reward_48h": None,
                },
            }

    return api_success(
        data={
            "window_days": window_days,
            "bin_days": bin_days,
            "niches": out,
        }
    )


# ── D3.10: Global kill-switch endpoint ────────────────────────────────
#
# Tiny file-backed flag the auto_approver worker checks before doing
# anything. Two sources of truth combined:
#   - GENLAB_AUTO_APPROVE_DISABLED env var (operator can set via shell
#     during an incident — survives until the worker restarts)
#   - file at $GENLAB_AUTO_APPROVE_KILL_FILE or
#     /opt/genlab/.runtime/auto_approve_kill_switch (default)
#
# Either being set disables auto-approval globally. The dashboard
# button writes/removes the file; env var is shell-only.


def _kill_switch_file_path() -> Path:
    """Resolve the file flag path — mirrors auto_approver._kill_switch_file_path."""
    override = os.environ.get("GENLAB_AUTO_APPROVE_KILL_FILE", "").strip()
    return Path(override) if override else Path("/opt/genlab/.runtime/auto_approve_kill_switch")


def _read_kill_switch_state() -> dict:
    """Return the current global kill-switch state.

    Mirrors auto_approver._kill_switch_active so dashboard + worker
    can never disagree about what "active" means.
    """
    env_set = os.environ.get("GENLAB_AUTO_APPROVE_DISABLED", "").strip() not in (
        "",
        "0",
        "false",
        "False",
    )
    file_path = _kill_switch_file_path()
    file_set = file_path.is_file()
    if env_set and file_set:
        source = "both"
    elif env_set:
        source = "env"
    elif file_set:
        source = "file"
    else:
        source = "none"
    return {
        "active": env_set or file_set,
        "source": source,
        "file_path": str(file_path),
        # Surface env-var separately so the UI can warn "env var also
        # set — only SSH can clear that".
        "env_var_set": env_set,
    }


@bp.route("/kill-switch", methods=["GET"])
def get_kill_switch():
    """Read current global kill-switch state."""
    return api_success(data=_read_kill_switch_state())


@bp.route("/kill-switch", methods=["POST"])
def set_kill_switch():
    """Flip the file-backed kill switch on or off.

    Body: ``{"active": true}`` to disable auto-approval globally,
    ``{"active": false}`` to re-enable. The env var ``GENLAB_AUTO_APPROVE_DISABLED``
    is NOT touched — that's set via shell on the worker host and
    survives restarts independently. The UI must surface that if it
    is set, this endpoint cannot fully clear it.
    """
    # 2026-07-14 (dashboard audit F10): distinguish "Content-Type
    # missing" from "'active' missing from body". get_json(silent=True)
    # returns None on either — but the second case is a real client
    # error, the first is a client misconfiguration deserving a
    # specific 415 message.
    if not request.is_json:
        return api_error(error="Content-Type must be application/json", code=415)
    body = request.get_json(silent=True) or {}
    if "active" not in body:
        return api_error(error="body must include 'active' boolean", code=400)
    if not isinstance(body["active"], bool):
        return api_error(error="'active' must be a boolean", code=400)

    file_path = _kill_switch_file_path()
    try:
        if body["active"]:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "Auto-approval globally disabled via dashboard kill-switch.\n"
                "Delete this file (or POST kill-switch active=false) to re-enable.\n"
            )
            logger.warning("[kill-switch] DISABLED via dashboard — file=%s", file_path)
        else:
            if file_path.exists():
                file_path.unlink()
                logger.warning("[kill-switch] RE-ENABLED via dashboard — file=%s", file_path)
            # else: idempotent — already off
    except OSError as exc:
        logger.exception("[kill-switch] file op failed")
        return api_error(error=f"file op failed: {exc}", code=500)

    return api_success(data=_read_kill_switch_state())
