"""Threads metric fetcher — extracted from learning/metric_collector.py.

Uses the Threads API ``GET /v1.0/{post_id}/insights`` endpoint with a per-niche
access token (from ``resolve_threads_credentials(niche_id)``).

Reward-shape specialisation (see metric_collector.py docstring): aligned with
``RewardShaper.BASE_WEIGHTS["threads"]`` — views, replies, reposts,
discovery_share. ``discovery_share`` isn't exposed by the Threads insights
endpoint and is deliberately OMITTED so ``compute_reward`` redistributes its
0.15 weight to observed metrics rather than treating it as a real zero.

Migration note (P5a phase 6 — FINAL, 2026-06-19): sixth and last per-platform
module in the metric_collector split. After this PR + the matching cleanup,
``metric_collector.py`` shrinks to ~500 LOC of pure orchestration (top-level
``fetch_platform_metrics``, ``get_channel_metrics``, ``compute_reward``,
``process_pending_task``, ``collect_metrics``, ``_default_bandit_updater``).
"""



from __future__ import annotations
from genlab_core.platforms.meta_http import get_shared_session as _get_shared_session

# 2026-07-22 anti-fingerprint extension: metric collectors call
# graph.facebook.com to fetch reel insights. Reusing the shared
# Meta HTTP session gives us (1) identified User-Agent header + (2)
# X-App-Usage capture via the response hook. Same rationale as
# platforms/facebook.py adopted in 8c02b266.
_META_SESSION = _get_shared_session()
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _fetch_threads(post_id: str, niche_id: str = "") -> dict:
    """Threads API — media insights.

    Returns keys aligned with ``RewardShaper.BASE_WEIGHTS["threads"]``:
    ``views, replies, reposts, discovery_share``. ``discovery_share``
    isn't exposed by the Threads insights endpoint — stubbed as 0.
    """
    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    token, _user_id = resolve_threads_credentials(niche_id)
    if not token:
        # F-0069 instrumentation: previously silent no-token path.
        logger.warning(
            "[metric_collector] F-0069: Threads no-token for niche=%s "
            "post_id=%s — task status will not advance",
            niche_id, post_id,
        )
        return {}
    try:
        resp = _META_SESSION.get(
            f"https://graph.threads.net/v1.0/{post_id}/insights",
            params={
                "metric": "views,likes,replies,reposts,quotes",
                "access_token": token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        data_items = payload.get("data", [])
        metrics: dict[str, Any] = {}
        for item in data_items:
            name = item.get("name", "")
            vals = item.get("values", [{}])
            val = vals[0].get("value", 0) if vals else 0
            metrics[name] = val
        # F-0069 instrumentation (2026-07-27): Threads has 35% stall
        # rate on prod (14/40 rows stuck at status=SUCCESS in 30d)
        # while YT+IG advance 100%. Existing WARN below fires only on
        # exception; a 200-OK-empty-response was the invisible-stall
        # case. Meta insights returns `{"data": []}` for fresh /
        # deleted / permission-denied posts. Diagnose next session
        # from these WARNs.
        if not metrics:
            logger.warning(
                "[metric_collector] F-0069: Threads insights empty "
                "for post_id=%s niche=%s (HTTP %s, data_items=%d, "
                "payload_error=%r) — status will not advance",
                post_id, niche_id, resp.status_code, len(data_items),
                payload.get("error"),
            )
        # discovery_share isn't exposed by the Threads API; omit the key
        # so compute_reward redistributes its 0.15 weight to observed
        # metrics (views / replies / reposts) rather than treating it
        # as a real zero contribution.
        return metrics
    except Exception as exc:
        logger.warning("[metric_collector] Threads fetch failed for %s: %s", post_id, exc)
        return {}


__all__ = ["_fetch_threads"]
