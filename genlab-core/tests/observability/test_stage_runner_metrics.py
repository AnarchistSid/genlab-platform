"""Tests for PipelineMetrics integration in stage_runner."""

from __future__ import annotations

from unittest.mock import MagicMock

from genlab_core.observability.metrics_writer import PipelineMetrics
from genlab_core.pipeline.stage_runner import (
    LocalStageRunner,
)


class FakeStage:
    """Minimal stage for testing."""

    def __init__(self, *, raise_exc: Exception | None = None):
        self._raise = raise_exc

    def execute(self, context: dict) -> dict:
        if self._raise:
            raise self._raise
        return context


class TestLocalStageRunnerMetrics:
    """Verify that LocalStageRunner records metrics when provided."""

    def test_records_ok_metric_on_success(self):
        metrics = PipelineMetrics(niche_id="gaming")
        runner = LocalStageRunner(metrics=metrics)
        pipeline_ctx = MagicMock()

        result = runner.run_stage(FakeStage(), {}, pipeline_ctx)

        assert result.success is True
        entries = metrics.entries
        assert len(entries) == 1
        assert entries[0].stage == "FakeStage"
        assert entries[0].status == "ok"
        assert entries[0].duration_ms >= 0

    def test_records_error_metric_on_failure(self):
        metrics = PipelineMetrics()
        runner = LocalStageRunner(metrics=metrics)
        pipeline_ctx = MagicMock()

        result = runner.run_stage(
            FakeStage(raise_exc=ValueError("boom")),
            {},
            pipeline_ctx,
        )

        assert result.success is False
        entries = metrics.entries
        assert len(entries) == 1
        assert entries[0].stage == "FakeStage"
        assert entries[0].status == "error"

    def test_no_metrics_still_works(self):
        """LocalStageRunner without metrics= arg should work as before."""
        runner = LocalStageRunner()
        pipeline_ctx = MagicMock()

        result = runner.run_stage(FakeStage(), {}, pipeline_ctx)
        assert result.success is True

    def test_metric_duration_matches_result(self):
        """The metric duration_ms should be close to result.elapsed_seconds * 1000."""
        metrics = PipelineMetrics()
        runner = LocalStageRunner(metrics=metrics)
        pipeline_ctx = MagicMock()

        result = runner.run_stage(FakeStage(), {}, pipeline_ctx)

        entry = metrics.entries[0]
        # Allow 50ms tolerance for test overhead
        assert abs(entry.duration_ms - result.elapsed_seconds * 1000) < 50
