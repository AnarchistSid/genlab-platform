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

from genlab_core.intelligence.cost_accumulator import get_accumulator
from genlab_core.pipeline.stage_context import StageContext

logger = logging.getLogger(__name__)

# Default P95 pipeline target (seconds)
DEFAULT_P95_TARGET = 600

# R-27: default daily LLM-cost budget (USD). The cost rule across CLAUDE.md
# and ``.claude/rules/optimization.md`` is "<$5/day". Treated as a per-run
# soft ceiling — going over is a SLO violation, not a pipeline failure.
DEFAULT_BUDGET_USD = 5.0


class RunReport:
    """Write per-run JSON summary to .tmp/runs/<run_id>/run_report.json.

    Reads: context['run_stats'], context['stories'],
           context['niche_config']
    Writes: context['run_stats']['report_path']
    """

    def execute(self, context: StageContext) -> StageContext:
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
            "run_id",
            f"{niche_id}_{now.strftime('%Y%m%d_%H%M%S')}",
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
            "duration_p95",
            DEFAULT_P95_TARGET,
        )
        slo_violations = []
        if total_duration > p95_target:
            slo_violations.append(
                f"Pipeline {total_duration:.0f}s exceeds {p95_target}s P95 target"
            )

        qc_total = qc.get("total", 0)
        qc_passed = qc.get("passed", 0)
        if qc_total > 0 and (qc_passed / qc_total) < 0.90:
            slo_violations.append(f"QC pass rate {qc_passed}/{qc_total} below 90% SLO")

        # Zero-blueprint SLO (R-65): a video-first pipeline that produces no
        # blueprint had a dark day — whether it fetched stories and dropped
        # them all, OR fetched nothing at all. The check previously required
        # stories>0, so a TOTAL fetch-wipeout (0 stories, 0 errors) slipped
        # through as "success" and the dashboard showed the niche "healthy".
        zero_blueprints = blueprints_pushed == 0
        if zero_blueprints:
            downloaded = video_val.get("passed", 0)
            if len(stories) > 0:
                slo_violations.append(
                    f"Zero blueprints produced from {len(stories)} stories "
                    f"(videos_validated={downloaded})"
                )
            else:
                slo_violations.append(
                    "Zero blueprints produced — no stories fetched "
                    "(total fetch wipeout: WARP/quota/relevance gate?)"
                )

        # R-27: read the per-run cost accumulator if one is installed
        # (pipeline_runner.run() installs it; tests run RunReport in
        # isolation may not). The structural blindness the audit called
        # out was that ``get_accumulator()`` returned ``None`` because
        # ``set_accumulator()`` was never wired — that's fixed in
        # pipeline_runner.run(). Treat ``None`` here defensively so
        # standalone tests / future entry points don't crash.
        cost_summary: dict = {}
        try:
            acc = get_accumulator()
            if acc is not None:
                cost_summary = acc.summary()
                budget_cap = niche_config.get("error_budgets", {}).get(
                    "daily_cost_usd", DEFAULT_BUDGET_USD
                )
                if cost_summary.get("total_usd", 0.0) > budget_cap:
                    slo_violations.append(
                        f"LLM cost ${cost_summary['total_usd']:.4f} exceeds "
                        f"${budget_cap:.2f} per-run budget"
                    )
        except Exception:
            logger.debug("[RunReport] cost-accumulator read failed", exc_info=True)

        # Determine status. A dark day (0 blueprints) is always a failure,
        # regardless of story count or errors (R-65) — this is the signal an
        # operator needs to know a channel produced nothing today.
        has_errors = bool(run_stats.get("errors"))

        # RENDER #2 fix (2026-06-13): propagate per-stage failures into the
        # run status. Pre-fix, sub-stage failures (whisper captions timing
        # out and producing corrupt _captioned.mp4, video_val rejecting
        # outputs, audio TTS errors) were invisible to the status
        # determination — the run report said "status: success" even when
        # half the captioned MP4s had no moov atom. Today's 4-agent audit
        # showed 6 of 8 VISUAL_READY blueprints failing quality bar while
        # every run reported success.
        #
        # New semantic: any non-zero `failed` or `errors` count in any
        # tracked sub-stage promotes the status to "partial" (even when
        # blueprints WERE pushed), so the dashboard QC banner catches it
        # before publish. Per-stage failure counts are exposed via a new
        # `stage_failures` field at the top level of the report so the
        # dashboard can show what specifically went wrong.
        stage_failures: dict[str, int] = {}
        for stage_name, stats_dict in (
            ("video_validation", video_val),
            ("audio", audio),
            ("text_overlays", overlays),
            ("whisper_captions", run_stats.get("whisper_captions", {}) or {}),
            ("publishing", publishing),
        ):
            if not isinstance(stats_dict, dict):
                continue
            failed = int(stats_dict.get("failed", 0) or 0)
            errors = int(stats_dict.get("errors", 0) or 0)
            total = failed + errors
            if total > 0:
                stage_failures[stage_name] = total

        # QC failures: surface so the dashboard knows whether "partial"
        # was driven by writing-side issues vs render-side issues.
        qc_failed = int(qc.get("failed", 0) or 0)
        if qc_failed > 0:
            stage_failures["qc"] = qc_failed

        if zero_blueprints:
            status = "failed"
        elif has_errors or stage_failures:
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
            # R-27: ``cost`` carries ``total_usd``, ``by_category``,
            # ``budget_remaining_pct``, and ``entry_count``. Empty dict
            # means "no accumulator was active" — distinct from
            # ``total_usd=0.0`` which means "accumulator active, no
            # LLM calls were made this run".
            "cost": cost_summary,
            "stage_timings": stage_timings,
            "slo_violations": slo_violations,
            # RENDER #2: per-stage failure attribution. Empty dict means
            # every tracked sub-stage was clean. Non-empty means status was
            # promoted from "success" to "partial". Dashboard reads this to
            # render a "this run had silent failures in X, Y, Z" badge.
            "stage_failures": stage_failures,
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

        # Also expose the report dict in run_stats so downstream stages
        # (and tests) can inspect it without re-reading from disk. The
        # `report_path` field above stays the source of truth for the
        # dashboard which reads from disk.
        run_stats["report"] = report

        # Log summary
        logger.info(
            "[RunReport] %s | %s | %.0fs | stories=%d blueprints=%d | QC: %s | violations=%d",
            niche_id,
            status,
            total_duration,
            len(stories),
            blueprints_pushed,
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
