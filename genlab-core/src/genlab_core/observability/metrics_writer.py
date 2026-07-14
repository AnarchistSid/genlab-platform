"""Pipeline metrics JSONL writer.

Records per-stage timing, status, and arbitrary metadata, then flushes to
a ``metrics.jsonl`` file inside the run directory.

Usage::

    metrics = PipelineMetrics(niche_id="gaming", run_id="20260317_0630")
    metrics.record_stage("FetchTrendingVideos", duration_ms=1234.5, status="ok")
    metrics.record_stage("ScoreAndFilter", duration_ms=567.8, status="ok", items=12)
    metrics.flush(run_dir=Path(".tmp/runs/20260317_0630"))

The flush writes one JSON object per line — compatible with ``jq``,
Elasticsearch, Loki, and any JSONL consumer.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

METRICS_FILENAME = "metrics.jsonl"


@dataclass
class StageMetric:
    """A single stage measurement."""

    stage: str
    duration_ms: float
    status: str  # "ok" | "error" | "skipped"
    timestamp_iso: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stage": self.stage,
            "duration_ms": round(self.duration_ms, 2),
            "status": self.status,
            "timestamp": self.timestamp_iso,
        }
        d.update(self.extra)
        return d


class PipelineMetrics:
    """Collects per-stage metrics and flushes them to a JSONL file.

    Thread-safe for basic append operations (list.append is atomic in
    CPython), which is sufficient since stages run one at a time or in
    a thread pool that appends results sequentially.
    """

    def __init__(
        self,
        *,
        niche_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self._niche_id = niche_id
        self._run_id = run_id
        self._entries: list[StageMetric] = []
        self._pipeline_start: float | None = None
        self._pipeline_end: float | None = None
        # C3 (2026-06-30): per-stage drop counts BY content_type. Lets
        # operators see "RelevanceGate dropped 8 of 12 showcase items
        # but only 1 of 8 news items" — actionable signal vs the
        # current aggregated "RelevanceGate dropped 9 of 23 stories".
        # Shape: {stage_name: {content_type: {before, after, dropped}}}
        self._filter_drops_by_content_type: dict[str, dict[str, dict[str, int]]] = {}

    @property
    def entries(self) -> list[StageMetric]:
        """Read-only access to recorded entries (for testing / inspection)."""
        return list(self._entries)

    @property
    def filter_drops_by_content_type(self) -> dict[str, dict[str, dict[str, int]]]:
        """Read-only access to per-stage per-content_type drop counts.

        Shape::

            {
              "RelevanceGate": {
                "showcase": {"before": 12, "after": 4, "dropped": 8},
                "news":     {"before":  8, "after": 7, "dropped": 1},
                "unknown":  {"before":  3, "after": 3, "dropped": 0},
              },
              "VideoGate":     {...},
              "PushToBacklog": {...},
            }

        Empty dict when no stages have called ``record_filter_drops``.
        """
        # Deep copy so consumers can't mutate internal state.
        return {
            stage: {ct: dict(counts) for ct, counts in by_type.items()}
            for stage, by_type in self._filter_drops_by_content_type.items()
        }

    def record_filter_drops(
        self,
        stage_name: str,
        before: list[Any],
        after: list[Any],
    ) -> None:
        """Record per-content_type drop counts for a filter stage.

        Counts ``before`` and ``after`` lists by each item's
        ``content_type`` field (defaulting to ``"unknown"`` when missing
        or non-truthy), then stores the diff in
        ``filter_drops_by_content_type[stage_name]``.

        Stages don't need to know HOW the count is bucketed — they just
        hand over the input + output lists and the writer does the math.
        Idempotent + last-write-wins per stage name; safe to call once
        per stage execution. Calling twice with the same stage_name
        OVERWRITES the previous record (matches the semantic of "this
        stage just ran; here are its drops").

        Items can be dicts (the common case in the pipeline — stories
        are dicts) OR objects with a ``content_type`` attribute (future-
        proofing for typed StoryCandidate migration). Anything else
        buckets as ``"unknown"``.

        Parameters
        ----------
        stage_name:
            Stage class name (e.g. ``"RelevanceGate"``). Used as the
            top-level key in the output dict.
        before:
            Stories / items entering the stage. ``len(before)`` becomes
            the ``before`` count for each content_type bucket.
        after:
            Stories / items leaving the stage (after filtering /
            deduping). ``len(after)`` becomes the ``after`` count.
            ``dropped = before - after`` per bucket.
        """

        def _content_type_of(item: Any) -> str:
            """Extract content_type defensively, defaulting to 'unknown'."""
            if isinstance(item, dict):
                ct = item.get("content_type")
            else:
                ct = getattr(item, "content_type", None)
            if not ct or not isinstance(ct, str):
                return "unknown"
            return ct

        before_counts: dict[str, int] = {}
        for item in before:
            ct = _content_type_of(item)
            before_counts[ct] = before_counts.get(ct, 0) + 1

        after_counts: dict[str, int] = {}
        for item in after:
            ct = _content_type_of(item)
            after_counts[ct] = after_counts.get(ct, 0) + 1

        # Union of seen content_types so buckets present in only one
        # list (e.g. all 'news' items dropped → 'news' only in before)
        # still appear with after=0.
        all_types = set(before_counts) | set(after_counts)
        by_type: dict[str, dict[str, int]] = {}
        for ct in all_types:
            b = before_counts.get(ct, 0)
            a = after_counts.get(ct, 0)
            by_type[ct] = {
                "before": b,
                "after": a,
                "dropped": max(0, b - a),
            }
        self._filter_drops_by_content_type[stage_name] = by_type
        logger.debug(
            "Recorded filter drops for %s: %d content_type bucket(s)",
            stage_name,
            len(by_type),
        )

    def record_stage(
        self,
        stage: str,
        *,
        duration_ms: float,
        status: str,
        **kwargs: Any,
    ) -> None:
        """Record a completed stage execution.

        Parameters
        ----------
        stage:
            Class name or human-readable label for the stage.
        duration_ms:
            Wall-clock duration in milliseconds.
        status:
            ``"ok"``, ``"error"``, or ``"skipped"``.
        **kwargs:
            Arbitrary extra fields (e.g. ``items=12``, ``error_msg="..."``).
        """
        from datetime import datetime

        entry = StageMetric(
            stage=stage,
            duration_ms=duration_ms,
            status=status,
            timestamp_iso=datetime.now(UTC).isoformat(),
            extra=kwargs,
        )
        self._entries.append(entry)
        logger.debug(
            "Recorded metric: stage=%s duration_ms=%.1f status=%s",
            stage,
            duration_ms,
            status,
        )

    @contextmanager
    def time_stage(self, stage: str, **kwargs: Any) -> Generator[dict[str, Any], None, None]:
        """Context manager that auto-records timing for a stage.

        Yields a mutable dict where the caller can stash extra fields
        (e.g. item counts) that will be included in the metric.

        On normal exit the status is ``"ok"``; on exception it is ``"error"``
        (and the exception is re-raised).

        2026-07-14 (pipeline audit): callers can set
        ``extra["_status"] = "skipped"`` on the yielded dict to mark the
        stage as skipped (flag-gated no-op, config-disabled, etc.). Prior
        state hard-coded ``ok`` — dashboard couldn't distinguish "stage
        ran + succeeded" from "stage silently no-op'd". Class-of-bug:
        observability records success on non-execution.

        Usage::

            with metrics.time_stage("FetchTrendingVideos") as extra:
                if flag_disabled:
                    extra["_status"] = "skipped"
                    return
                videos = fetcher.fetch()
                extra["items"] = len(videos)
        """
        extra: dict[str, Any] = dict(kwargs)
        t0 = time.monotonic()
        try:
            yield extra
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            # Pop the sentinel so it doesn't land as a spurious extras field.
            status = extra.pop("_status", "ok")
            self.record_stage(stage, duration_ms=elapsed_ms, status=status, **extra)
        except Exception:
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            extra.pop("_status", None)  # error wins over caller-set skipped
            self.record_stage(stage, duration_ms=elapsed_ms, status="error", **extra)
            raise

    def mark_pipeline_start(self) -> None:
        """Record the wall-clock start of the full pipeline."""
        self._pipeline_start = time.monotonic()

    def mark_pipeline_end(self) -> None:
        """Record the wall-clock end of the full pipeline."""
        self._pipeline_end = time.monotonic()

    def flush(self, run_dir: Path) -> Path:
        """Write all recorded metrics to ``<run_dir>/metrics.jsonl``.

        Creates the run directory if it does not exist.  Returns the
        path to the written file.

        Each line is a self-contained JSON object.  A summary line is
        appended at the end with pipeline-level totals.
        """
        run_dir.mkdir(parents=True, exist_ok=True)
        out_path = run_dir / METRICS_FILENAME

        lines: list[str] = []
        for entry in self._entries:
            row = entry.to_dict()
            if self._niche_id:
                row["niche_id"] = self._niche_id
            if self._run_id:
                row["run_id"] = self._run_id
            lines.append(json.dumps(row, separators=(",", ":")))

        # Append pipeline summary
        total_ms = sum(e.duration_ms for e in self._entries)
        ok_count = sum(1 for e in self._entries if e.status == "ok")
        error_count = sum(1 for e in self._entries if e.status == "error")

        summary: dict[str, Any] = {
            "_type": "pipeline_summary",
            "total_stages": len(self._entries),
            "ok": ok_count,
            "errors": error_count,
            "total_duration_ms": round(total_ms, 2),
        }
        if self._pipeline_start is not None and self._pipeline_end is not None:
            wall_ms = (self._pipeline_end - self._pipeline_start) * 1000.0
            summary["wall_clock_ms"] = round(wall_ms, 2)
        if self._niche_id:
            summary["niche_id"] = self._niche_id
        if self._run_id:
            summary["run_id"] = self._run_id
        lines.append(json.dumps(summary, separators=(",", ":")))

        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(
            "Flushed %d stage metrics to %s",
            len(self._entries),
            out_path,
        )
        return out_path
