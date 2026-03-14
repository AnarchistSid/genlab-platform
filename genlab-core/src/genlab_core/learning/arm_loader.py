"""
Lightweight bandit arm loader for genlab-core.
Reads/writes BanditArm-like dicts from SharePoint lists.
Each niche has its own list: {NicheDisplayName}_BanditArms

Accepts a proxy object with `.all()`, `.create()`, `.update()` methods
matching the GraphTableProxy interface.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Map niche_id to SharePoint list display name
BANDIT_LIST_NAMES = {
    "gaming": "CriticalRush_BanditArms",
    "ai_creators": "BlackboxBrief_BanditArms",
    "sports": "ClutchWire_BanditArms",
    "movies": "SpliceReel_BanditArms",
    "anime": "FrameDrift_BanditArms",
}


def load_all_arms(proxy, niche_id: str) -> dict[str, tuple[float, float]]:
    """Load all bandit arms for a niche. Returns {arm_id: (alpha, beta)}.

    Args:
        proxy: Object with `.all()` returning list of {id, fields} dicts
               (e.g. GraphTableProxy or mock). Should already be pointed at
               the correct list for this niche.
        niche_id: Used only for logging; caller is responsible for
                  constructing the proxy against the right list.
    """
    try:
        items = proxy.all()
        arms: dict[str, tuple[float, float]] = {}
        for item in items:
            fields = item.get("fields", item)
            arm_id = fields.get("Title", "")
            alpha = float(fields.get("Alpha", 1.0))
            beta = float(fields.get("Beta", 1.0))
            if arm_id:
                arms[arm_id] = (alpha, beta)
        return arms
    except Exception as e:
        logger.warning("[arm_loader] failed to load arms for %s: %s", niche_id, e)
        return {}


def save_arm(
    proxy,
    arm_id: str,
    alpha: float,
    beta: float,
    content_type: str = "",
    platform: str = "",
) -> None:
    """Save a single bandit arm via proxy.create().

    Always creates a new item — the warm-start only runs once per niche
    and only touches arms that don't have existing observations.
    """
    fields = {
        "Title": arm_id,
        "Alpha": alpha,
        "Beta": beta,
        "ContentType": content_type,
        "Platform": platform,
        "TotalPulls": 0,
        "LastUpdated": datetime.now(timezone.utc).isoformat(),
    }
    try:
        proxy.create(fields)
    except Exception as e:
        logger.warning("[arm_loader] save failed for %s: %s", arm_id, e)
