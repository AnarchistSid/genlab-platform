"""Tests for genlab_core.observability.logging."""

from __future__ import annotations

import json
import logging

import pytest
from genlab_core.observability.logging import configure_logging


class TestConfigureLogging:
    """Verify that configure_logging sets up structlog wrapping stdlib."""

    def teardown_method(self):
        """Reset root logger to avoid bleeding between tests."""
        root = logging.getLogger()
        root.handlers.clear()
        root.setLevel(logging.WARNING)

    def test_json_output_produces_json(self, capsys):
        """With json_output=True, stdlib logger output should be valid JSON."""
        configure_logging(json_output=True, level=logging.DEBUG)

        test_logger = logging.getLogger("test.json_check")
        test_logger.info("hello world", extra={})

        captured = capsys.readouterr()
        # structlog writes to stderr via StreamHandler
        line = captured.err.strip().split("\n")[-1]
        parsed = json.loads(line)
        assert parsed["event"] == "hello world"
        assert "level" in parsed

    def test_console_output_is_not_json(self, capsys):
        """With json_output=False, output should be human-readable (not JSON)."""
        configure_logging(json_output=False, level=logging.DEBUG)

        test_logger = logging.getLogger("test.console_check")
        test_logger.info("console message")

        captured = capsys.readouterr()
        line = captured.err.strip().split("\n")[-1]

        # Console output should NOT be valid JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(line)

        # But should contain the message
        assert "console message" in line

    def test_level_filtering(self, capsys):
        """Messages below the configured level should be suppressed."""
        configure_logging(json_output=True, level=logging.WARNING)

        test_logger = logging.getLogger("test.level_filter")
        test_logger.info("should be hidden")
        test_logger.warning("should appear")

        captured = capsys.readouterr()
        assert "should be hidden" not in captured.err
        assert "should appear" in captured.err

    def test_timestamp_present_in_json(self, capsys):
        """JSON output should include an ISO timestamp."""
        configure_logging(json_output=True, level=logging.DEBUG)

        logging.getLogger("test.ts").info("with timestamp")

        captured = capsys.readouterr()
        line = captured.err.strip().split("\n")[-1]
        parsed = json.loads(line)
        assert "timestamp" in parsed

    def test_logger_name_present_in_json(self, capsys):
        """JSON output should include the logger name."""
        configure_logging(json_output=True, level=logging.DEBUG)

        logging.getLogger("myapp.module").info("named logger")

        captured = capsys.readouterr()
        line = captured.err.strip().split("\n")[-1]
        parsed = json.loads(line)
        assert parsed.get("logger") == "myapp.module"

    def test_existing_getlogger_works_after_configure(self, capsys):
        """Pre-existing logging.getLogger() calls should still work."""
        # Simulate existing code pattern
        legacy_logger = logging.getLogger("genlab_core.pipeline.stage_runner")

        configure_logging(json_output=True, level=logging.DEBUG)
        legacy_logger.info("[Pipeline] Running stage: %s", "FetchVideos")

        captured = capsys.readouterr()
        line = captured.err.strip().split("\n")[-1]
        parsed = json.loads(line)
        assert "FetchVideos" in parsed["event"]

    def test_structlog_logger_works(self, capsys):
        """structlog.get_logger() should produce structured output too."""
        import structlog

        configure_logging(json_output=True, level=logging.DEBUG)
        slog = structlog.get_logger("test.structlog")
        slog.info("structured event", count=42)

        captured = capsys.readouterr()
        line = captured.err.strip().split("\n")[-1]
        parsed = json.loads(line)
        assert parsed["event"] == "structured event"
        assert parsed["count"] == 42
