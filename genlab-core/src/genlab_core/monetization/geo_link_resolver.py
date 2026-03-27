"""Geo-targeted affiliate link resolver.

Selects the correct affiliate link (US vs IN) based on niche audience geography,
and appends UTM tracking parameters + network-specific subIDs.
"""
from __future__ import annotations

import logging
import urllib.parse

logger = logging.getLogger(__name__)

# Primary audience geography per niche
NICHE_PRIMARY_GEO: dict[str, str] = {
    "ai_creators": "US",
    "gaming": "IN",
    "sports": "IN",
    "movies": "IN",
    "anime": "IN",
}


def resolve_affiliate_link(
    product: dict,
    niche_id: str,
    platform: str,
    blueprint_id: str | None = None,
) -> str:
    """Return the best affiliate URL with geo-targeting and tracking params.

    Selection order:
    1. If niche audience is IN and amazon_in link exists → use it
    2. If niche audience is US and amazon_us link exists → use it
    3. Fall back to cuelinks/earnkaro if available
    4. Return empty string if no link found
    """
    networks = product.get("networks", {})
    geo = NICHE_PRIMARY_GEO.get(niche_id, "US")

    # Select the best network based on geo
    if geo == "IN":
        candidates = ["amazon", "amazon_in", "earnkaro", "cuelinks"]
    else:
        candidates = ["amazon_us", "amazon", "cuelinks"]

    base_url = ""
    network = ""
    for net in candidates:
        info = networks.get(net, {})
        url = info.get("url", "")
        if url:
            base_url = url
            network = net
            break

    if not base_url:
        return ""

    # Add UTM tracking parameters
    utm_params = {
        "utm_source": "genlab",
        "utm_medium": platform,
        "utm_campaign": niche_id,
    }

    # Add network-specific subID for attribution
    sub_id = f"{niche_id}_{blueprint_id[:8]}" if blueprint_id else niche_id
    if network in ("cuelinks",):
        utm_params["uid"] = sub_id
    elif network in ("admitad",):
        utm_params["subid"] = sub_id

    # Append params to URL
    sep = "&" if "?" in base_url else "?"
    param_str = urllib.parse.urlencode(utm_params)
    return f"{base_url}{sep}{param_str}"
