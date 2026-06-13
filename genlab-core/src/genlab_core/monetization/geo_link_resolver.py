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
        amazon (IN) → amazon_in → amazon_us → earnkaro (when key set)
    Selection order (US audience):
        amazon_us → amazon (IN) → shareasale → cj → impact

    NOTE (2026-06-14 cuelinks audit, queue item #12): cuelinks was
    previously last-fallback in both lists. The 2026-06-14 prod-trace
    audit confirmed that **every cuelinks redirect in our 73-click
    historical sample earned zero commission**. Mechanism: cuelinks
    (linksredirect.com) is a click tracker that faithfully passes
    through whatever URL is in the inner ``url=`` parameter; our
    catalog's cuelinks entries had the bare ``amazon.in/dp/B0XYZ``
    without the ``tag=aspirehub-21`` affiliate tag, so the 302 sent
    users to Amazon with no attribution. Cuelinks doesn't INJECT
    affiliate tags — it only relays whatever URL it receives.

    Two options were considered: (a) fix the catalog's cuelinks
    entries to embed our Amazon tag, (b) remove cuelinks from the
    candidate list entirely. Option (b) won — cuelinks adds zero
    revenue value over a direct Amazon link with the same tag (same
    Amazon Associates commission either way), so the cuelinks hop is
    pure latency + a third-party dependency for no gain. The catalog
    entries can remain; this resolver just won't pick them. To
    re-enable, add ``"cuelinks"`` back to the candidate list AND
    verify the catalog's inner URLs carry the Amazon tag.

    Skips placeholder URLs and validates link health before selection.
    Falls through to next candidate on broken/placeholder links.
    """
    networks = product.get("networks", {})
    geo = NICHE_PRIMARY_GEO.get(niche_id, "US")

    # Build candidate list — Amazon Associates first (real commission),
    # then per-geo affiliate networks (EarnKaro for IN; ShareASale/CJ/
    # Impact for US — added in #34a, 2026-06-13). Cuelinks removed
    # 2026-06-14 after the audit (see docstring above).
    #
    # The candidate is silently skipped for any niche whose
    # product["networks"] dict doesn't carry the network's url field
    # (the loop below filters via `info.get("url")`), so adding networks
    # here is additive — niches that haven't onboarded a network see
    # zero behavior change.
    if geo == "IN":
        candidates = [
            "amazon",
            "amazon_in",
            "amazon_us",
            "earnkaro",  # Indian deal aggregator
        ]
    else:
        candidates = [
            "amazon_us",
            "amazon",
            "shareasale",  # US network — direct tracking links
            "cj",  # CJ Affiliate — global, template-based
            "impact",  # Impact.com — per-advertiser deep links
        ]

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
                net,
                product.get("name", "?"),
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

    # Add network-specific subID for attribution. Each network has its
    # own param convention for publisher-supplied tracking IDs — keeping
    # the mapping centralized here means a new network just adds one
    # branch instead of touching every caller.
    sub_id = f"{niche_id}_{blueprint_id[:8]}" if blueprint_id else niche_id
    # NOTE: the cuelinks branch (utm_params["uid"]) was removed alongside
    # the candidate-list removal on 2026-06-14. Kept dead-code-free so a
    # future readder of cuelinks sees the candidate-list change first
    # rather than restoring orphan sub_id wiring.
    if network in ("admitad",):
        utm_params["subid"] = sub_id
    elif network == "shareasale":
        utm_params["afftrack"] = sub_id
    elif network == "cj":
        utm_params["sid"] = sub_id
    elif network == "impact":
        # Impact.com uses subId1 / subId2 / subId3 for tracking layers;
        # the niche_id+blueprint_id slug goes into subId1.
        utm_params["subId1"] = sub_id
    elif network == "earnkaro":
        # EarnKaro shortened URLs encode attribution server-side; the
        # sub_id is appended as `ref` for first-party click breakdown.
        utm_params["ref"] = sub_id

    # Append params to URL
    sep = "&" if "?" in base_url else "?"
    param_str = urllib.parse.urlencode(utm_params)
    return (f"{base_url}{sep}{param_str}", network)
