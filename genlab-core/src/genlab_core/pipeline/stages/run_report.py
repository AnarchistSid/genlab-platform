"""Pipeline stage: Write per-run JSON summary report.

Collects metrics from all previous stages via context['run_stats'] and
writes a comprehensive run_report.json to the run directory.

Captures:
  - Source fetch stats
  - Scoring/ranking metrics
  - QC gate results
  - Virality scoring averages
  - Video validation stats
  - Audio generation stats
  - Publishing results
  - Insight fetch results
  - Per-stage timing from _stage_timings
  - SLO compliance checks

Non-fatal: report write failure is logged but never crashes pipeline.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default P95 pipeline target (seconds)
DEFAULT_P95_TARGET = 600


class RunReport:
    """Write per-run JSON summary to .tmp/runs/<run_id>/run_report.json.

    Reads: context['run_stats'], context['stories'],
           context['niche_config']
    Writes: context['run_stats']['report_path']
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        run_stats = context.get("run_stats", {})
        niche_config = context.get("niche_config", {})
        niche_id = context.get("niche_id") or niche_config.get("niche_id", "unknown")
        stories = context.get("stories", [])
        blueprints_pushed = run_stats.get("backlog_push", {}).get("blueprints_pushed", 0)

        now = datetime.now(UTC)

        # Derive run timing
        stage_timings = run_stats.get("_stage_timings", {})
        total_duration = sum(stage_timings.values()) if stage_timings else 0

        # Determine run_id from context (top-level), then run_stats, then generate
        run_id = context.get("run_id") or run_stats.get(
            "run_id", f"{niche_id}_{now.strftime('%Y%m%d_%H%M%S')}",
        )

        # Collect sub-stage stats
        qc = run_stats.get("qc", {})
        virality = run_stats.get("virality", {})
        video_val = run_stats.get("video_validation", {})
        audio = run_stats.get("audio", {})
        overlays = run_stats.get("text_overlays", {})
        insights = run_stats.get("insights", {})
        publishing = run_stats.get("publishing", {})
        express = run_stats.get("express_lane", {})

        # SLO checks
        p95_target = niche_config.get("error_budgets", {}).get(
            "duration_p95", DEFAULT_P95_TARGET,
        )
        slo_violations = []
        if total_duration > p95_target:
            slo_violations.append(
                f"Pipeline {total_duration:.0f}s exceeds {p95_target}s P95 target"
            )

        qc_total = qc.get("total", 0)
        qc_passed = qc.get("passed", 0)
        if qc_total > 0 and (qc_passed / qc_total) < 0.90:
            slo_violations.append(
                f"QC pass rate {qc_passed}/{qc_total} below 90% SLO"
            )

        # Zero-blueprint SLO: if we fetched stories but produced no blueprints,
        # the pipeline is broken in a hidden way (e.g. yt-dlp bot-detection
        # cascade, writing-stage cascade, over-aggressive dedup). Flag it.
        zero_blueprint_cascade = len(stories) > 0 and blueprints_pushed == 0
        if zero_blueprint_cascade:
            downloaded = video_val.get("passed", 0)
            slo_violations.append(
                f"Zero blueprints produced from {len(stories)} stories "
                f"(videos_validated={downloaded})"
            )

        # Determine status
        has_errors = bool(run_stats.get("errors"))
        if has_errors and not stories:
            status = "failed"
        elif zero_blueprint_cascade:
            status = "failed"  # success on paper but produced nothing
        elif has_errors:
            status = "partial"
        else:
            status = "success"

        report = {
            "run_id": run_id,
            "niche_id": niche_id,
            "timestamp": now.isoformat(),
            "duration_seconds": round(total_duration, 1),
            "status": status,
            "metrics": {
                "stories_count": len(stories),
                "blueprints_count": blueprints_pushed,
                "qc": qc,
                "virality": virality,
                "video_validation": video_val,
                "audio": audio,
                "text_overlays": overlays,
                "insights": insights,
                "publishing": publishing,
                "express_lane": express,
            },
            "stage_timings": stage_timings,
            "slo_violations": slo_violations,
        }

        # Write to run directory — prefer context's run_dir (set by pipeline_runner)
        try:
            ctx_run_dir = context.get("run_dir")
            run_dir = Path(ctx_run_dir) if ctx_run_dir else self._resolve_run_dir(niche_id, run_id)
            run_dir.mkdir(parents=True, exist_ok=True)
            report_path = run_dir / "run_report.json"
            report_path.write_text(json.dumps(report, indent=2, default=str))
            run_stats["report_path"] = str(report_path)
            logger.info("[RunReport] Written: %s", report_path)
        except Exception:
            logger.exception("[RunReport] Failed to write report")

        # Log summary
        logger.info(
            "[RunReport] %s | %s | %.0fs | stories=%d blueprints=%d | "
            "QC: %s | violations=%d",
            niche_id, status, total_duration,
            len(stories), blueprints_pushed,
            qc.get("pass_rate", "n/a"),
            len(slo_violations),
        )

        if slo_violations:
            for v in slo_violations:
                logger.warning("[RunReport] SLO VIOLATION: %s", v)

        # Push dashboard notification event
        try:
            from genlab_core.observability.dashboard_events import push_event
            push_event(
                "pipeline_complete",
                f"Pipeline Complete: {niche_id}",
                f"{blueprints_pushed} blueprints, {len(stories)} stories in {total_duration:.0f}s",
                entity_id=run_id,
                entity_type="pipeline_run",
                niche_id=niche_id,
            )
        except Exception:
            pass  # non-fatal

        return context

    @staticmethod
    def _resolve_run_dir(niche_id: str, run_id: str) -> Path:
        """Find or create the run output directory."""
        # Try standard .tmp/runs/ location relative to workspace
        from genlab_core.settings import settings
        try:
            root = settings.get_project_root()
            return root / ".tmp" / "runs" / run_id
        except Exception:
            # Fallback to /tmp
            return Path("/tmp") / "genlab_runs" / run_id
