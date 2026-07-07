"""Seasonal product rotation for affiliate matching.

Loads seasonal events from config and provides active event products
that override the static catalog during shopping events.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

_SEASONAL_PATH = Path(__file__).parent.parent.parent.parent / "config" / "affiliate_seasonal.yaml"


def _expand_env_vars_in_seasonal(config: dict[str, Any]) -> dict[str, Any]:
    """Walk ``events[*].products[*].networks[*].url`` and expand ``${VAR}``.

    Mirrors ``catalog_loader.expand_env_vars`` but for the seasonal file
    shape (which has an extra ``events[*]`` wrapper vs the flat catalog).
    Without this, seasonal URLs template like
    ``https://amazon.com/dp/X?tag=${AMAZON_US_AFFILIATE_TAG}`` ship as
    literals — Amazon silently rejects the unknown tag and clicks earn
    zero commission (see PR #272 for the same class of bug in the
    dashboard-side loader).

    Mutates *config* in place AND returns it.

    Missing env vars leave ``${VAR}`` intact — operators can spot the
    literal in ``affiliate_clicks.affiliate_url`` and trace back.
    """
    events = config.get("events") or []
    if not isinstance(events, list):
        return config
    for event in events:
        products = event.get("products") or []
        if not isinstance(products, list):
            continue
        for product in products:
            networks = product.get("networks") or {}
            if not isinstance(networks, dict):
                continue
            for _, info in networks.items():
                if not isinstance(info, dict):
                    continue
                url = info.get("url")
                if isinstance(url, str) and "${" in url:
                    info["url"] = os.path.expandvars(url)
    return config


def load_seasonal_config(path: Path | None = None) -> dict[str, Any]:
    """Load seasonal config. Returns empty dict if file missing.

    Env vars in URL fields (e.g. ``${AMAZON_US_AFFILIATE_TAG}``) are
    expanded at load time via :func:`_expand_env_vars_in_seasonal`.
    """
    p = path or _SEASONAL_PATH
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}
    return _expand_env_vars_in_seasonal(config)


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
