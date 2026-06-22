"""Pin: reasoning_trace working memory + VideoGate wire.

The 2026-06-22 audit identified working memory as the missing intra-run
coordination layer. Stages run in isolation; downstream stages can't
see prior stages' concerns.

This module ships:
1. ``genlab_core/pipeline/reasoning_trace.append_trace()`` helper
2. ``reasoning_trace`` key seeded by pipeline_runner in every run
3. Demonstration wire: VideoGate emits a stage-summary entry

These pins guard:
- append_trace creates the list when absent (no init coordination)
- decision verbs are stored as-given (closed set: keep/drop/approve/etc.)
- confidence is clamped to [0, 1]
- get_warnings filters to warning entries only
- has_low_confidence_decision detects any sub-threshold confidence
- Fail-OPEN: malformed input doesn't crash callers
- VideoGate source pin: import + append_trace call structurally present
"""

from __future__ import annotations

from pathlib import Path


class TestAppendTrace:
    def test_creates_list_when_absent(self):
        """Pin: stages can append without coordinating initialization.
        ``reasoning_trace`` may be missing if a test or sub-pipeline
        skips pipeline_runner; append_trace handles that gracefully."""
        from genlab_core.pipeline.reasoning_trace import append_trace

        ctx: dict = {}
        append_trace(ctx, stage="X", decision="info")
        assert "reasoning_trace" in ctx
        assert len(ctx["reasoning_trace"]) == 1
        assert ctx["reasoning_trace"][0]["stage"] == "X"

    def test_appends_to_existing_list(self):
        """Pin: when pipeline_runner pre-seeds the list, append extends
        it rather than replacing."""
        from genlab_core.pipeline.reasoning_trace import append_trace

        ctx = {"reasoning_trace": [{"stage": "Pre", "decision": "info"}]}
        append_trace(ctx, stage="X", decision="keep")
        assert len(ctx["reasoning_trace"]) == 2
        assert ctx["reasoning_trace"][0]["stage"] == "Pre"
        assert ctx["reasoning_trace"][1]["stage"] == "X"

    def test_confidence_clamped_to_unit_interval(self):
        """Pin: out-of-range confidence is clamped. Guards against
        accidental scaling errors (e.g., a stage emits 100 thinking
        percentage)."""
        from genlab_core.pipeline.reasoning_trace import append_trace

        ctx: dict = {}
        append_trace(ctx, stage="X", decision="keep", confidence=2.5)
        assert ctx["reasoning_trace"][0]["confidence"] == 1.0
        append_trace(ctx, stage="Y", decision="drop", confidence=-0.3)
        assert ctx["reasoning_trace"][1]["confidence"] == 0.0

    def test_reasons_metadata_default_to_empty(self):
        """Pin: callers can omit optional kwargs; entry has empty
        defaults so consumers can rely on key presence."""
        from genlab_core.pipeline.reasoning_trace import append_trace

        ctx: dict = {}
        append_trace(ctx, stage="X", decision="keep")
        entry = ctx["reasoning_trace"][0]
        assert entry["reasons"] == []
        assert entry["metadata"] == {}
        assert entry["entity_id"] == ""

    def test_malformed_input_does_not_crash(self):
        """Pin: helper is fail-OPEN. Bad types must not raise — the
        trace is augmentation, never gate the pipeline."""
        from genlab_core.pipeline.reasoning_trace import append_trace

        # Should NOT raise even with garbage types
        ctx: dict = {"reasoning_trace": "not_a_list"}  # type: ignore
        append_trace(ctx, stage="X", decision="keep")
        # Should have replaced the malformed value with a fresh list
        assert isinstance(ctx["reasoning_trace"], list)
        assert len(ctx["reasoning_trace"]) == 1


class TestConsumerHelpers:
    def test_get_warnings_filters_correctly(self):
        """Pin: get_warnings returns ONLY warning entries."""
        from genlab_core.pipeline.reasoning_trace import append_trace, get_warnings

        ctx: dict = {}
        append_trace(ctx, stage="A", decision="keep")
        append_trace(ctx, stage="B", decision="warning", reasons=["something off"])
        append_trace(ctx, stage="C", decision="info")
        append_trace(ctx, stage="D", decision="warning", reasons=["another concern"])

        warnings = get_warnings(ctx["reasoning_trace"])
        assert len(warnings) == 2
        assert warnings[0]["stage"] == "B"
        assert warnings[1]["stage"] == "D"

    def test_get_warnings_none_returns_empty(self):
        """Pin: None/missing trace → empty list (callers don't need
        to guard)."""
        from genlab_core.pipeline.reasoning_trace import get_warnings

        assert get_warnings(None) == []
        assert get_warnings([]) == []
        assert get_warnings("not a list") == []  # type: ignore

    def test_low_confidence_detects(self):
        """Pin: has_low_confidence_decision returns True when ANY
        entry's confidence is below threshold."""
        from genlab_core.pipeline.reasoning_trace import (
            append_trace,
            has_low_confidence_decision,
        )

        ctx: dict = {}
        append_trace(ctx, stage="A", decision="keep", confidence=0.9)
        append_trace(ctx, stage="B", decision="keep", confidence=0.4)
        append_trace(ctx, stage="C", decision="keep", confidence=0.95)

        assert has_low_confidence_decision(ctx["reasoning_trace"], threshold=0.5)
        # Tighten the threshold — all entries pass
        assert not has_low_confidence_decision(ctx["reasoning_trace"], threshold=0.3)

    def test_low_confidence_handles_malformed(self):
        """Pin: malformed entries don't crash the detector."""
        from genlab_core.pipeline.reasoning_trace import has_low_confidence_decision

        bad_trace = [
            {"stage": "X"},  # no confidence
            {"confidence": "not a number"},  # bad type
            None,  # not a dict at all
        ]
        # Must NOT raise; just returns False for these
        assert not has_low_confidence_decision(bad_trace, threshold=0.5)


class TestVideoGateWire:
    """Source-level pin: VideoGate imports + calls append_trace at
    the end of execute(). Guards against accidental removal in
    future refactors."""

    def test_video_gate_imports_and_calls_append_trace(self):
        """Pin: video_gate.py wires reasoning_trace at end of execute().
        Decision verb is determined by drop_rate (>=50% = warning)."""
        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "pipeline"
            / "stages"
            / "video_gate.py"
        )
        content = src.read_text()
        assert "from genlab_core.pipeline.reasoning_trace import append_trace" in content
        assert "append_trace(" in content
        # The drop_rate-based decision pin: ensures the WARNING tier
        # at high drop is preserved (consumers depend on this signal).
        assert 'decision = "warning" if drop_rate >= 0.5 else "info"' in content


class TestPipelineRunnerSeedsTrace:
    """Source-level pin: pipeline_runner.run() seeds reasoning_trace
    to empty list in the initial context dict. Guards against
    accidental removal during context refactors."""

    def test_pipeline_runner_seeds_empty_trace(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "pipeline"
            / "pipeline_runner.py"
        )
        content = src.read_text()
        assert '"reasoning_trace": []' in content, (
            "pipeline_runner.run() must seed reasoning_trace=[] in the "
            "initial context dict. Stages assume the key exists."
        )


class TestStageContextTypedDict:
    """Source-level pin: StageContext TypedDict includes reasoning_trace
    so type-checked stage code gets schema for free."""

    def test_stage_context_declares_reasoning_trace(self):
        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "pipeline"
            / "stage_context.py"
        )
        content = src.read_text()
        assert "reasoning_trace: list[dict[str, Any]]" in content, (
            "StageContext TypedDict must declare reasoning_trace. "
            "Without it, type-checkers won't catch new stages that "
            "forget the helper."
        )
