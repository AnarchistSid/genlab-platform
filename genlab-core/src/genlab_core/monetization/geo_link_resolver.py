"""Geo-targeted affiliate link resolver.

Selects the correct affiliate link (US vs IN) based on niche audience geography,
and appends UTM tracking parameters + network-specific subIDs.

Skips placeholder URLs (example.com) and known-broken links (cached from the
link health checker).
"""
from __future__ import annotations

import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# Primary audience geography per niche
NICHE_PRIMARY_GEO: dict[str, str] = {
    "ai_creators": "US",
    "gaming": "IN",
    "sports": "IN",
    "movies": "IN",
    "anime": "IN",
}

# In-memory health cache: url → (healthy: bool, checked_at: float)
# TTL = 6 hours. Shared across the pipeline run.
_health_cache: dict[str, tuple[bool, float]] = {}
_health_lock = threading.Lock()
_HEALTH_TTL = 6 * 3600  # 6 hours


def _is_url_healthy(url: str) -> bool:
    """Check if a URL is reachable (cached, non-blocking on first miss).

    Returns True if the URL responds with 2xx/3xx, False on 404/5xx/timeout.
    Results are cached for 6 hours to avoid hammering affiliate networks.
    """
    now = time.monotonic()
    with _health_lock:
        cached = _health_cache.get(url)
        if cached and (now - cached[1]) < _HEALTH_TTL:
            return cached[0]

    # Quick HEAD check with 5s timeout
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "GenLab-LinkCheck/1.0")
        req.add_header("Range", "bytes=0-0")
        with urllib.request.urlopen(req, timeout=5) as resp:
            healthy = resp.status in (200, 206, 301, 302, 303, 307, 308)
    except urllib.error.HTTPError as exc:
        healthy = exc.code in (200, 206, 301, 302, 303, 307, 308)
    except Exception:
        healthy = False

    with _health_lock:
        _health_cache[url] = (healthy, now)

    if not healthy:
        logger.warning("[GeoResolver] Broken link detected: %s", url[:120])
    return healthy


def _is_placeholder(url: str) -> bool:
    """Return True if the URL is a placeholder that should be skipped."""
    return not url or "example.com" in url or "${" in url


def resolve_affiliate_link(
    product: dict,
    niche_id: str,
    platform: str,
    blueprint_id: str | None = None,
) -> str:
    """Wrapper that returns just the URL (back-compat). New callers should
    use resolve_affiliate_link_with_network() to get both URL and network.
    """
    url, _network = resolve_affiliate_link_with_network(product, niche_id, platform, blueprint_id)
    return url


def resolve_affiliate_link_with_network(
    product: dict,
    niche_id: str,
    platform: str,
    blueprint_id: str | None = None,
) -> tuple[str, str]:
    """Return the best affiliate URL with geo-targeting and tracking params.

    Selection order (IN audience):
    1. amazon (IN) → amazon_us (fallback)
    Selection order (US audience):
    1. amazon_us → amazon (IN)

    NOTE: Cuelinks linksredirect.com is a click tracker only — it does
    NOT inject affiliate tags. Server-side wrapping through Cuelinks
    strips our Amazon Associates tag entirely, earning zero commission.
    Direct Amazon Associates links earn commission via our tags
    (aspirehub-21 for IN, aspirehub06-20 for US).

    Cuelinks is kept as last-resort fallback only when no Amazon link
    is available (e.g., for non-Amazon merchants).

    Skips placeholder URLs and validates link health before selection.
    Falls through to next candidate on broken/placeholder links.
    """
    networks = product.get("networks", {})
    geo = NICHE_PRIMARY_GEO.get(niche_id, "US")

    # Build candidate list — Amazon Associates first (real commission),
    # Cuelinks only as last resort (works as click tracker, not monetizer).
    if geo == "IN":
        candidates = ["amazon", "amazon_in", "amazon_us", "cuelinks"]
    else:
        candidates = ["amazon_us", "amazon", "cuelinks"]

    base_url = ""
    network = ""
    for net in candidates:
        info = networks.get(net, {})
        url = info.get("url", "")
        if _is_placeholder(url):
            continue
        if not _is_url_healthy(url):
            logger.debug(
                "[GeoResolver] Skipping broken %s link for %s, trying next",
                net, product.get("name", "?"),
            )
            continue
        base_url = url
        network = net
        break

    if not base_url:
        logger.debug(
            "[GeoResolver] No healthy link found for product %s",
            product.get("name", "?"),
        )
        return ("", "")

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
    return (f"{base_url}{sep}{param_str}", network)
