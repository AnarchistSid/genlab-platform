"""Decision traces — per-decision structured log of (input, decision, reasoning).

The 2026-06-22 "make-the-agent-smarter" audit identified observability
for agent decisions as the highest-leverage debugging investment. Today
the metrics_writer records WHAT happened (stage timing + status); the
log lines record WHY only in human-readable prose. When an LLM gate
makes a wrong call, recovering "which prompt did we send + what did
the model return" is detective work across multiple log files.

This module ships a structured per-decision trace:

    DecisionTrace(
        stage="VideoGate",
        entity_id="story:abc123",
        decision="drop",
        confidence=0.95,
        reason="bitrate 26kbps below 150kbps threshold",
        prompt=None,           # set when an LLM was involved
        llm_response=None,
        metadata={"bitrate_bps": 26000, "threshold": 150000},
    )

Traces are appended to ``<run_dir>/decision_traces.jsonl`` — one JSON
object per line, append-only. Operators can later grep + analyze:

    cat .tmp/runs/<run_id>/decision_traces.jsonl | jq 'select(.stage=="VideoGate")'

Comparison to siblings:
- ``reasoning_trace`` (in pipeline/) — in-context working memory
  (within-run, consumed by gates immediately, list[dict])
- ``episodic_events`` (in learning/episodic_memory.py) — Postgres
  long-term memory (cross-run, queried by RAG / analytics)
- ``decision_traces`` (this module) — per-run on-disk audit log
  (within-run, consumed by debug + offline analysis)

All three compose: a stage decision can write to reasoning_trace
(immediate consumer), episodic_events (long-term memory), AND
decision_traces (audit log). Most stages will write to 1-2 of these.

Cost: file-append per decision. Negligible disk + zero LLM cost.

Fail-OPEN: every error path (file missing, JSON serialization fails,
disk full) is caught and logged at DEBUG. Traces are augmentation;
they MUST NEVER block a stage from completing.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DecisionTrace:
    """One structured decision event.

    Required fields:
        stage: The stage name (conventionally the class name).
        decision: Closed-set verb — keep / drop / approve / reject /
            override / escalate / warning / info. Use the most
            specific verb for the decision shape.
        reason: Human-readable explanation.

    Optional fields:
        entity_id: The entity the decision was about
            (story_id, blueprint_id, comment_id). Empty when the
            decision is stage-summary.
        confidence: Float in [0, 1]. 1.0 = certain.
        prompt: The exact prompt sent to an LLM (if any). Captured
            so operators can reproduce the decision later.
        llm_response: The exact LLM response (if any). Truncated to
            8KB to keep trace files reasonable.
        metadata: Structured data for downstream analysis (counts,
            scores, thresholds, model name, token usage).
        timestamp: ISO-8601 UTC timestamp; auto-populated.
        run_id: Pipeline run ID (auto-populated from env when sink
            is run-aware; else empty).
    """

    stage: str
    decision: str
    reason: str
    entity_id: str = ""
    confidence: float = 1.0
    prompt: str | None = None
    llm_response: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    run_id: str = ""

    def __post_init__(self) -> None:
        # Auto-stamp timestamp if not explicitly set.
        if not self.timestamp:
            # frozen=True means we can't direct-assign; use object.__setattr__
            object.__setattr__(self, "timestamp", datetime.now(UTC).isoformat())
        # Clamp confidence silently.
        clamped = max(0.0, min(1.0, float(self.confidence)))
        if clamped != self.confidence:
            object.__setattr__(self, "confidence", clamped)
        # Truncate llm_response if huge (keep trace files reasonable).
        if self.llm_response and len(self.llm_response) > 8192:
            object.__setattr__(self, "llm_response", self.llm_response[:8192] + "... [truncated]")


class DecisionTraceSink:
    """Append decisions to a per-run JSONL file.

    Lifecycle: instantiate once per pipeline run with the run_dir;
    pass into stages that want to record decisions. Each ``record()``
    call appends one JSON line.

    Thread-safe via OS-level file append semantics (POSIX guarantees
    writes ≤ PIPE_BUF are atomic). Per-line writes stay well under
    the 4KB POSIX limit even with truncated llm_response.

    Disabled instance: if ``run_dir`` is None or doesn't exist + can't
    be created, the sink no-ops silently. This is the dev/test mode
    where decision traces aren't relevant.
    """

    def __init__(self, run_dir: str | Path | None) -> None:
        self._path: Path | None = None
        if run_dir is None:
            return
        try:
            run_path = Path(run_dir)
            run_path.mkdir(parents=True, exist_ok=True)
            self._path = run_path / "decision_traces.jsonl"
        except OSError as exc:
            logger.debug("[decision_trace] sink init skipped (%s)", exc)
            self._path = None

    def record(self, trace: DecisionTrace) -> None:
        """Append one trace to the JSONL file.

        Fail-OPEN: any IO/JSON error is caught and logged at DEBUG.
        The caller's pipeline MUST NEVER fail because trace writing
        had a hiccup.
        """
        if self._path is None:
            return
        try:
            line = json.dumps(asdict(trace), ensure_ascii=False, default=str)
            with self._path.open("a") as fh:
                fh.write(line + "\n")
        except (OSError, TypeError, ValueError) as exc:
            logger.debug("[decision_trace] write failed (non-fatal): %s", exc)

    @property
    def is_enabled(self) -> bool:
        """True when the sink has a writable path."""
        return self._path is not None

    @property
    def path(self) -> Path | None:
        """The JSONL file path (None when disabled)."""
        return self._path


def get_sink_from_context(context: dict[str, Any]) -> DecisionTraceSink:
    """Pull a sink from the stage context, creating one if missing.

    Convenience for stages that don't want to coordinate sink
    initialization. The created sink is stashed in
    ``context["decision_trace_sink"]`` so subsequent stages share it.
    """
    sink = context.get("decision_trace_sink")
    if isinstance(sink, DecisionTraceSink):
        return sink
    run_dir = context.get("run_dir") or os.environ.get("GENLAB_RUN_DIR")
    sink = DecisionTraceSink(run_dir)
    context["decision_trace_sink"] = sink
    return sink


def record_decision(
    context: dict[str, Any],
    *,
    stage: str,
    decision: str,
    reason: str,
    entity_id: str = "",
    confidence: float = 1.0,
    prompt: str | None = None,
    llm_response: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Convenience: get the sink, build a trace, record it.

    Fail-OPEN: every error path is caught silently. Use this from
    inside stages when you just want "record this decision" without
    the bookkeeping of sink lookup + trace construction.
    """
    try:
        sink = get_sink_from_context(context)
        # Pull run_id from context if available (helps cross-trace analysis)
        run_id = context.get("run_id", "")
        trace = DecisionTrace(
            stage=stage,
            decision=decision,
            reason=reason,
            entity_id=entity_id,
            confidence=confidence,
            prompt=prompt,
            llm_response=llm_response,
            metadata=metadata or {},
            run_id=str(run_id),
        )
        sink.record(trace)
    except Exception as exc:  # noqa: BLE001 — fail-OPEN
        logger.debug("[decision_trace] record_decision failed (non-fatal): %s", exc)
