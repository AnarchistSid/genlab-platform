"""Cuelinks V3 API client — 2026-07-16 (PR 1 of 3).

Cuelinks V3 is a REST API that provides three capabilities Gen Lab uses:

  * ``/ping`` — auth verification
  * ``/campaigns`` — merchant discovery, filterable by category / country /
    access_status, sortable by EPC (earnings per 100 clicks). Used weekly
    by the campaign-refresh timer to build a curated shortlist of
    high-EPC non-Amazon merchants.
  * ``/links/convert`` — transforms a merchant URL into a tracked
    affiliate short link. Sub-IDs (per-channel attribution) are embedded
    in the request.

Design choices
==============

* **Amazon guard is load-bearing.** The 2026-06-14 audit (queue item
  #12, commit `1444565d`) removed Cuelinks from the affiliate candidate
  list after prod trace showed **every cuelinks redirect earned ₹0**.
  Root cause: Cuelinks is a click tracker — it faithfully relays
  whatever URL is in the inner ``url=`` parameter. For Amazon URLs,
  the direct Amazon Associates tag flow captures the commission
  regardless of whether Cuelinks was in the middle; adding Cuelinks
  is pure latency + a third-party dependency for zero revenue gain.

  This module ENFORCES that invariant: ``convert_url`` raises
  ``AmazonUrlNotAllowed`` for any URL matching ``amazon.in`` or
  ``amazon.com``. Callers routing Amazon must use ``amazon_us`` /
  ``amazon_in`` adapters directly (see
  ``geo_link_resolver.py:308-327``). The V3 integration re-enables
  Cuelinks ONLY for merchants where Cuelinks is the actual broker
  (Flipkart, Myntra, Ajio, Meesho, Nykaa, etc.) — networks Gen Lab
  doesn't have direct affiliate agreements with.

* **Disk-cached with different TTLs per endpoint.** ``/campaigns``
  changes over time (new merchants, EPC drift) so 6h TTL. ``/links/
  convert`` returns URLs that are stable once generated for a
  ``(target_url, subid)`` pair so 30d TTL — matches EarnKaro pattern.

* **Fail-graceful.** Any HTTP error / timeout returns ``[]`` (campaigns)
  or ``""`` (convert). Callers fall back to other networks; nothing
  crashes the pipeline. Errors log at WARNING with
  ``cuelinks_*_failed`` prefix so operators can grep by category.

* **Stdlib HTTP only.** ``urllib.request`` to avoid adding
  requests/httpx to the runtime dep set (matches earnkaro_client).

* **Env-key fresh on every call.** No cached auth material — operator
  can rotate ``CUELINKS_V3_API_KEY`` without restarting the pipeline.

What this module does NOT do
============================

* Sign in / OAuth. V3 uses a static API key + ``Authorization: Token``
  header. No refresh flow.
* Batch conversion (API supports it; deferred pending volume signal).
* Response schema validation beyond "shape looks like JSON dict/list".
* Retry with backoff. If Cuelinks is down, we fall to next network.
* Persist the key. Env-only; no secret writes.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from genlab_core.cache.disk_cache import Cache

logger = logging.getLogger(__name__)

_CUELINKS_V3_BASE = "https://developers.cuelinks.com/pub_api/v3"
_REQUEST_TIMEOUT_SEC = 10

# Endpoint-specific TTLs.
_CAMPAIGNS_CACHE_TTL_HOURS = 6  # Merchants + EPCs shift; refresh often-ish
_CONVERT_CACHE_TTL_HOURS = 24 * 30  # (target_url, subid) → short URL is stable

# Amazon domains — the load-bearing guard. Adding a new geo (amazon.de,
# amazon.co.jp, etc.) means Cuelinks becomes a no-attribution proxy
# there too. Update this set explicitly rather than a broad regex so
# every addition surfaces in code review.
_AMAZON_DOMAINS: frozenset[str] = frozenset(
    {
        "amazon.com",
        "amazon.in",
        "amazon.co.uk",
        "amazon.de",
        "amazon.co.jp",
        "amazon.ca",
        "amazon.com.au",
        "amazon.fr",
        "amazon.it",
        "amazon.es",
    }
)

# Module-level cache. Shared .tmp/cache dir so operator disk_cleanup
# clears all third-party caches with one sweep.
_campaigns_cache = Cache(cache_dir=".tmp/cache/cuelinks-campaigns", max_entries=200)
_convert_cache = Cache(cache_dir=".tmp/cache/cuelinks-convert", max_entries=10000)


class AmazonUrlNotAllowed(ValueError):
    """Raised when a caller tries to route an Amazon URL through Cuelinks.

    See the module docstring + ``geo_link_resolver.py:308-327`` for
    the 2026-06-14 audit that established this invariant. The class
    is a subclass of ValueError so existing broad-except handlers
    treat it as a caller error, not a network failure.
    """


def _is_amazon_url(url: str) -> bool:
    """Return True iff ``url``'s netloc matches an Amazon storefront.

    Uses ``urllib.parse`` so query strings and paths can't smuggle a
    non-Amazon domain past the check. Case-insensitive comparison —
    Amazon URLs are always lowercase-hostname in practice but we
    normalise defensively.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except (ValueError, AttributeError):
        return False
    host = (parsed.netloc or "").lower().split(":")[0]
    # Strip leading 'www.' to match the domain set entries.
    if host.startswith("www."):
        host = host[4:]
    return host in _AMAZON_DOMAINS


def verify() -> dict[str, Any] | None:
    """Hit ``/ping`` to confirm the API key is valid + capture publisher identity.

    Returns the parsed JSON body on success, ``None`` on any failure
    (missing key / network error / non-2xx). Publisher identity is
    NOT cached — verify() is intended to be called at startup, on
    key-rotation, or from a health-check probe.
    """
    key = _get_key()
    if not key:
        return None
    body = _get(f"{_CUELINKS_V3_BASE}/ping", key=key)
    if body is None:
        return None
    if isinstance(body, dict):
        return body
    logger.warning("[cuelinks] cuelinks_ping_failed unexpected_shape type=%s", type(body).__name__)
    return None


def list_campaigns(
    *,
    access_status: str = "open",
    sort: str = "epc_7d",
    order: str = "desc",
    per_page: int = 100,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Fetch merchant campaigns, filterable + sorted by EPC.

    Parameters mirror the V3 API's query string. Defaults match the
    "high-EPC merchants we can actually earn from" case the operator
    is most likely to want: ``open`` access status + 7-day EPC
    descending.

    Cache is keyed by the exact query so different callers with
    different filters don't pollute each other. Cache TTL is 6h
    (merchants + EPCs shift; refresh often-ish).

    Returns a list of campaign dicts on success (may be empty),
    ``[]`` on any failure. Caller responsible for handling the
    empty case (fall back to prior curated list, alert operator,
    etc.).
    """
    key = _get_key()
    if not key:
        return []

    params = {
        "access_status": access_status,
        "sort": sort,
        "order": order,
        "per_page": str(per_page),
    }
    query = urllib.parse.urlencode(params)
    url = f"{_CUELINKS_V3_BASE}/campaigns?{query}"

    cache_key = _cache_key_for(f"campaigns::{query}")
    if not force_refresh:
        cached = _campaigns_cache.get(cache_key, ttl_hours=_CAMPAIGNS_CACHE_TTL_HOURS)
        if cached is not None and isinstance(cached, list):
            return cached

    body = _get(url, key=key)
    if body is None:
        return []
    campaigns = _parse_campaigns(body)
    if campaigns:
        _campaigns_cache.set(cache_key, campaigns)
    return campaigns


def convert_url(
    target_url: str,
    *,
    subid: str = "",
    shorten: bool = True,
    force_refresh: bool = False,
) -> str:
    """Transform a merchant URL into a tracked Cuelinks affiliate URL.

    The ``subid`` parameter propagates channel/blueprint identity
    into Cuelinks reporting so we can attribute revenue back to
    the source blueprint. Suggested convention:
    ``f"{niche_id}:{blueprint_id[:8]}"``.

    Parameters
    ----------
    target_url:
        Merchant URL to convert. MUST NOT be an Amazon storefront
        (see ``AmazonUrlNotAllowed`` + module docstring).
    subid:
        Sub-ID for revenue attribution. Empty string is allowed but
        makes downstream reporting harder.
    shorten:
        Ask Cuelinks to return the shortened form (default True).
    force_refresh:
        Skip cache and re-call the API.

    Returns
    -------
    str
        Tracked Cuelinks URL, or ``""`` on any failure. Empty string
        means the caller should fall back to another network's URL.

    Raises
    ------
    AmazonUrlNotAllowed
        When ``target_url`` matches an Amazon storefront. This is
        a hard failure by design — the 2026-06-14 audit established
        that Cuelinks-brokered Amazon links earn ₹0. Fix the caller
        to use ``amazon_us`` / ``amazon_in`` adapters instead. See
        ``geo_link_resolver.py:308-327`` for full rationale.
    """
    if not target_url or not target_url.startswith(("http://", "https://")):
        return ""

    if _is_amazon_url(target_url):
        raise AmazonUrlNotAllowed(
            f"Amazon URL must not route through Cuelinks (2026-06-14 audit "
            f"proved ₹0 commission): {target_url[:80]}. Use amazon_us / "
            f"amazon_in adapters directly. See geo_link_resolver.py:308-327 "
            f"and monetization/cuelinks_client.py module docstring."
        )

    key = _get_key()
    if not key:
        return ""

    # Cache keyed by (target_url, subid, shorten). Different subids
    # produce different tracked URLs and must NOT share cache entries
    # (would misattribute revenue across channels).
    cache_key = _cache_key_for(f"convert::{target_url}::{subid}::{shorten}")
    if not force_refresh:
        cached = _convert_cache.get(cache_key, ttl_hours=_CONVERT_CACHE_TTL_HOURS)
        if cached and isinstance(cached, str):
            return cached

    payload = {"url": target_url, "subid": subid, "shorten": shorten}
    body = _post(f"{_CUELINKS_V3_BASE}/links/convert", key=key, payload=payload)
    if body is None:
        return ""
    tracked = _parse_convert(body)
    if tracked:
        _convert_cache.set(cache_key, tracked)
    return tracked


# ─── Internal helpers ────────────────────────────────────────────────


def _get_key() -> str:
    key = os.environ.get("CUELINKS_V3_API_KEY", "").strip()
    if not key:
        logger.debug("[cuelinks] CUELINKS_V3_API_KEY unset — cuelinks disabled")
    return key


def _cache_key_for(seed: str) -> str:
    import hashlib

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"cuelinks-{digest[:32]}"


# 2026-07-17: Cuelinks V3 WAF returns HTTP 403 for requests carrying
# the default Python-urllib/X.Y User-Agent. Direct curl works. Adding
# a real UA restores 200. Discovered live during first API-key
# provisioning — confirmed with side-by-side curl vs client.
_USER_AGENT = "GenLab/1.0 (+https://github.com/AnarchistSid/genlab-platform)"


def _get(url: str, *, key: str) -> Any:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Token {key}",
            "User-Agent": _USER_AGENT,
        },
    )
    return _execute(request, tag="get", url=url)


def _post(url: str, *, key: str, payload: dict[str, Any]) -> Any:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Token {key}",
            "User-Agent": _USER_AGENT,
        },
    )
    return _execute(request, tag="post", url=url)


def _execute(request: urllib.request.Request, *, tag: str, url: str) -> Any:
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        logger.warning(
            "[cuelinks] cuelinks_%s_failed http_status=%s url=%s",
            tag,
            exc.code,
            url[:80],
        )
        return None
    except urllib.error.URLError as exc:
        logger.warning(
            "[cuelinks] cuelinks_%s_failed network_error=%s url=%s",
            tag,
            exc.reason,
            url[:80],
        )
        return None
    except TimeoutError:
        logger.warning("[cuelinks] cuelinks_%s_failed timeout url=%s", tag, url[:80])
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[cuelinks] cuelinks_%s_failed json_decode_error raw=%s", tag, raw[:200])
        return None


def _parse_campaigns(body: Any) -> list[dict[str, Any]]:
    """Extract the campaigns list from a V3 /campaigns response.

    The V3 docs advertise a paginated response with a top-level
    ``campaigns`` (or ``data``) list; we try both. If neither is
    present but the body IS a list, treat it as the list directly
    (defensive against unexpected wrapper stripping).
    """
    if isinstance(body, list):
        return [c for c in body if isinstance(c, dict)]
    if isinstance(body, dict):
        for key in ("campaigns", "data", "results"):
            candidate = body.get(key)
            if isinstance(candidate, list):
                return [c for c in candidate if isinstance(c, dict)]
    logger.warning(
        "[cuelinks] cuelinks_campaigns_parse_failed unknown_shape type=%s",
        type(body).__name__,
    )
    return []


def list_conversions(
    from_date: str,
    to_date: str,
    *,
    per_page: int = 200,
) -> list[dict[str, Any]]:
    """Fetch confirmed conversions in the [from_date, to_date] range.

    2026-07-17 (Layer 2 batch 2 monetization). Cuelinks V3 exposes
    conversion polling — until this method existed, Gen Lab had
    ZERO real conversion attribution: `affiliate_revenue` rows were
    all `clicks × 0.02` estimation, `affiliate_conversions` was
    empty, 108-arm product bandit had 0 observations.

    Dates in ISO 8601 date format (YYYY-MM-DD). API returns confirmed
    conversions only (pending/reversed conversions filtered server-
    side per Cuelinks convention).

    Cache is deliberately NOT applied — conversion data should always
    reflect the latest server state so the daily import cron can
    detect newly-confirmed conversions from prior windows.

    Returns
    -------
    list of conversion dicts with fields normalized:
        - conversion_id: str
        - order_id: str
        - subid: str  (blueprint attribution — was set at click-time)
        - campaign_name: str
        - sale_amount: float (in INR by default)
        - commission_amount: float
        - currency: str
        - status: str ("confirmed" | "pending" | "reversed")
        - conversion_time: str (ISO 8601)
    Empty list on API failure or missing key (fail-open — never
    crashes the importer).
    """
    key = _get_key()
    if not key:
        return []

    params = {
        "from_date": from_date,
        "to_date": to_date,
        "per_page": str(per_page),
    }
    query = urllib.parse.urlencode(params)
    # V3 endpoint is /transactions (real-time conversions with cursor
    # sync per developer portal docs). Earlier draft used /conversions
    # which 404s. Verified live 2026-07-17.
    url = f"{_CUELINKS_V3_BASE}/transactions?{query}"

    body = _get(url, key=key)
    if body is None:
        return []

    return _parse_conversions(body)


def _parse_conversions(body: Any) -> list[dict[str, Any]]:
    """Parse Cuelinks V3 /transactions response into a normalized list.

    Handles the multiple shapes Cuelinks V3 has been observed to
    return: bare list, {"data": [...]}, {"conversions": [...]}.
    Fail-open — unknown shape returns [].
    """
    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        for k in ("data", "conversions", "results", "items"):
            if isinstance(body.get(k), list):
                rows = body[k]
                break
        else:
            logger.warning(
                "[cuelinks] cuelinks_conversions_parse_failed unknown_shape keys=%s",
                list(body.keys())[:10],
            )
            return []
    else:
        return []

    normalized: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            normalized.append(
                {
                    "conversion_id": str(
                        row.get("conversion_id") or row.get("id") or ""
                    ),
                    "order_id": str(row.get("order_id") or ""),
                    "subid": str(row.get("subid") or row.get("sub_id") or ""),
                    "campaign_name": str(
                        row.get("campaign_name") or row.get("merchant") or ""
                    ),
                    "sale_amount": float(
                        row.get("sale_amount") or row.get("order_amount") or 0
                    ),
                    "commission_amount": float(
                        row.get("commission_amount") or row.get("commission") or 0
                    ),
                    "currency": str(row.get("currency") or "INR"),
                    "status": str(row.get("status") or "confirmed"),
                    "conversion_time": str(
                        row.get("conversion_time") or row.get("created_at") or ""
                    ),
                }
            )
        except (TypeError, ValueError) as exc:
            logger.debug("[cuelinks] skipping malformed conversion row: %s", exc)
    return normalized


def _parse_convert(body: Any) -> str:
    """Pull the tracked URL out of a V3 /links/convert response.

    Try the most-likely keys first. Validate the returned URL looks
    like a Cuelinks tracked link before trusting it (guards against
    the API returning the input URL unchanged on quota-exhaustion,
    which would silently disable tracking).
    """
    if not isinstance(body, dict):
        return ""
    for key in ("tracking_url", "url", "short_url", "cuelinks_url", "data"):
        candidate = body.get(key)
        if isinstance(candidate, str) and (
            "cuelinks.com" in candidate or "linksredirect.com" in candidate
        ):
            return candidate
    logger.warning(
        "[cuelinks] cuelinks_convert_parse_failed unknown_shape keys=%s",
        list(body.keys())[:10],
    )
    return ""
