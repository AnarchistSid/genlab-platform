"""Shared helper for calibration logging from operator-action paths.

Extracted 2026-06-15 (AUTO #2 step S2) to give every operator-action
endpoint a single line to log calibration data, so that the
bulk-review (PR #192), batch-review (api/blueprints.py:872), single
approve-and-schedule (api/blueprints.py:908), and archive
(api/publishing_queue.py:230) paths no longer bypass
calibration_logger.

Round-3 audit finding (S2): 4 paths updated blueprints.action_taken
without calling calibration_logger. As operator productivity goes up
(bulk-review is 15x throughput per [[session-2026-06-14-captioned-mp4-cleanup]])
calibration data accumulates SLOWER because the most-used paths
silently skip logging. Net effect: AUTO #2 readiness threshold
(≥30 samples × ≥90% agreement) drifts further out of reach as the
operator gets better at their job.

The helper:

  1. Re-fetches the blueprint via the passed BacklogClient
  2. Builds the gate's ``extra`` wrapper (mirrors PR #221's
     auto_approver fix + dashboard preview endpoint)
  3. Runs the real gate via
     ``genlab_core.scheduling.auto_approval_gate.evaluate``
  4. Calls ``calibration_logger.log()`` with the operator's action
     + the prior ``action_taken_source`` (so S1's gate-vs-gate guard
     fires correctly)
  5. Catches + swallows ALL exceptions — calibration must NEVER
     block the operator-action path

The 4 endpoints just import + call ``log_calibration_for_action`` —
no duplication of the wrapper-build + gate-eval + logger glue.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Map dashboard action terms to canonical calibration_logger
# operator_action values. "archived" doesn't map directly — the
# closest semantic equivalent is "rejected" (operator explicitly
# moved the blueprint out of the active queue). Mapping here so the
# call sites stay shape-agnostic.
_ACTION_ALIAS: dict[str, str] = {
    "archived": "rejected",
    # Hold/release/approve via the Publishing Queue endpoints
    # (api/publishing_queue.py) map to the same canonical actions —
    # operator's intent matches the verb regardless of which UI
    # surface emitted it.
    "held": "rejected",
    "released": "skipped",
}


def _safe_visual_paths(raw: Any) -> list:
    """Decode a ``visual_paths`` field into a list.

    The Postgres path stores ``visual_paths`` as a JSON-encoded
    string for many blueprints; the gate's ``has_video`` check
    relies on `bool(visual_paths)`. A raw string `"[]"` is truthy
    but represents an empty list — gate then reports `has_video=
    True` when the post actually has no video. PR #221's auto_approver
    fix uses ``_safe_json_list``; the dashboard preview endpoint uses
    ``_safe_json_parse``. This helper is the third copy — kept here
    because the calibration_helper module is the natural shared layer.

    Returns [] on any decode failure — never raises.
    """
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
            return decoded if isinstance(decoded, list) else []
        except (ValueError, TypeError):
            return []
    return []


def log_calibration_for_action(
    *,
    client: Any,
    record_id: str,
    action: str,
) -> None:
    """Log a calibration row for an operator action. Best-effort,
    swallows ALL exceptions so calibration NEVER blocks the caller.

    Args:
        client: A live BacklogClient (sync). Used to re-fetch the
            blueprint for the gate's evaluation.
        record_id: The blueprint id the operator acted on.
        action: The operator's action. Common values: "approved",
            "rejected", "revised", "skipped". "archived" is
            translated to "rejected" via ``_ACTION_ALIAS``.
    """
    try:
        from genlab_core.scheduling import calibration_logger
        from genlab_core.scheduling.auto_approval_gate import evaluate as gate_evaluate

        # Re-fetch the blueprint to get the shape evaluate() expects.
        bp = client.blueprints.get(record_id)
        fields = bp.get("fields", {}) if isinstance(bp, dict) else {}
        flat = {"id": bp.get("id"), **fields}
        niche_id = (flat.get("niche_id") or "").strip()

        # Build the gate's `extra` wrapper if missing — same pattern as
        # the dashboard preview endpoint + auto_approver (PR #221) +
        # the backfill script (PR #224). All 4 sites must stay
        # aligned; a new score field added to the gate needs to land
        # in all 4.
        #
        # 2026-06-15 audit fix: visual_paths must be JSON-decoded
        # (Postgres path stores it as a string). Without the decode,
        # `bool("[]")` is True so the gate reports `has_video=True`
        # even for video-less blueprints — calibration verdict
        # disagrees with what the worker would decide, defeating the
        # whole purpose of S2.
        if not isinstance(flat.get("extra"), dict):
            flat["extra"] = {
                "visual_paths": _safe_visual_paths(flat.get("visual_paths")),
                "composite_score": flat.get("composite_score"),
                "virality_score": flat.get("virality_score"),
                "validation_status": flat.get("validation_status"),
            }

        try:
            decision = gate_evaluate(flat)
        except Exception as ev_exc:
            logger.warning(
                "[calibration] gate evaluation failed — calibration row "
                "logged with decision=None, breaking the auto-approver "
                "confusion matrix (rule #22): %s",
                ev_exc,
                exc_info=True,
            )
            decision = None

        # Translate dashboard action vocabulary to calibration_logger's
        # canonical operator_action values.
        operator_action = _ACTION_ALIAS.get(action, action)

        action_source = (flat.get("action_taken_source") or "").strip() or None

        # Engine 1.4 (2026-06-26): for bulk-review / archive / queue-hold
        # operator paths, no UI dropdown is shown — so the operator can't
        # pick a feedback_category. When the canonical action resolves to
        # "rejected", ask Haiku to auto-classify so the calibration row
        # still carries the categorical signal that downstream consumers
        # (rejection-breakdown endpoint, Engine 2.4 rubric synthesis)
        # depend on. Opt-in via GENLAB_RATIONALE_CLASSIFIER_ENABLED;
        # default OFF so legacy behaviour (NULL category) is preserved.
        # Fail-OPEN: any error leaves feedback_category None.
        feedback_category: str | None = None
        if operator_action == "rejected":
            try:
                from genlab_core.learning.rationale_classifier import (
                    UNCATEGORIZED as _RC_UNCATEGORIZED,
                )
                from genlab_core.learning.rationale_classifier import (
                    classify_rejection as _rc_classify,
                )

                auto_category, _auto_conf = _rc_classify(flat, niche_id)
                if auto_category != _RC_UNCATEGORIZED:
                    feedback_category = auto_category
            except Exception as rc_exc:  # noqa: BLE001 — never block caller
                logger.debug("[rationale] auto-classify skipped: %s", rc_exc)

        calibration_logger.log(
            blueprint_id=record_id,
            niche_id=niche_id,
            decision=decision,
            operator_action=operator_action,
            action_taken_source=action_source,
            feedback_category=feedback_category,
        )

        # Per-post decision trace wire-point #2 (2026-06-30): mirror the
        # (gate verdict, operator action) pair into post_decision_trace
        # so downstream analysis reads from ONE table instead of joining
        # auto_approval_calibration + blueprints + publishing_analytics.
        # Strictly additive — the existing calibration_logger.log call
        # above remains the source of truth for AUTO #2 readiness; this
        # is the same data shaped for trace-table consumers. Fail-OPEN
        # so trace failures NEVER block operator review.
        try:
            from genlab_core.learning.post_decision_trace import (
                record_operator_decision,
            )

            record_operator_decision(
                blueprint_id=record_id,
                niche_id=niche_id,
                gate_verdict=(decision.approved if decision is not None else None),
                gate_confidence=(decision.confidence if decision is not None else None),
                operator_action=operator_action,
            )
        except Exception as trace_exc:  # noqa: BLE001 — never block caller
            logger.warning(
                "[trace] operator-decision wire skipped — post_decision_trace "
                "row lost (2026-07-16 audit flagged 99 orphaned trace rows; "
                "silent-fail here compounds that class-of-bug): %s",
                trace_exc,
                exc_info=True,
            )
    except Exception as cal_exc:  # noqa: BLE001 — never block caller
        # 2026-07-19: elevated DEBUG → WARNING. Same class-of-bug that
        # rule #19 was written from (`review_server.py:1443` masked
        # 17 days of calibration_logger failures 2026-06-29 → 2026-07-16).
        # When calibration_helper was extracted from review_server.py,
        # the OUTER swallow was reintroduced at DEBUG. This is a
        # regression of the rule #19 origin site. Elevated to WARNING
        # + exc_info=True so the underlying failure is visible before
        # another 17-day silent stall accumulates.
        logger.warning(
            "[calibration] skipped (non-fatal) — auto-approver readiness "
            "ratchet stalls if this fires systematically: %s",
            cal_exc,
            exc_info=True,
        )
