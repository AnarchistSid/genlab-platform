"""Factory for wrapping pipeline stage classes as Prefect @task callables.

Given a stage class with an `execute(context) -> context` method, produces
a Prefect task that:
  1. Logs stage entry/exit
  2. Catches exceptions and records them in context
  3. Returns the updated context dict

Usage:
    from genlab_core.orchestration.task_factory import make_stage_task

    fetch_task = make_stage_task(FetchGamingStories, retries=1)
    # Inside a @flow:
    context = fetch_task(context)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Type

logger = logging.getLogger(__name__)


def make_stage_task(
    stage_cls: Type[Any],
    *,
    retries: int = 0,
    retry_delay_seconds: int = 30,
    tags: list[str] | None = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """Create a Prefect @task from a pipeline stage class.

    Args:
        stage_cls: Class with an ``execute(context: dict) -> dict`` method.
        retries: Number of Prefect retries on failure.
        retry_delay_seconds: Delay between retries.
        tags: Optional Prefect tags for filtering.

    Returns:
        A Prefect task callable: ``(context: dict) -> dict``.
    """
    from prefect import task

    stage_name = stage_cls.__name__

    @task(
        name=stage_name,
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
        tags=tags or [],
    )
    def run_stage(context: Dict[str, Any]) -> Dict[str, Any]:
        stage = stage_cls()
        t0 = time.monotonic()
        logger.info("[Prefect] Stage %s starting", stage_name)
        try:
            result = stage.execute(context)
            elapsed = time.monotonic() - t0
            logger.info(
                "[Prefect] Stage %s completed in %.1fs", stage_name, elapsed
            )
            return result
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error(
                "[Prefect] Stage %s failed after %.1fs: %s",
                stage_name, elapsed, e,
            )
            context.setdefault("run_stats", {}).setdefault("errors", []).append(
                {"stage": stage_name, "error": str(e)}
            )
            raise

    return run_stage
