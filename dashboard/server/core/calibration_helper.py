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

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Map dashboard action terms to canonical calibration_logger
# operator_action values. "archived" doesn't map directly — the
# closest semantic equivalent is "rejected" (operator explicitly
# moved the blueprint out of the active queue). Mapping here so the
# 4 call sites stay shape-agnostic.
_ACTION_ALIAS: dict[str, str] = {
    "archived": "rejected",
}


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
        # the dashboard preview endpoint + auto_approver (PR #221). All
        # 3 sites must stay aligned; a new score field added to the
        # gate needs to land in all 3.
        if not isinstance(flat.get("extra"), dict):
            flat["extra"] = {
                "visual_paths": flat.get("visual_paths"),
                "composite_score": flat.get("composite_score"),
                "virality_score": flat.get("virality_score"),
                "validation_status": flat.get("validation_status"),
            }

        try:
            decision = gate_evaluate(flat)
        except Exception as ev_exc:
            logger.debug("[calibration] gate evaluation failed: %s", ev_exc)
            decision = None

        # Translate dashboard action vocabulary to calibration_logger's
        # canonical operator_action values.
        operator_action = _ACTION_ALIAS.get(action, action)

        action_source = (flat.get("action_taken_source") or "").strip() or None
        calibration_logger.log(
            blueprint_id=record_id,
            niche_id=niche_id,
            decision=decision,
            operator_action=operator_action,
            action_taken_source=action_source,
        )
    except Exception as cal_exc:  # noqa: BLE001 — never block caller
        logger.debug("[calibration] skipped (non-fatal): %s", cal_exc)
