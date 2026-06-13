"""EarnKaro link-shortener HTTP client — task #34b (2026-06-13).

EarnKaro converts third-party product URLs into ekaro.in short links
that carry the publisher's affiliate attribution. The conversion
happens server-side via a single POST to ``ekaro.in/api/converter/public``;
no per-merchant config needed.

Design choices
==============

* **Disk-cached for 30 days.** EarnKaro short links are stable —
  once a target URL has been converted, the shortened URL doesn't
  change unless the upstream merchant URL changes. Caching avoids
  hammering the API and stays within their free-tier rate limits.
* **Fail-graceful.** Any HTTP error / timeout / parse failure returns
  ``""`` so the affiliate matcher can try the next network instead of
  crashing the pipeline. Errors log at WARNING with the underlying
  exception type — operator can grep for `earnkaro_conversion_failed`.
* **Response-shape tolerant.** The exact JSON shape returned by
  EarnKaro's public API isn't documented stably (the field is
  sometimes `data`, sometimes `deal`, sometimes `url`). The parser
  tries each known key; if none match, the raw response is logged
  and "" is returned.
* **Stdlib HTTP only.** Uses ``urllib.request`` to avoid pulling in
  requests/httpx as a runtime dep. The 10s timeout caps the worst
  case for a slow API.
* **Key check on every call** — ``EARNKARO_CONVERT_KEY`` read fresh
  from the environment, not cached, so the operator can rotate keys
  without restarting the pipeline.

What this module does NOT do
============================

* Validate that the returned URL actually works (no HEAD request).
  EarnKaro's response is trusted; their server-side validation is
  what we're paying for.
* Implement bulk conversion (the API supports batch). The pipeline
  converts one URL at a time today; bulk is a future optimization
  if conversion latency dominates a pipeline run.
* Persist the key beyond process lifetime (no .env writing).
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from genlab_core.cache.disk_cache import Cache

logger = logging.getLogger(__name__)

_EARNKARO_API_URL = "https://ekaro.in/api/converter/public"
_REQUEST_TIMEOUT_SEC = 10
_CACHE_TTL_HOURS = 24 * 30  # 30 days — short links are stable

# Module-level cache instance. Reuses the shared .tmp/cache dir so
# operators can clear all third-party caches with one cleanup.
_cache = Cache(cache_dir=".tmp/cache/earnkaro", max_entries=5000)


def convert_url(target_url: str, *, force_refresh: bool = False) -> str:
    """Convert a target product URL to an ekaro.in affiliate short link.

    Parameters
    ----------
    target_url:
        The merchant URL to shorten. Must be a valid http(s) URL.
    force_refresh:
        Skip the disk cache and re-call the API. Useful when the
        operator has reconfigured something upstream and wants a
        fresh conversion.

    Returns
    -------
    str
        The ekaro.in short link, or ``""`` if conversion failed
        (missing key / API error / parse failure). Caller is
        responsible for checking the empty case.
    """
    if not target_url or not target_url.startswith(("http://", "https://")):
        return ""

    key = os.environ.get("EARNKARO_CONVERT_KEY", "").strip()
    if not key:
        # No key → graceful skip. Logged at debug because the affiliate
        # matcher falls back to other networks; this isn't an error.
        logger.debug("[earnkaro] EARNKARO_CONVERT_KEY unset — returning empty")
        return ""

    cache_key = _cache_key_for(target_url)
    if not force_refresh:
        cached = _cache.get(cache_key, ttl_hours=_CACHE_TTL_HOURS)
        if cached and isinstance(cached, str):
            return cached

    short_url = _do_convert(target_url=target_url, key=key)
    if short_url:
        _cache.set(cache_key, short_url)
    return short_url


def _cache_key_for(target_url: str) -> str:
    """Deterministic cache key — safe for disk_cache's key validation
    (alnum + dash). Hashing keeps the key short and predictable."""
    import hashlib

    digest = hashlib.sha256(target_url.encode("utf-8")).hexdigest()
    return f"earnkaro-{digest[:32]}"


def _do_convert(*, target_url: str, key: str) -> str:
    """Single HTTP POST + response parse. Returns "" on any failure.

    Separated from the public ``convert_url`` so unit tests can mock
    this layer with controlled responses, while the public API
    handles cache + env-var checks.
    """
    payload = json.dumps(
        {
            "deal": str(target_url),
            "convert_option": "convert_only",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        _EARNKARO_API_URL,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        logger.warning(
            "[earnkaro] earnkaro_conversion_failed http_status=%s url=%s",
            exc.code,
            target_url[:80],
        )
        return ""
    except urllib.error.URLError as exc:
        logger.warning(
            "[earnkaro] earnkaro_conversion_failed network_error=%s url=%s",
            exc.reason,
            target_url[:80],
        )
        return ""
    except TimeoutError:
        logger.warning("[earnkaro] earnkaro_conversion_failed timeout url=%s", target_url[:80])
        return ""

    return _parse_response(raw, target_url=target_url)


def _parse_response(raw: str, *, target_url: str) -> str:
    """Pull the short URL out of the EarnKaro response.

    EarnKaro's public response shape isn't documented stably — across
    different account tiers / API versions the field name shifts. We
    try the most common keys in order and validate the result looks
    like an ekaro.in URL before trusting it.
    """
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning(
            "[earnkaro] earnkaro_conversion_failed json_decode_error raw=%s",
            raw[:200],
        )
        return ""

    if not isinstance(body, dict):
        return ""

    # Tried-keys order: most likely first. Add more as the operator
    # discovers them in live responses. The validate_url() check below
    # filters out any non-ekaro string accidentally captured.
    for key in ("data", "url", "deal", "short_url", "converted_url"):
        candidate = body.get(key)
        if isinstance(candidate, str) and ("ekaro.in" in candidate or "earnkaro.com" in candidate):
            return candidate

    logger.warning(
        "[earnkaro] earnkaro_conversion_failed unknown_response_shape "
        "keys=%s url=%s",
        list(body.keys()),
        target_url[:80],
    )
    return ""
