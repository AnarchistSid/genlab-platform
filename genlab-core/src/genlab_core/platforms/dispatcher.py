# genlab-core/src/genlab_core/platforms/dispatcher.py
"""Concurrent multi-platform dispatch."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from genlab_core.platforms.models import PublishPayload, PublishResult
from genlab_core.platforms.registry import get_client


def dispatch_many(
    tasks: list[tuple[str, PublishPayload]],
    max_workers: int = 5,
) -> dict[str, PublishResult]:
    """Dispatch to multiple platforms concurrently. Never raises."""
    if not tasks:
        return {}
    results: dict[str, PublishResult] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            platform: pool.submit(_safe_dispatch, platform, payload)
            for platform, payload in tasks
        }
        for platform, future in futures.items():
            results[platform] = future.result()
    return results


def _safe_dispatch(platform: str, payload: PublishPayload) -> PublishResult:
    """Catch exceptions so one platform failure doesn't kill others."""
    try:
        client = get_client(platform)
        return client.publish(payload)
    except Exception as exc:
        return PublishResult(platform=platform, success=False, error=str(exc))
