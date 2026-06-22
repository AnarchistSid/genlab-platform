"""Working memory: per-run reasoning trace shared across pipeline stages.

The 2026-06-22 "make-the-agent-smarter" audit identified working memory
as the missing intra-run coordination layer. Stages run in isolation:
the relevance_gate doesn't know that the video_gate later found
"clip too short"; the auto_approval_gate doesn't see that QC raised
a borderline flag. Each stage makes its decision in a vacuum.

This module ships the minimal viable shared-trace primitive:

    context["reasoning_trace"]: list[ReasoningEntry]

Each entry records (stage, decision, confidence, reasons). Downstream
stages can consult prior entries to detect conflicts — e.g., the
auto-approval gate can downgrade confidence when prior gates flagged
borderline issues.

The trace is intentionally ADDITIVE — no stage's existing behavior
changes. Stages that don't write entries don't appear in the trace.
Consumers that don't read the trace behave exactly as before.

Persistence: the trace lives in the pipeline context dict only. At
push_to_backlog time the trace is serialized into blueprint.extra
so the dashboard's auto-approval-preview endpoint can read it back
at review time. Migration not required — extra is JSONB and tolerates
new keys.

Comparison to episodic_memory:
- episodic_memory writes to a Postgres table queryable across runs
  (long-term semantic + episodic memory; used by RAG)
- reasoning_trace lives only in the current pipeline run's context
  (short-term working memory; used by within-run gates)

These compose: a reasoning_trace entry CAN also become an episodic
event (record both), but most reasoning_trace entries are too
fine-grained to deserve long-term storage.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Decision verbs are a closed set so consumers can pattern-match
# reliably. Stages should pick the verb that best describes their
# decision shape:
#
# - "keep": stage kept this entity (story passed filter, blueprint
#   passed gate, etc.)
# - "drop": stage dropped this entity (rejected by gate, filtered out)
# - "approve": stage explicitly approved (auto-approval, manual ok)
# - "reject": stage explicitly rejected
# - "warning": stage didn't drop but flagged a concern downstream
#   consumers should notice
# - "info": neutral observational entry (e.g., "30 stories fetched")
Decision = Literal["keep", "drop", "approve", "reject", "warning", "info"]


def append_trace(
    context: dict[str, Any],
    *,
    stage: str,
    decision: Decision,
    confidence: float = 1.0,
    reasons: list[str] | None = None,
    entity_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Append a reasoning entry to the pipeline context's trace.

    Args:
        context: The stage context dict. If ``reasoning_trace`` is not
            present, this function INITIALIZES it (so stages can
            safely append without coordinating initialization).
        stage: The stage name making the decision. Conventionally the
            class name (e.g., "VideoGate", "RelevanceGate").
        decision: One of the closed-set decision verbs.
        confidence: Float in [0, 1]. 1.0 = certain, 0.5 = neutral,
            0.0 = least certain. Clamped silently if out of range.
        reasons: Human-readable strings explaining the decision.
            Multiple reasons capture multi-factor decisions
            ("score=0.32 + missing video clip + niche mismatch").
        entity_id: Optional ID of the entity the decision is about
            (story_id, blueprint_id, video_id). Empty string when
            the decision is about the stage's overall output.
        metadata: Optional structured context (counts, scores, etc.)
            for downstream consumers. Keep it small; this lives in
            memory + serializes to blueprint.extra.

    Fail-OPEN: any unexpected error is caught and logged. Working
    memory MUST NEVER block a stage from completing.
    """
    try:
        entry = {
            "stage": str(stage),
            "decision": str(decision),
            "confidence": max(0.0, min(1.0, float(confidence))),
            "reasons": list(reasons) if reasons else [],
            "entity_id": str(entity_id),
            "metadata": dict(metadata) if metadata else {},
        }
        trace = context.get("reasoning_trace")
        if not isinstance(trace, list):
            trace = []
            context["reasoning_trace"] = trace
        trace.append(entry)
    except Exception as exc:  # noqa: BLE001 — fail-OPEN
        logger.debug("[reasoning_trace] append failed (non-fatal): %s", exc)


def get_warnings(trace: list[dict] | None) -> list[dict]:
    """Filter a trace to just the entries with decision='warning'.

    Used by the auto-approval gate to detect prior-stage concerns
    that should soften its own confidence. Returns empty list when
    trace is None, empty, or malformed (fail-OPEN).
    """
    if not trace or not isinstance(trace, list):
        return []
    out = []
    for entry in trace:
        if isinstance(entry, dict) and entry.get("decision") == "warning":
            out.append(entry)
    return out


def has_low_confidence_decision(trace: list[dict] | None, threshold: float = 0.5) -> bool:
    """True if any prior stage made a decision with confidence < threshold.

    Used by the gate to detect "earlier stages were uncertain" — when
    multiple stages were uncertain, the gate's own decision should be
    less confident even if its own checks pass.
    """
    if not trace or not isinstance(trace, list):
        return False
    for entry in trace:
        if isinstance(entry, dict):
            conf = entry.get("confidence")
            if isinstance(conf, (int, float)) and conf < threshold:
                return True
    return False
