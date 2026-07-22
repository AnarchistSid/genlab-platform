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
        metrics: dict[str, Any] = {}
        for item in resp.json().get("data", []):
            name = item.get("name", "")
            vals = item.get("values", [{}])
            val = vals[0].get("value", 0) if vals else 0
            metrics[name] = val
        # discovery_share isn't exposed by the Threads API; omit the key
        # so compute_reward redistributes its 0.15 weight to observed
        # metrics (views / replies / reposts) rather than treating it
        # as a real zero contribution.
        return metrics
    except Exception as exc:
        logger.warning("[metric_collector] Threads fetch failed for %s: %s", post_id, exc)
        return {}


__all__ = ["_fetch_threads"]
