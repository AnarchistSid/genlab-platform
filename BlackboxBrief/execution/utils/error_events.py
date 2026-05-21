"""Structured error event helpers for operational pipeline logging."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class ErrorEvent:
    """Normalized machine-readable error event."""

    code: str
    message: str
    component: str
    severity: str = "error"
    run_id: Optional[str] = None
    candidate_id: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )

    def to_dict(self) -> Dict[str, Any]:
        event = {
            "event_type": "pipeline_error",
            "timestamp": self.timestamp,
            "code": self.code,
            "message": self.message,
            "component": self.component,
            "severity": self.severity,
            "details": self.details,
        }
        if self.run_id:
            event["run_id"] = self.run_id
        if self.candidate_id:
            event["candidate_id"] = self.candidate_id
        if self.exception_type:
            event["exception_type"] = self.exception_type
        if self.exception_message:
            event["exception_message"] = self.exception_message
        return event


def emit_error_event(
    logger: Any,
    *,
    code: str,
    message: str,
    component: str,
    severity: str = "error",
    run_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    details: Optional[Mapping[str, Any]] = None,
    exc: Optional[BaseException] = None,
) -> Dict[str, Any]:
    """Emit a structured error event and return the emitted payload."""

    event = ErrorEvent(
        code=code,
        message=message,
        component=component,
        severity=severity,
        run_id=run_id,
        candidate_id=candidate_id,
        details=dict(details or {}),
        exception_type=type(exc).__name__ if exc else None,
        exception_message=str(exc) if exc else None,
    ).to_dict()

    log_method = logger.error if severity in {"error", "critical"} else logger.warning
    log_method("error_event=%s", json.dumps(event, sort_keys=True), exc_info=bool(exc))
    return event
