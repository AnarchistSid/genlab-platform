"""Alert value object shared by every health-monitor check.

Extracted from ``health_monitor.py`` (2026-07-08 god-module split, DEV-1)
so each ``checks/*.py`` submodule can import ``Alert`` without importing
the facade module — the facade re-exports the check functions via
wildcard, so a `from .health_monitor import Alert` at the check-module
level would round-trip through a circular import.
"""

from __future__ import annotations


class Alert:
    """A detected health issue."""

    def __init__(
        self,
        check: str,
        severity: str,
        message: str,
        niche_id: str = "",
        details: dict | None = None,
        auto_fix: str = "",
    ):
        self.check = check
        self.severity = severity  # "critical" or "warning"
        self.message = message
        self.niche_id = niche_id
        self.details = details or {}
        self.auto_fix = auto_fix

    def __repr__(self) -> str:
        n = f"[{self.niche_id}] " if self.niche_id else ""
        fix = f" (auto-fix: {self.auto_fix})" if self.auto_fix else ""
        return f"[{self.severity.upper()}] {n}{self.check}: {self.message}{fix}"
