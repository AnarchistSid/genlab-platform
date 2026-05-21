"""Prefect 3.x flow wrapping the full CriticalRush gaming pipeline.

Each pipeline stage becomes a Prefect @task so the Prefect UI shows
per-stage timing, retries, and failure isolation.

Stages are loaded from niche.yaml's pipeline.stages list — the same
source of truth that pipeline_runner.py uses. Prefect-specific metadata
(retries, retry_delay_seconds) is also read from niche.yaml entries.

Usage (CLI):
    python -m niches.gaming.flows.gaming_flow              # run once
    python -m niches.gaming.flows.gaming_flow --dry-run    # dry run

Usage (Prefect):
    Registered via deployment_manager as "gaming-scheduled" and "gaming-manual".
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

try:
    from prefect import flow, task
except ImportError:
    # Prefect eliminated in Sprint 68 — decorators are no-ops
    def flow(fn=None, **kwargs):
        return fn if fn else lambda f: f

    def task(fn=None, **kwargs):
        return fn if fn else lambda f: f


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
NICHE_ID = "gaming"


# ---------------------------------------------------------------------------
# Stage loading — single source of truth: niche.yaml
# ---------------------------------------------------------------------------


def _load_stages_from_config() -> list[dict[str, Any]]:
    """Load pipeline stages from niche.yaml — single source of truth.

    Each entry has at minimum a 'class' key (fully-qualified class path)
    and optionally 'retries' and 'retry_delay_seconds' for Prefect config.

    Returns:
        List of stage dicts, e.g.
        [{"class": "niches.gaming.stages.fetch_gaming_stories.FetchGamingStories",
          "retries": 1, "retry_delay_seconds": 30}, ...]
    """
    niche_yaml = PROJECT_ROOT / "niches" / NICHE_ID / "config" / "niche.yaml"
    with open(niche_yaml) as f:
        config = yaml.safe_load(f)

    stages = config.get("pipeline", {}).get("stages")
    if not stages:
        raise RuntimeError(
            f"No pipeline.stages found in {niche_yaml}. Check niche.yaml pipeline.stages list."
        )
    return stages


def _make_task(stage_module: str, stage_class: str, **task_kwargs):
    """Lazy-import a stage class and wrap its execute() as a Prefect task."""

    @task(name=stage_class, **task_kwargs)
    def run(context: dict[str, Any]) -> dict[str, Any]:
        import importlib

        mod = importlib.import_module(stage_module)
        cls = getattr(mod, stage_class)
        stage = cls()
        return stage.execute(context)

    return run


def _build_task_list() -> list:
    """Build Prefect task list from niche.yaml stage definitions.

    Reads class paths and optional Prefect metadata (retries, retry_delay_seconds)
    from niche.yaml. Tags are derived from the niche ID and the class name.
    """
    stages = _load_stages_from_config()
    tasks = []

    for entry in stages:
        class_path = entry["class"]
        module_path, class_name = class_path.rsplit(".", 1)

        # Derive a tag from the class name (e.g., FetchGamingStories → fetch)
        tag_hint = class_name.replace("Gaming", "").replace("Stories", "").lower()

        task_kwargs: dict[str, Any] = {"tags": [NICHE_ID, tag_hint]}

        if "retries" in entry:
            task_kwargs["retries"] = entry["retries"]
        if "retry_delay_seconds" in entry:
            task_kwargs["retry_delay_seconds"] = entry["retry_delay_seconds"]

        tasks.append(_make_task(module_path, class_name, **task_kwargs))

    return tasks


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------


@flow(
    name="gaming-pipeline",
    description="CriticalRush gaming content pipeline — stages loaded from niche.yaml",
    retries=0,
    timeout_seconds=1800,  # 30 min hard cap
)
def gaming_pipeline(
    dry_run: bool = False,
    verbose: bool = False,
    trigger: str = "scheduled",
) -> dict[str, Any]:
    """Run the full gaming pipeline as a Prefect flow.

    Args:
        dry_run: If True, stages run but don't persist changes.
        verbose: If True, enable DEBUG logging.
        trigger: How this run was initiated (scheduled/manual/spike).

    Returns:
        Final context dict with run_stats.
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    # Build initial context (mirrors PipelineRunner.run())
    from genlab_core.niche_loader import get_feature_flags, load_niche_config

    run_id = f"{NICHE_ID}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    config = load_niche_config(NICHE_ID, PROJECT_ROOT)
    flags = get_feature_flags(NICHE_ID, PROJECT_ROOT)

    context: dict[str, Any] = {
        "stories": [],
        "blueprints": [],
        "run_stats": {"trigger": trigger},
        "feature_flags": flags,
        "niche_config": config,
        "run_id": run_id,
        "dry_run": dry_run,
    }

    logger.info(
        "[Flow] gaming-pipeline started (run_id=%s, trigger=%s, dry_run=%s)",
        run_id,
        trigger,
        dry_run,
    )

    t0 = time.monotonic()

    # Execute stages sequentially — loaded from niche.yaml
    stage_tasks = _build_task_list()
    for stage_task in stage_tasks:
        context = stage_task(context)

    elapsed = time.monotonic() - t0
    context["run_stats"]["total_duration_seconds"] = round(elapsed, 1)

    logger.info(
        "[Flow] gaming-pipeline completed in %.1fs (stories=%d)",
        elapsed,
        len(context.get("stories", [])),
    )

    return context


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(description="Run gaming pipeline as Prefect flow")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    result = gaming_pipeline(dry_run=args.dry_run, verbose=args.verbose, trigger="manual")
    stats = result.get("run_stats", {})
    print(f"\nDone. Stories: {len(result.get('stories', []))}")
    print(f"Duration: {stats.get('total_duration_seconds', 0)}s")
