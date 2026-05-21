"""Pipeline execution context — shared state passed between stages.

Every pipeline run creates a single PipelineContext at startup.
Stages read/write stories, blueprints, errors, and stats through it.

Deep utility code that needs the current niche_id or run_id without
explicit parameter threading can use the ContextVar accessor:

    from core.context import get_current_context
    ctx = get_current_context()
    if ctx:
        niche = ctx.niche_id

Usage:
    from core.context import PipelineContext, set_current_context

    ctx = PipelineContext(niche_id="ai_creators", run_id="20260305_120000")
    token = set_current_context(ctx)
    try:
        run_pipeline(ctx)
    finally:
        _current_context.reset(token)
"""

from __future__ import annotations

import traceback
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class PipelineContext:
    """Mutable state bag for a single pipeline run.

    Required fields (must be provided at construction):
        niche_id:  Which niche this run targets ("ai_creators", "gaming", etc.)
        run_id:    Unique run identifier (typically YYYYMMDD_HHMMSS)

    All other fields have safe defaults and are populated as stages execute.
    """

    # ── Required ──────────────────────────────────────────────
    niche_id: str
    run_id: str

    # ── Stage data (populated progressively) ──────────────────
    stories: list[dict[str, Any]] = field(default_factory=list)
    blueprints: list[dict[str, Any]] = field(
        default_factory=list
    )  # Legacy — stages use context['stories']

    # ── Accumulated metrics per stage ─────────────────────────
    run_stats: dict[str, Any] = field(default_factory=dict)

    # ── Feature flags (loaded from niche config at startup) ───
    feature_flags: dict[str, bool] = field(default_factory=dict)

    # ── Niche-specific config snapshot ────────────────────────
    niche_config: dict[str, Any] = field(default_factory=dict)

    # ── Error log ─────────────────────────────────────────────
    errors: list[dict[str, Any]] = field(default_factory=list)

    # ── Timing ────────────────────────────────────────────────
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # ── Abort flag (set by record_error with fatal=True) ──────
    is_aborted: bool = field(default=False, repr=False)

    # ------------------------------------------------------------------
    # Error recording
    # ------------------------------------------------------------------
    def record_error(
        self,
        stage: str,
        error: Exception,
        *,
        fatal: bool = False,
    ) -> None:
        """Append a structured error entry.

        Args:
            stage: Pipeline stage name (e.g. "fetch_ai_creators").
            error: The exception that occurred.
            fatal: If True, sets ``is_aborted`` so the runner stops.
        """
        self.errors.append(
            {
                "stage": stage,
                "error_type": type(error).__name__,
                "message": str(error),
                "traceback": traceback.format_exception(error),
                "fatal": fatal,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        if fatal:
            self.is_aborted = True

    # ------------------------------------------------------------------
    # Feature flags
    # ------------------------------------------------------------------
    def get_flag(self, flag_name: str, default: bool = False) -> bool:
        """Check whether a feature flag is enabled for this run.

        Stages use this instead of reading YAML directly, so flags can be
        overridden per-niche or per-run without touching config files.
        """
        return bool(self.feature_flags.get(flag_name, default))


# ---------------------------------------------------------------------------
# ContextVar — ambient access to the current PipelineContext
# ---------------------------------------------------------------------------
_current_context: ContextVar[PipelineContext | None] = ContextVar("current_context", default=None)


def set_current_context(ctx: PipelineContext) -> Token[PipelineContext | None]:
    """Set the current pipeline context for this execution scope.

    Returns a Token that can be passed to ``_current_context.reset(token)``
    to restore the previous value (important for tests and nested runs).
    """
    return _current_context.set(ctx)


def get_current_context() -> PipelineContext | None:
    """Return the active PipelineContext, or None if not inside a run."""
    return _current_context.get()
