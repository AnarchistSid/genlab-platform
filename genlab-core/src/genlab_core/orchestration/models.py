"""Pydantic models for orchestration state shared between backend and frontend."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class StageStatus(BaseModel):
    """Status of a single pipeline stage within a flow run."""

    name: str
    status: str = "pending"  # pending | running | completed | failed | skipped
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    error: Optional[str] = None
    items_processed: int = 0
    items_total: int = 0


class FlowRunSummary(BaseModel):
    """Lightweight summary of a Prefect flow run for dashboard display."""

    run_id: str
    flow_name: str
    niche_id: str = ""
    state: str = "PENDING"  # PENDING | RUNNING | COMPLETED | FAILED | CANCELLED
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0
    stages: list[StageStatus] = Field(default_factory=list)
    current_stage: Optional[str] = None
    trigger: str = "scheduled"  # scheduled | manual | spike
    error: Optional[str] = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class PipelineProgress(BaseModel):
    """Real-time progress event emitted via SocketIO."""

    niche_id: str
    run_id: str
    pipeline_status: str = "running"  # running | completed | failed
    current_stage: Optional[str] = None
    stage_index: int = 0
    total_stages: int = 0
    items_processed: int = 0
    items_total: int = 0
    elapsed_seconds: float = 0.0
    stages: list[StageStatus] = Field(default_factory=list)
