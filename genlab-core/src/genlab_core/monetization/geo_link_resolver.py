"""Geo-targeted affiliate link resolver.

Selects the correct affiliate link (US vs IN) based on niche audience geography,
and appends UTM tracking parameters + network-specific subIDs.

Skips placeholder URLs (example.com) and known-broken links (cached from the
link health checker).
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# Primary audience geography per niche — drives Amazon US vs IN
# affiliate URL selection.
#
# 2026-06-17 update: flipped all four India-defaulted niches to "US"
# based on actual `affiliate_clicks.country` distribution observed
# over the trailing 90 days:
#
#   niche        US clicks  IN clicks  → conclusion
#   ai_creators  10         5          (already US, kept)
#   anime        13         1          ⇒ 93% US
#   gaming       12         1          ⇒ 92% US
#   movies       27         0          ⇒ 100% US
#   sports       12         0          ⇒ 100% US
#
# These ratios are CONSERVATIVE — they undercount US viewers, who
# have been seeing amazon.in URLs that they recognize as
# "wrong country" and may have skipped without clicking. With
# amazon.com URLs the US share will likely climb further. We were
# routing 140 of 156 affiliate publishes / month to amazon.in for
# US-dominant audiences ⇒ ~140 unconvertable clicks / month.
#
# If a niche's audience ever shifts back toward India we can revert
# per-niche here; the click-geography query that motivated this
# flip is captured in the PR body for auditability.
NICHE_PRIMARY_GEO: dict[str, str] = {
    "ai_creators": "US",
    "gaming": "US",
    "sports": "US",
    "movies": "US",
    "anime": "US",
}

# 2026-06-17 (follow-up to PR #277): dynamic geo override from
# observed clicks. The hardcoded NICHE_PRIMARY_GEO above is the
# **fallback** — the real audience may drift away from those
# defaults over months. The dynamic resolver queries
# affiliate_clicks for the last-30-day country distribution per
# niche and returns the dominant country (≥60% threshold so a 50/50
# split doesn't flap).
#
# Cached for 1 hour per process to avoid hammering Postgres on
# every link resolution. The cache survives a pipeline run (resolver
# is called many times per blueprint) but expires across runs so
# audience shifts surface within hours of accumulating in the data.
#
# Falls back to the hardcoded dict on DB error / missing env /
# insufficient data — fail-OPEN so a transient DB outage never
# breaks link generation.
_DYNAMIC_GEO_TTL_SECONDS: int = 3600
_DYNAMIC_GEO_DOMINANCE_THRESHOLD: float = 0.6
_DYNAMIC_GEO_MIN_CLICKS: int = 10
_dynamic_geo_cache: dict[str, tuple[str, float]] = {}
_dynamic_geo_lock = threading.Lock()


def _compute_dynamic_geo(niche_id: str) -> str | None:
    """Query affiliate_clicks for the last-30d dominant country.

    Returns:
        "US" / "IN" / similar when one country has ≥60% of last-30d
        clicks AND there are ≥10 clicks total (so a single click in
        an outlier country doesn't flip the geo).
        None when there's insufficient data or the DB is unreachable
        (caller falls back to NICHE_PRIMARY_GEO[niche_id]).
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        return None
    try:
        import psycopg  # noqa: F401 — kept for the except ImportError clause

        from genlab_core.storage.tenant_context import pg_connect  # SR-A/C/D Tier-3
    except ImportError:
        return None
    try:
        with pg_connect(db_url, connect_timeout=5, niche_id=niche_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT country, COUNT(*) AS clicks
                    FROM affiliate_clicks
                    WHERE niche_id = %s
                      AND created_at >= NOW() - INTERVAL '30 days'
                      AND country IS NOT NULL AND country != ''
                    GROUP BY country
                    ORDER BY clicks DESC
                    """,
                    (niche_id,),
                )
                rows = cur.fetchall()
    except Exception as exc:
        # WARNING (not DEBUG): fallback to hardcoded geo silently ships
        # wrong-country affiliate URLs → wrong Amazon domain → wrong
        # commission rate. Silent monetization revenue leak. If this
        # fires often for one niche, either RLS is misconfigured or
        # affiliate_clicks isn't being populated for that tenant.
        logger.warning(
            "[GeoResolver] dynamic geo query failed for niche=%s: %s — falling back to hardcoded",
            niche_id,
            exc,
        )
        return None

    if not rows:
        return None
    total = sum(int(r[1]) for r in rows)
    if total < _DYNAMIC_GEO_MIN_CLICKS:
        return None
    top_country, top_clicks = rows[0]
    share = int(top_clicks) / total
    if share < _DYNAMIC_GEO_DOMINANCE_THRESHOLD:
        # Audience is split — don't flap; stick with the hardcoded
        # default until a clear winner emerges.
        return None
    return str(top_country).upper()


def _resolve_niche_geo(niche_id: str) -> str:
    """Niche → audience geo, dynamic if possible, hardcoded fallback.

    The cache means each niche's geo is computed at most once per hour
    per process — the rest is O(1) dict lookup.
    """
    now = time.monotonic()
    with _dynamic_geo_lock:
        cached = _dynamic_geo_cache.get(niche_id)
        if cached and (now - cached[1]) < _DYNAMIC_GEO_TTL_SECONDS:
            return cached[0]

    dynamic = _compute_dynamic_geo(niche_id)
    geo = dynamic if dynamic else NICHE_PRIMARY_GEO.get(niche_id, "US")
    with _dynamic_geo_lock:
        _dynamic_geo_cache[niche_id] = (geo, now)
    if dynamic and dynamic != NICHE_PRIMARY_GEO.get(niche_id):
        logger.info(
            "[GeoResolver] dynamic geo for niche=%s: %s (hardcoded fallback was %s)",
            niche_id,
            dynamic,
            NICHE_PRIMARY_GEO.get(niche_id, "<none>"),
        )
    return geo


# In-memory health cache: url → (healthy: bool, checked_at: float)
# TTL = 6 hours. Shared across the pipeline run.
_health_cache: dict[str, tuple[bool, float]] = {}
_health_lock = threading.Lock()
_HEALTH_TTL = 6 * 3600  # 6 hours


# HTTP codes that mean "the URL exists and is reachable", from the
# perspective of a liveness check that doesn't care about the response
# body. 2xx + 3xx are obvious; 405 deserves a special note:
#
#   amazon.com returns 405 (Method Not Allowed) to our ranged-GET
#   liveness check on /dp/* URLs, while amazon.in returns 206
#   (Partial Content) to the same request. Before adding 405 to
#   this allowlist, every US-targeted catalog product failed the
#   health check, NICHE_PRIMARY_GEO="US" fell back to amazon.in,
#   and PR #277's geo→US routing was structurally a no-op for
#   catalog matches. The 405 from amazon.com still proves the URL
#   is reachable — the server is refusing the SPECIFIC request shape
#   (ranged GET on a product page), not denying the URL exists.
#
# 416 (Range Not Satisfiable) is included for the same reason —
# a server can reject our Range header without invalidating the URL.
_HEALTHY_CODES = frozenset({200, 206, 301, 302, 303, 307, 308, 405, 416})


def _is_url_healthy(url: str) -> bool:
    """Check if a URL is reachable (cached, non-blocking on first miss).

    Returns True if the URL responds with any code in ``_HEALTHY_CODES``
    (essentially "the URL exists and the server responded"), False on
    404/5xx/timeout. Results are cached for 6 hours to avoid hammering
    affiliate networks.
    """
    now = time.monotonic()
    with _health_lock:
        cached = _health_cache.get(url)
        if cached and (now - cached[1]) < _HEALTH_TTL:
            return cached[0]

    # Quick liveness check with 5s timeout. We use ranged GET (1 byte)
    # instead of HEAD because many CDNs return inaccurate codes to HEAD
    # but respect the Range header on GET.
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "GenLab-LinkCheck/1.0")
        req.add_header("Range", "bytes=0-0")
        with urllib.request.urlopen(req, timeout=5) as resp:
            healthy = resp.status in _HEALTHY_CODES
    except urllib.error.HTTPError as exc:
        healthy = exc.code in _HEALTHY_CODES
    except Exception as exc:
        # Distinguish network/timeout failure from HTTP-status failure —
        # both mark the URL unhealthy but for different reasons. The
        # generic warning at :217 fires either way; this info-level log
        # captures the exception surface for diagnosis.
        logger.info("[GeoResolver] link health check exception for %s: %s", url[:120], exc)
        healthy = False

    with _health_lock:
        _health_cache[url] = (healthy, now)

    if not healthy:
        logger.warning("[GeoResolver] Broken link detected: %s", url[:120])
    return healthy


def _is_placeholder(url: str) -> bool:
    """Return True if the URL is a placeholder that should be skipped."""
    return not url or "example.com" in url or "${" in url


def _try_earnkaro_transform(base_url: str, *, product_name: str = "") -> tuple[str, str]:
    """Try transforming an Amazon URL → EarnKaro short link.

    Returns (ekaro_url, "earnkaro") on success, ("", "") on any failure.
    The earnkaro_client.convert_url helper already encapsulates
    credential check + HTTP + cache + graceful fallback — this wrapper
    just adds the matcher-side logging + the (url, network) return shape
    geo_link_resolver expects.

    Separated as a module-level function so tests can patch it cleanly
    without touching the earnkaro_client module directly.
    """
    import os

    # Cheap pre-check: skip the call entirely when the key isn't set.
    # earnkaro_client.convert_url would also return "" but we avoid the
    # import + cache-key hash overhead in the (current) common case
    # where no operator has configured EarnKaro yet.
    if not os.environ.get("EARNKARO_CONVERT_KEY", "").strip():
        return ("", "")

    try:
        from genlab_core.monetization.earnkaro_client import convert_url

        ekaro_url = convert_url(base_url)
    except Exception as exc:
        # Be paranoid here: never let a transformer error block the
        # original Amazon URL from publishing. Log + skip.
        logger.warning(
            "[GeoResolver] EarnKaro transform raised for %s: %s",
            product_name,
            exc,
        )
        return ("", "")

    if ekaro_url:
        logger.info(
            "[GeoResolver] EarnKaro-transformed Amazon URL for %s -> %s",
            product_name,
            ekaro_url[:80],
        )
        return (ekaro_url, "earnkaro")
    return ("", "")


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
    # Use the dynamic resolver: queries affiliate_clicks for last-30d
    # country distribution, caches for 1 hour, falls back to the
    # hardcoded NICHE_PRIMARY_GEO dict on any error / insufficient
    # data. See ``_resolve_niche_geo`` docstring for the dominance
    # thresholds + cache semantics.
    geo = _resolve_niche_geo(niche_id)

    # Build candidate list — Amazon Associates first (real commission),
    # then per-geo affiliate networks (EarnKaro for IN; ShareASale/CJ/
    # Impact for US — added in #34a, 2026-06-13).
    #
    # ## Cuelinks (2026-07-16 re-integration, PR 3)
    #
    # Cuelinks was removed 2026-06-14 (see docstring above) because
    # the pre-V3 stub routed Amazon URLs → ₹0 commission. It's now
    # re-added as the LAST fallback in both geo lists — the ordering
    # matters: Amazon adapters always come first, so any product with
    # both an Amazon URL and a Cuelinks URL picks Amazon. Cuelinks
    # only fires when Amazon URLs are absent or broken. The client-
    # side ``cuelinks_client.AmazonUrlNotAllowed`` guard is the
    # belt-and-suspenders defense.
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
            "cuelinks",  # Non-Amazon merchants (Flipkart/Myntra/etc.); LAST by design
        ]
    else:
        candidates = [
            "amazon_us",
            "amazon",
            "shareasale",  # US network — direct tracking links
            "cj",  # CJ Affiliate — global, template-based
            "impact",  # Impact.com — per-advertiser deep links
            "cuelinks",  # US Cuelinks coverage is thinner but non-zero; LAST by design
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

    # ── EarnKaro auto-transformation (2026-06-13) ─────────────────────────
    # When the base URL is an Amazon link AND we're targeting an IN-geo
    # niche AND EARNKARO_CONVERT_KEY is set, route the click through
    # EarnKaro to capture their (higher) commission tier instead of /
    # in addition to the direct Amazon Associates tag.
    #
    # EarnKaro acts as a pure URL transformer for Amazon: POST the
    # Amazon URL to ekaro.in/api/converter/public, get back an ekaro.in
    # short link, use that as the published URL. Transform failure
    # (key missing, API error, unrecognized response) is silent — we
    # fall back to the original Amazon URL with the amazon network tag.
    #
    # This closes the half-wired-stub gap from #34a/b: EarnKaro's
    # adapter shipped working code, but the matcher never had a code
    # path that CALLED it. Now it does, for every Indian-audience
    # affiliate URL the matcher picks.
    if geo == "IN" and network in ("amazon", "amazon_in") and base_url:
        transformed_url, transformed_network = _try_earnkaro_transform(
            base_url, product_name=product.get("name", "?")
        )
        if transformed_url:
            base_url = transformed_url
            network = transformed_network

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
