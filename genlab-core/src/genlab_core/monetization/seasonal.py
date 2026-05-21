"""Seasonal product rotation for affiliate matching.

Loads seasonal events from config and provides active event products
that override the static catalog during shopping events.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SEASONAL_PATH = Path(__file__).parent.parent.parent.parent / "config" / "affiliate_seasonal.yaml"


def load_seasonal_config(path: Path | None = None) -> dict[str, Any]:
    """Load seasonal config. Returns empty dict if file missing."""
    p = path or _SEASONAL_PATH
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def get_active_events(config: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """Return events whose date window includes today."""
    today = today or date.today()
    active = []
    for event in config.get("events", []):
        start = event.get("start")
        end = event.get("end")
        if not start or not end:
            continue
        start_date = datetime.strptime(str(start), "%Y-%m-%d").date()
        end_date = datetime.strptime(str(end), "%Y-%m-%d").date()
        if start_date <= today <= end_date:
            active.append(event)
    return active


def get_seasonal_products(
    config: dict[str, Any], today: date | None = None
) -> list[dict[str, Any]]:
    """Return all products from currently active seasonal events."""
    products = []
    for event in get_active_events(config, today):
        event_products = event.get("products", [])
        for p in event_products:
            p["_seasonal_event"] = event.get("name", "")
        products.extend(event_products)
    return products
