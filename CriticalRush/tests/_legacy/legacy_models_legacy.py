"""Tests for orchestration Pydantic models."""

from genlab_core.orchestration.models import (
    FlowRunSummary,
    PipelineProgress,
    StageStatus,
)


class TestStageStatus:
    def test_defaults(self):
        s = StageStatus(name="FetchGamingStories")
        assert s.name == "FetchGamingStories"
        assert s.status == "pending"
        assert s.duration_seconds == 0.0
        assert s.error is None

    def test_with_values(self):
        s = StageStatus(
            name="WriteGamingContent",
            status="completed",
            duration_seconds=12.5,
            items_processed=8,
            items_total=10,
        )
        assert s.status == "completed"
        assert s.items_processed == 8


class TestFlowRunSummary:
    def test_defaults(self):
        r = FlowRunSummary(run_id="abc123", flow_name="gaming-pipeline")
        assert r.state == "PENDING"
        assert r.trigger == "scheduled"
        assert r.stages == []
        assert r.niche_id == ""

    def test_with_stages(self):
        r = FlowRunSummary(
            run_id="abc",
            flow_name="gaming-pipeline",
            niche_id="gaming",
            state="RUNNING",
            current_stage="FetchGamingStories",
            stages=[StageStatus(name="FetchGamingStories", status="running")],
        )
        assert r.current_stage == "FetchGamingStories"
        assert len(r.stages) == 1


class TestPipelineProgress:
    def test_serialization(self):
        p = PipelineProgress(
            niche_id="gaming",
            run_id="run_123",
            current_stage="FilterGamingStories",
            stage_index=1,
            total_stages=13,
            elapsed_seconds=45.2,
        )
        d = p.model_dump()
        assert d["niche_id"] == "gaming"
        assert d["stage_index"] == 1
        assert d["pipeline_status"] == "running"
