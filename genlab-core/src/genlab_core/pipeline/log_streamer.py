"""Pipeline log streaming — writes structured JSONL for dashboard consumption.

Provides a logging.Handler that writes JSON log records to a JSONL file.
The dashboard watcher thread tails this file and emits Socket.IO events.

Usage in pipeline_runner.py:
    handler, log_path = install_log_handler(niche_id, run_id, log_dir)
    try:
        ...run stages...
    finally:
        remove_log_handler(handler)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum lines kept in log file (older lines truncated on handler install)
MAX_LOG_LINES = 2000


class PipelineLogHandler(logging.Handler):
    """Write structured JSON log records to a JSONL file.

    Each line is a JSON object:
        {"ts": "ISO8601", "level": "INFO", "logger": "name",
         "message": "text", "run_id": "...", "niche_id": "...",
         "stage": "StageName" or null}

    The ``stage`` field is populated from the log record's ``stage``
    extra key if present (set by StageRunner via LoggingFilter).
    """

    def __init__(
        self,
        log_path: Path,
        run_id: str,
        niche_id: str,
    ) -> None:
        super().__init__(level=logging.DEBUG)
        self._log_path = log_path
        self._run_id = run_id
        self._niche_id = niche_id
        self._file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
                "run_id": self._run_id,
                "niche_id": self._niche_id,
                "stage": getattr(record, "stage", None),
            }
            line = json.dumps(entry, ensure_ascii=False)
            self._file.write(line + "\n")
            self._file.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass
        super().close()


class StageLoggingFilter(logging.Filter):
    """Inject ``stage`` field into log records during stage execution."""

    def __init__(self) -> None:
        super().__init__()
        self.current_stage: str | None = None

    def filter(self, record: logging.LogRecord) -> bool:
        record.stage = self.current_stage  # type: ignore[attr-defined]
        return True


def install_log_handler(
    niche_id: str,
    run_id: str,
    log_dir: Path,
) -> tuple[PipelineLogHandler, StageLoggingFilter, Path]:
    """Install a PipelineLogHandler on the root logger.

    Truncates the log file if it exceeds MAX_LOG_LINES.

    Returns:
        (handler, stage_filter, log_path)
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{niche_id}_pipeline_logs.jsonl"

    # Truncate to keep file manageable
    _truncate_if_needed(log_path)

    # Write a run-start sentinel
    with open(log_path, "a", encoding="utf-8") as f:
        sentinel = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": "INFO",
            "logger": "pipeline",
            "message": f"=== Pipeline run started: {run_id} ===",
            "run_id": run_id,
            "niche_id": niche_id,
            "stage": None,
            "_type": "run_start",
        }
        f.write(json.dumps(sentinel, ensure_ascii=False) + "\n")

    handler = PipelineLogHandler(log_path, run_id, niche_id)
    handler.setFormatter(logging.Formatter("%(message)s"))

    stage_filter = StageLoggingFilter()
    handler.addFilter(stage_filter)

    logging.getLogger().addHandler(handler)
    return handler, stage_filter, log_path


def remove_log_handler(handler: PipelineLogHandler) -> None:
    """Remove the handler from root logger and close it."""
    logging.getLogger().removeHandler(handler)
    handler.close()


def read_recent_logs(
    log_path: Path,
    *,
    limit: int = 200,
    after_ts: str | None = None,
) -> list[dict[str, Any]]:
    """Read the most recent log entries from a JSONL file.

    Args:
        log_path: Path to the JSONL file.
        limit: Max entries to return.
        after_ts: If set, only return entries with ts > after_ts.

    Returns:
        List of parsed log dicts, newest last.
    """
    if not log_path.exists():
        return []

    lines: list[str] = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            # Read last `limit * 2` lines (buffer for filtering)
            all_lines = f.readlines()
            lines = all_lines[-(limit * 2):]
    except OSError:
        return []

    entries: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if after_ts and entry.get("ts", "") <= after_ts:
            continue
        entries.append(entry)

    return entries[-limit:]


def _truncate_if_needed(log_path: Path) -> None:
    """Keep only the last MAX_LOG_LINES lines in the log file."""
    if not log_path.exists():
        return
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LOG_LINES:
            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_LOG_LINES:])
    except OSError:
        pass
