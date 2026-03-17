"""Tests for genlab_core.pipeline.log_streamer."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from genlab_core.pipeline.log_streamer import (
    MAX_LOG_LINES,
    PipelineLogHandler,
    StageLoggingFilter,
    install_log_handler,
    read_recent_logs,
    remove_log_handler,
)


class TestPipelineLogHandler:
    def test_writes_jsonl(self, tmp_path: Path):
        log_path = tmp_path / "test.jsonl"
        handler = PipelineLogHandler(log_path, run_id="r1", niche_id="gaming")
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="hello world",
            args=(), exc_info=None,
        )
        handler.emit(record)
        handler.close()

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["message"] == "hello world"
        assert entry["level"] == "INFO"
        assert entry["run_id"] == "r1"
        assert entry["niche_id"] == "gaming"
        assert entry["stage"] is None

    def test_stage_from_record_extra(self, tmp_path: Path):
        log_path = tmp_path / "test.jsonl"
        handler = PipelineLogHandler(log_path, run_id="r1", niche_id="gaming")
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test", level=logging.WARNING,
            pathname="", lineno=0, msg="slow",
            args=(), exc_info=None,
        )
        record.stage = "RenderVideo"  # type: ignore[attr-defined]
        handler.emit(record)
        handler.close()

        entry = json.loads(log_path.read_text().strip())
        assert entry["stage"] == "RenderVideo"
        assert entry["level"] == "WARNING"


class TestStageLoggingFilter:
    def test_injects_stage_into_record(self):
        filt = StageLoggingFilter()
        filt.current_stage = "FetchStories"

        record = logging.LogRecord(
            name="x", level=logging.INFO,
            pathname="", lineno=0, msg="msg",
            args=(), exc_info=None,
        )
        assert filt.filter(record) is True
        assert record.stage == "FetchStories"  # type: ignore[attr-defined]

    def test_none_when_no_stage(self):
        filt = StageLoggingFilter()

        record = logging.LogRecord(
            name="x", level=logging.INFO,
            pathname="", lineno=0, msg="msg",
            args=(), exc_info=None,
        )
        filt.filter(record)
        assert record.stage is None  # type: ignore[attr-defined]


class TestInstallRemove:
    def test_install_creates_file_with_sentinel(self, tmp_path: Path):
        handler, stage_filter, log_path = install_log_handler(
            "gaming", "run_001", tmp_path,
        )
        try:
            assert log_path.exists()
            lines = log_path.read_text().strip().split("\n")
            sentinel = json.loads(lines[0])
            assert sentinel["_type"] == "run_start"
            assert sentinel["run_id"] == "run_001"
        finally:
            remove_log_handler(handler)

    def test_handler_captures_root_logger_messages(self, tmp_path: Path):
        handler, _, log_path = install_log_handler(
            "gaming", "run_002", tmp_path,
        )
        try:
            test_logger = logging.getLogger("test_capture")
            test_logger.setLevel(logging.DEBUG)
            test_logger.info("capture this")

            entries = read_recent_logs(log_path)
            messages = [e["message"] for e in entries]
            assert "capture this" in messages
        finally:
            remove_log_handler(handler)

    def test_stage_filter_tags_records(self, tmp_path: Path):
        handler, stage_filter, log_path = install_log_handler(
            "gaming", "run_003", tmp_path,
        )
        try:
            stage_filter.current_stage = "WriteContent"
            test_logger = logging.getLogger("test_stage_tag")
            test_logger.setLevel(logging.DEBUG)
            test_logger.info("writing hooks")

            entries = read_recent_logs(log_path)
            tagged = [e for e in entries if e.get("stage") == "WriteContent"]
            assert len(tagged) >= 1
            assert tagged[0]["message"] == "writing hooks"
        finally:
            remove_log_handler(handler)

    def test_remove_cleans_up(self, tmp_path: Path):
        handler, _, _ = install_log_handler("gaming", "run_004", tmp_path)
        root = logging.getLogger()
        assert handler in root.handlers
        remove_log_handler(handler)
        assert handler not in root.handlers


class TestReadRecentLogs:
    def _write_entries(self, path: Path, entries: list[dict]):
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def test_returns_last_n(self, tmp_path: Path):
        log_path = tmp_path / "test.jsonl"
        entries = [{"ts": f"2026-01-01T00:00:{i:02d}Z", "message": f"m{i}"} for i in range(10)]
        self._write_entries(log_path, entries)

        result = read_recent_logs(log_path, limit=3)
        assert len(result) == 3
        assert result[0]["message"] == "m7"

    def test_after_ts_filters(self, tmp_path: Path):
        log_path = tmp_path / "test.jsonl"
        entries = [
            {"ts": "2026-01-01T00:00:00Z", "message": "old"},
            {"ts": "2026-01-01T00:00:05Z", "message": "new"},
        ]
        self._write_entries(log_path, entries)

        result = read_recent_logs(log_path, after_ts="2026-01-01T00:00:01Z")
        assert len(result) == 1
        assert result[0]["message"] == "new"

    def test_missing_file_returns_empty(self, tmp_path: Path):
        result = read_recent_logs(tmp_path / "nope.jsonl")
        assert result == []

    def test_handles_corrupt_lines(self, tmp_path: Path):
        log_path = tmp_path / "test.jsonl"
        log_path.write_text('{"message":"ok"}\nnot json\n{"message":"also ok"}\n')
        result = read_recent_logs(log_path)
        assert len(result) == 2


class TestTruncation:
    def test_truncates_on_install(self, tmp_path: Path):
        log_path = tmp_path / "gaming_pipeline_logs.jsonl"
        # Write more than MAX_LOG_LINES
        with open(log_path, "w") as f:
            for i in range(MAX_LOG_LINES + 500):
                f.write(json.dumps({"ts": "t", "message": f"line_{i}"}) + "\n")

        handler, _, _ = install_log_handler("gaming", "run_trunc", tmp_path)
        remove_log_handler(handler)

        # File should be truncated to MAX_LOG_LINES + sentinel
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) <= MAX_LOG_LINES + 1  # +1 for sentinel
