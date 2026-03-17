"""Tests for genlab_core.observability.metrics_writer."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from genlab_core.observability.metrics_writer import (
    METRICS_FILENAME,
    PipelineMetrics,
    StageMetric,
)


# ── StageMetric dataclass ───────────────────────────────────────────────────


class TestStageMetric:
    def test_to_dict_basic(self):
        m = StageMetric(
            stage="FetchVideos",
            duration_ms=1234.567,
            status="ok",
            timestamp_iso="2026-03-17T06:30:00+00:00",
        )
        d = m.to_dict()
        assert d["stage"] == "FetchVideos"
        assert d["duration_ms"] == 1234.57  # rounded to 2 decimals
        assert d["status"] == "ok"
        assert d["timestamp"] == "2026-03-17T06:30:00+00:00"

    def test_to_dict_includes_extra(self):
        m = StageMetric(
            stage="Score",
            duration_ms=100.0,
            status="ok",
            timestamp_iso="2026-03-17T06:30:00+00:00",
            extra={"items": 12, "filtered": 3},
        )
        d = m.to_dict()
        assert d["items"] == 12
        assert d["filtered"] == 3

    def test_to_dict_empty_extra(self):
        m = StageMetric(
            stage="X",
            duration_ms=0.0,
            status="error",
            timestamp_iso="2026-03-17T06:30:00+00:00",
        )
        d = m.to_dict()
        # Should not have any extra keys beyond the core 4
        assert set(d.keys()) == {"stage", "duration_ms", "status", "timestamp"}


# ── PipelineMetrics.record_stage ────────────────────────────────────────────


class TestRecordStage:
    def test_record_single_stage(self):
        pm = PipelineMetrics(niche_id="gaming")
        pm.record_stage("FetchVideos", duration_ms=500.0, status="ok")

        entries = pm.entries
        assert len(entries) == 1
        assert entries[0].stage == "FetchVideos"
        assert entries[0].duration_ms == 500.0
        assert entries[0].status == "ok"

    def test_record_multiple_stages(self):
        pm = PipelineMetrics()
        pm.record_stage("A", duration_ms=100.0, status="ok")
        pm.record_stage("B", duration_ms=200.0, status="error", error_msg="boom")
        pm.record_stage("C", duration_ms=50.0, status="skipped")

        assert len(pm.entries) == 3
        assert pm.entries[1].extra["error_msg"] == "boom"

    def test_record_with_extra_kwargs(self):
        pm = PipelineMetrics()
        pm.record_stage("Score", duration_ms=300.0, status="ok", items=10, threshold=0.5)

        entry = pm.entries[0]
        assert entry.extra["items"] == 10
        assert entry.extra["threshold"] == 0.5

    def test_entries_are_copy(self):
        """The entries property should return a copy, not the internal list."""
        pm = PipelineMetrics()
        pm.record_stage("A", duration_ms=1.0, status="ok")

        entries = pm.entries
        entries.clear()

        # Internal list should be unaffected
        assert len(pm.entries) == 1

    def test_timestamp_is_iso_format(self):
        pm = PipelineMetrics()
        pm.record_stage("X", duration_ms=1.0, status="ok")

        ts = pm.entries[0].timestamp_iso
        # Should be parseable ISO 8601
        assert "T" in ts
        assert "+" in ts or "Z" in ts


# ── PipelineMetrics.time_stage ──────────────────────────────────────────────


class TestTimeStage:
    def test_time_stage_records_ok(self):
        pm = PipelineMetrics()

        with pm.time_stage("Render") as extra:
            time.sleep(0.01)  # small sleep to get non-zero duration
            extra["frames"] = 120

        entries = pm.entries
        assert len(entries) == 1
        assert entries[0].stage == "Render"
        assert entries[0].status == "ok"
        assert entries[0].duration_ms >= 5  # at least a few ms
        assert entries[0].extra["frames"] == 120

    def test_time_stage_records_error_on_exception(self):
        pm = PipelineMetrics()

        with pytest.raises(ValueError, match="render failed"):
            with pm.time_stage("Render"):
                raise ValueError("render failed")

        entries = pm.entries
        assert len(entries) == 1
        assert entries[0].status == "error"
        assert entries[0].duration_ms >= 0

    def test_time_stage_preserves_initial_kwargs(self):
        pm = PipelineMetrics()

        with pm.time_stage("Fetch", source="youtube") as extra:
            extra["count"] = 5

        entry = pm.entries[0]
        assert entry.extra["source"] == "youtube"
        assert entry.extra["count"] == 5

    def test_time_stage_reraises_exception(self):
        """The exception should propagate after recording."""
        pm = PipelineMetrics()

        with pytest.raises(RuntimeError):
            with pm.time_stage("Broken"):
                raise RuntimeError("fatal")

        # But metric was still recorded
        assert len(pm.entries) == 1


# ── PipelineMetrics.flush ───────────────────────────────────────────────────


class TestFlush:
    def test_flush_creates_file(self, tmp_path: Path):
        pm = PipelineMetrics(niche_id="gaming", run_id="run_001")
        pm.record_stage("A", duration_ms=100.0, status="ok")

        out = pm.flush(tmp_path / "myrun")

        assert out.exists()
        assert out.name == METRICS_FILENAME

    def test_flush_creates_parent_dirs(self, tmp_path: Path):
        pm = PipelineMetrics()
        pm.record_stage("A", duration_ms=1.0, status="ok")

        deep_dir = tmp_path / "a" / "b" / "c"
        out = pm.flush(deep_dir)

        assert out.exists()
        assert deep_dir.exists()

    def test_flush_writes_valid_jsonl(self, tmp_path: Path):
        pm = PipelineMetrics(niche_id="anime", run_id="r42")
        pm.record_stage("Fetch", duration_ms=100.0, status="ok", items=5)
        pm.record_stage("Score", duration_ms=200.0, status="error", error_msg="timeout")

        out = pm.flush(tmp_path)
        lines = out.read_text().strip().split("\n")

        # 2 stage lines + 1 summary line = 3
        assert len(lines) == 3

        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_flush_stage_lines_have_niche_and_run(self, tmp_path: Path):
        pm = PipelineMetrics(niche_id="sports", run_id="r99")
        pm.record_stage("Publish", duration_ms=50.0, status="ok")

        out = pm.flush(tmp_path)
        lines = out.read_text().strip().split("\n")
        stage_line = json.loads(lines[0])

        assert stage_line["niche_id"] == "sports"
        assert stage_line["run_id"] == "r99"

    def test_flush_summary_line(self, tmp_path: Path):
        pm = PipelineMetrics(niche_id="movies")
        pm.record_stage("A", duration_ms=100.0, status="ok")
        pm.record_stage("B", duration_ms=200.0, status="ok")
        pm.record_stage("C", duration_ms=50.0, status="error")

        out = pm.flush(tmp_path)
        lines = out.read_text().strip().split("\n")
        summary = json.loads(lines[-1])

        assert summary["_type"] == "pipeline_summary"
        assert summary["total_stages"] == 3
        assert summary["ok"] == 2
        assert summary["errors"] == 1
        assert summary["total_duration_ms"] == 350.0
        assert summary["niche_id"] == "movies"

    def test_flush_summary_with_wall_clock(self, tmp_path: Path):
        pm = PipelineMetrics()
        pm.mark_pipeline_start()
        pm.record_stage("A", duration_ms=100.0, status="ok")
        time.sleep(0.01)
        pm.mark_pipeline_end()

        out = pm.flush(tmp_path)
        lines = out.read_text().strip().split("\n")
        summary = json.loads(lines[-1])

        assert "wall_clock_ms" in summary
        assert summary["wall_clock_ms"] >= 5  # at least a few ms

    def test_flush_no_wall_clock_without_marks(self, tmp_path: Path):
        pm = PipelineMetrics()
        pm.record_stage("A", duration_ms=1.0, status="ok")

        out = pm.flush(tmp_path)
        lines = out.read_text().strip().split("\n")
        summary = json.loads(lines[-1])

        assert "wall_clock_ms" not in summary

    def test_flush_empty_metrics(self, tmp_path: Path):
        """Flushing with no recorded stages should still write a summary."""
        pm = PipelineMetrics(niche_id="gaming")

        out = pm.flush(tmp_path)
        lines = out.read_text().strip().split("\n")

        assert len(lines) == 1
        summary = json.loads(lines[0])
        assert summary["_type"] == "pipeline_summary"
        assert summary["total_stages"] == 0

    def test_flush_without_niche_or_run(self, tmp_path: Path):
        """Metrics created without niche_id/run_id omit those fields."""
        pm = PipelineMetrics()
        pm.record_stage("A", duration_ms=1.0, status="ok")

        out = pm.flush(tmp_path)
        lines = out.read_text().strip().split("\n")
        stage_line = json.loads(lines[0])

        assert "niche_id" not in stage_line
        assert "run_id" not in stage_line

    def test_flush_returns_path(self, tmp_path: Path):
        pm = PipelineMetrics()
        pm.record_stage("A", duration_ms=1.0, status="ok")

        result = pm.flush(tmp_path)

        assert isinstance(result, Path)
        assert result == tmp_path / METRICS_FILENAME


# ── Pipeline start/end marks ────────────────────────────────────────────────


class TestPipelineMarks:
    def test_mark_pipeline_start_sets_time(self):
        pm = PipelineMetrics()
        assert pm._pipeline_start is None

        pm.mark_pipeline_start()
        assert pm._pipeline_start is not None
        assert isinstance(pm._pipeline_start, float)

    def test_mark_pipeline_end_sets_time(self):
        pm = PipelineMetrics()
        pm.mark_pipeline_end()
        assert pm._pipeline_end is not None
