"""Pin: structured decision-trace JSONL sink + VideoGate wire.

The 2026-06-22 audit identified observability for agent decisions as
the highest-leverage debug investment. Today's logs record WHAT
happened in prose; recovering the prompt+response+downstream-effect
of a wrong LLM gate decision is detective work across files.

This module ships a structured per-decision trace at
``<run_dir>/decision_traces.jsonl`` — append-only, one JSON object
per line, queryable via jq/grep.

These pins guard:
- DecisionTrace dataclass auto-stamps timestamp + clamps confidence
- LLM response truncated to 8KB to keep files reasonable
- Sink writes valid JSONL (one object per line, parseable)
- Sink no-ops silently when run_dir is None/missing
- record_decision convenience handles sink lookup + trace creation
- Fail-OPEN on every IO/JSON error
- VideoGate source pin: imports + calls record_decision
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch


class TestDecisionTraceDataclass:
    def test_auto_stamps_timestamp(self):
        """Pin: timestamp is auto-populated if not provided. Operators
        rely on ordering by timestamp; missing values would break
        time-series analysis."""
        from genlab_core.observability.decision_trace import DecisionTrace

        t = DecisionTrace(stage="X", decision="keep", reason="ok")
        assert t.timestamp  # non-empty
        # ISO-8601 format check (rough): contains 'T' and ends with offset
        assert "T" in t.timestamp

    def test_explicit_timestamp_preserved(self):
        """Pin: when timestamp is supplied, no auto-stamp."""
        from genlab_core.observability.decision_trace import DecisionTrace

        t = DecisionTrace(
            stage="X",
            decision="keep",
            reason="ok",
            timestamp="2026-01-01T00:00:00+00:00",
        )
        assert t.timestamp == "2026-01-01T00:00:00+00:00"

    def test_confidence_clamped(self):
        """Pin: confidence outside [0,1] is clamped silently."""
        from genlab_core.observability.decision_trace import DecisionTrace

        t = DecisionTrace(stage="X", decision="keep", reason="ok", confidence=2.5)
        assert t.confidence == 1.0
        t2 = DecisionTrace(stage="X", decision="drop", reason="bad", confidence=-0.3)
        assert t2.confidence == 0.0

    def test_llm_response_truncated_at_8kb(self):
        """Pin: huge LLM responses (rare but possible) are truncated to
        keep trace files manageable. Truncation marker preserves
        debuggability — operator sees the response was truncated."""
        from genlab_core.observability.decision_trace import DecisionTrace

        huge = "x" * 20000  # 20KB
        t = DecisionTrace(stage="X", decision="keep", reason="ok", llm_response=huge)
        assert t.llm_response is not None
        assert len(t.llm_response) <= 8192 + len("... [truncated]")
        assert t.llm_response.endswith("[truncated]")

    def test_short_llm_response_not_truncated(self):
        """Pin: typical small LLM responses pass through unchanged."""
        from genlab_core.observability.decision_trace import DecisionTrace

        small = "approved: looks good"
        t = DecisionTrace(stage="X", decision="approve", reason="ok", llm_response=small)
        assert t.llm_response == small


class TestSink:
    def test_appends_valid_jsonl(self, tmp_path):
        """Pin: each record() call appends ONE valid JSON object per
        line. The format must be parseable by jq + Python json.loads
        line-by-line."""
        from genlab_core.observability.decision_trace import (
            DecisionTrace,
            DecisionTraceSink,
        )

        sink = DecisionTraceSink(tmp_path)
        assert sink.is_enabled
        sink.record(DecisionTrace(stage="A", decision="keep", reason="ok"))
        sink.record(DecisionTrace(stage="B", decision="drop", reason="bad"))

        path = sink.path
        assert path is not None
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        assert first["stage"] == "A"
        assert first["decision"] == "keep"
        assert second["stage"] == "B"
        assert second["decision"] == "drop"

    def test_disabled_when_run_dir_none(self):
        """Pin: None run_dir → sink no-ops. Dev/test scenarios where
        traces aren't relevant must not error."""
        from genlab_core.observability.decision_trace import (
            DecisionTrace,
            DecisionTraceSink,
        )

        sink = DecisionTraceSink(None)
        assert not sink.is_enabled
        # MUST NOT raise
        sink.record(DecisionTrace(stage="X", decision="keep", reason="ok"))

    def test_unwritable_run_dir_disables_silently(self, tmp_path):
        """Pin: when run_dir can't be created (permission denied,
        etc.), sink disables silently — no exception bubbles up."""
        from genlab_core.observability.decision_trace import (
            DecisionTrace,
            DecisionTraceSink,
        )

        # Use a path inside a non-existent parent that we can't create
        # (mock OSError on mkdir)
        with patch.object(Path, "mkdir", side_effect=OSError("permission denied")):
            sink = DecisionTraceSink(tmp_path / "subdir")
            assert not sink.is_enabled
            sink.record(DecisionTrace(stage="X", decision="keep", reason="ok"))

    def test_write_error_does_not_raise(self, tmp_path):
        """Pin: IO error during write is caught silently. Trace
        writing MUST NEVER block a stage."""
        from genlab_core.observability.decision_trace import (
            DecisionTrace,
            DecisionTraceSink,
        )

        sink = DecisionTraceSink(tmp_path)
        # Patch the open call to raise mid-write
        with patch.object(Path, "open", side_effect=OSError("disk full")):
            # MUST NOT raise
            sink.record(DecisionTrace(stage="X", decision="keep", reason="ok"))


class TestRecordDecisionConvenience:
    def test_creates_sink_from_context(self, tmp_path):
        """Pin: record_decision() finds-or-creates the sink from the
        context dict + caches it for subsequent calls (no double-init
        across many stages in one run)."""
        from genlab_core.observability.decision_trace import (
            DecisionTraceSink,
            record_decision,
        )

        ctx: dict = {"run_dir": str(tmp_path), "run_id": "run-123"}
        record_decision(ctx, stage="A", decision="keep", reason="ok")
        # Sink was created + stashed in context
        sink = ctx.get("decision_trace_sink")
        assert isinstance(sink, DecisionTraceSink)
        # File written
        lines = (tmp_path / "decision_traces.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        first = json.loads(lines[0])
        assert first["stage"] == "A"
        assert first["run_id"] == "run-123"

    def test_no_run_dir_no_op_no_error(self):
        """Pin: when context has no run_dir AND env has no
        GENLAB_RUN_DIR, record_decision is a silent no-op."""
        from genlab_core.observability.decision_trace import record_decision

        ctx: dict = {}
        # MUST NOT raise even with no run_dir
        record_decision(ctx, stage="A", decision="keep", reason="ok")

    def test_serialization_error_does_not_raise(self, tmp_path):
        """Pin: non-JSON-serializable metadata is caught silently.
        Stages must not have to think about JSON-cleanliness of their
        metadata dict — fail-OPEN absorbs it."""
        from genlab_core.observability.decision_trace import record_decision

        ctx: dict = {"run_dir": str(tmp_path)}
        # Pass a non-serializable object (a set) as metadata value
        record_decision(
            ctx,
            stage="A",
            decision="keep",
            reason="ok",
            metadata={"bad": {1, 2, 3}},  # set is not JSON-serializable
        )
        # MUST NOT raise — `default=str` in json.dumps catches it


class TestVideoGateWire:
    """Source-level pin: VideoGate writes to BOTH reasoning_trace
    (in-memory) AND decision_traces (on-disk). Both consumers serve
    different timelines (immediate vs offline) and must coexist."""

    def test_video_gate_calls_record_decision(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "pipeline"
            / "stages"
            / "video_gate.py"
        )
        content = src.read_text()
        assert "from genlab_core.observability.decision_trace import record_decision" in content
        assert "record_decision(" in content
