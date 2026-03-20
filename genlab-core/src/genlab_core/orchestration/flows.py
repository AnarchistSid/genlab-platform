"""Prefect flow definitions for Gen Lab pipeline orchestration.

These flows wrap the unified pipeline CLI and can be deployed as
Prefect deployments, replacing individual launchd plists.

Usage:
    # Register flows with Prefect server
    prefect deploy --all

    # Run directly
    python -m genlab_core.orchestration.flows --niche gaming

    # Schedule via Prefect (replaces launchd)
    prefect deployment run 'daily-pipeline/gaming'
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prefect import flow, task
    from prefect.tasks import task_input_hash

    PREFECT_AVAILABLE = True
except ImportError:
    PREFECT_AVAILABLE = False

    # Stub decorators when prefect is not installed
    def flow(*args, **kwargs):  # type: ignore[no-redef]
        def decorator(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return decorator

    def task(*args, **kwargs):  # type: ignore[no-redef]
        def decorator(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return decorator

    class task_input_hash:  # type: ignore[no-redef]
        pass


ALL_NICHES = ["ai_creators", "gaming", "sports", "movies", "anime"]

# Pipeline schedule (IST → UTC)
NICHE_SCHEDULE = {
    "ai_creators": "0 2 30 * * *",   # 08:00 IST
    "gaming":      "0 4 0 * * *",    # 09:30 IST
    "anime":       "0 6 0 * * *",    # 11:30 IST
    "movies":      "0 8 0 * * *",    # 13:30 IST
    "sports":      "0 10 0 * * *",   # 15:30 IST
}


@task(retries=2, retry_delay_seconds=60)
def run_pipeline_stage(niche_id: str, stage: str) -> dict:
    """Run a single pipeline stage for a niche."""
    from genlab_core.pipeline.cli import run_pipeline

    logger.info("Running stage %s for niche %s", stage, niche_id)
    result = run_pipeline(niche_ids=[niche_id], stages_filter=[stage])
    return {"niche_id": niche_id, "stage": stage, "exit_code": result}


@flow(name="daily-pipeline", log_prints=True)
def daily_pipeline(niche_id: str = "gaming") -> int:
    """Run the full daily pipeline for a single niche.

    This is the Prefect equivalent of the per-niche launchd plist.
    """
    from genlab_core.pipeline.cli import run_pipeline

    logger.info("Starting daily pipeline for %s", niche_id)
    exit_code = run_pipeline(niche_ids=[niche_id])
    logger.info("Pipeline complete for %s — exit code %d", niche_id, exit_code)
    return exit_code


@flow(name="daily-pipeline-all", log_prints=True)
def daily_pipeline_all() -> dict:
    """Run daily pipeline for all 5 niches sequentially.

    Replaces 5 individual launchd plists with a single Prefect flow.
    """
    results = {}
    for niche_id in ALL_NICHES:
        logger.info("Starting pipeline for %s", niche_id)
        exit_code = daily_pipeline(niche_id)
        results[niche_id] = exit_code
        if exit_code != 0:
            logger.warning("Pipeline failed for %s (exit code %d)", niche_id, exit_code)

    successes = sum(1 for v in results.values() if v == 0)
    logger.info("All pipelines complete: %d/%d succeeded", successes, len(results))
    return results


@flow(name="publish-all", log_prints=True)
def publish_all(niche_id: str = "all") -> int:
    """Run publisher for one or all niches.

    Replaces per-niche publisher launchd plists.
    """
    from genlab_core.publishing.publish_all_platforms import run_publish

    niches = ALL_NICHES if niche_id == "all" else [niche_id]
    total_exit = 0
    for nid in niches:
        logger.info("Publishing for %s", nid)
        try:
            exit_code = run_publish(niche_id=nid)
            total_exit = max(total_exit, exit_code)
        except Exception as e:
            logger.error("Publish failed for %s: %s", nid, e)
            total_exit = 1
    return total_exit


@flow(name="collect-insights", log_prints=True)
def collect_insights(window: int = 6) -> dict:
    """Collect engagement insights for all niches at a given window.

    Replaces 20 fetch-insights launchd plists (already consolidated to 4).
    """
    results = {}
    for niche_id in ALL_NICHES:
        logger.info("Collecting %dh insights for %s", window, niche_id)
        try:
            future = run_pipeline_stage(niche_id, "fetch_insights")
            results[niche_id] = future
        except Exception as e:
            logger.warning("Insight collection failed for %s: %s", niche_id, e)
            results[niche_id] = {"error": str(e)}
    return results


@flow(name="token-health", log_prints=True)
def token_health_check() -> dict:
    """Run token health checks across all platforms."""
    from genlab_core.monitoring.token_health import run_all_checks

    return run_all_checks()


def main():
    """CLI entry point for running flows directly."""
    import argparse

    parser = argparse.ArgumentParser(description="GenLab Prefect flows")
    parser.add_argument("--niche", default="all", help="Niche ID or 'all'")
    parser.add_argument("--flow", default="pipeline",
                       choices=["pipeline", "publish", "insights", "health"],
                       help="Which flow to run")
    parser.add_argument("--window", type=int, default=6, help="Insights window (hours)")
    args = parser.parse_args()

    if args.flow == "pipeline":
        if args.niche == "all":
            daily_pipeline_all()
        else:
            daily_pipeline(args.niche)
    elif args.flow == "publish":
        publish_all(args.niche)
    elif args.flow == "insights":
        collect_insights(args.window)
    elif args.flow == "health":
        token_health_check()


if __name__ == "__main__":
    main()
