"""Amazon Product Advertising API 5.0 client.

Provides product search and ASIN lookup for dynamic affiliate matching.
Requires PA-API credentials (access_key, secret_key, partner_tag).

Note: PA-API requires $5+ trailing 30-day revenue to maintain access.

Usage:
    client = PaapiClient(access_key="...", secret_key="...", partner_tag="your-tag-20")
    products = client.search("PS5 console", search_index="VideoGames")
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import requests

from genlab_core.monetization.paapi_signing import (
    PAAPI_REGION,
    PAAPI_SERVICE,
    sign_request_v4,
)

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent.parent.parent / ".tmp" / "cache" / "paapi"
_CACHE_TTL = 3600  # 1 hour

# PA-API endpoints
_HOSTS = {
    "us": "webservices.amazon.com",
    "in": "webservices.amazon.in",
    "uk": "webservices.amazon.co.uk",
}

# Niche to PA-API SearchIndex
NICHE_SEARCH_INDEX = {
    "gaming": "VideoGames",
    "sports": "SportingGoods",
    "movies": "MoviesAndTV",
    "anime": "Books",
    "ai_creators": "Electronics",
}


@dataclass
class PaapiProduct:
    """Product returned from PA-API."""

    asin: str
    title: str
    price: float
    currency: str
    image_url: str
    detail_url: str
    category: str = ""
    rating: float = 0.0


class PaapiClient:
    """Amazon PA-API 5.0 client with disk caching."""

    def __init__(
        self,
        access_key: str = "",
        secret_key: str = "",
        partner_tag: str = "",
        region: str = "us",
        cache_dir: Path | None = None,
        cache_ttl: int = _CACHE_TTL,
    ) -> None:
        self._access_key = access_key or os.environ.get("PAAPI_ACCESS_KEY", "")
        self._secret_key = secret_key or os.environ.get("PAAPI_SECRET_KEY", "")
        self._partner_tag = partner_tag or os.environ.get("AMAZON_US_AFFILIATE_TAG", "")
        self._host = _HOSTS.get(region, _HOSTS["us"])
        self._cache_dir = cache_dir or _CACHE_DIR
        self._cache_ttl = cache_ttl
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def is_configured(self) -> bool:
        """Check if PA-API credentials are set."""
        return bool(self._access_key and self._secret_key)

    def search(
        self,
        keywords: str,
        search_index: str = "All",
        min_price: int = 500,
        max_results: int = 5,
    ) -> list[PaapiProduct]:
        """Search PA-API for products. Returns cached results if available."""
        if not self.is_configured:
            logger.debug("[PAAPI] Not configured — skipping search")
            return []

        cache_key = self._cache_key(f"search:{keywords}:{search_index}")
        cached = self._read_cache(cache_key)
        if cached is not None:
            return cached

        # Build PA-API SearchItems request
        payload = {
            "Keywords": keywords,
            "SearchIndex": search_index,
            "ItemCount": min(max_results, 10),
            "PartnerTag": self._partner_tag,
            "PartnerType": "Associates",
            "Resources": [
                "ItemInfo.Title",
                "Offers.Listings.Price",
                "Images.Primary.Large",
            ],
        }

        try:
            response = self._signed_request("SearchItems", payload)
            products = self._parse_search_response(response)
            products = [p for p in products if p.price >= min_price]
            self._write_cache(cache_key, products)
            return products[:max_results]
        except Exception as e:
            logger.warning("[PAAPI] Search failed: %s", e)
            return []

    def get_by_asin(self, asin: str) -> PaapiProduct | None:
        """Fetch a single product by ASIN."""
        if not self.is_configured:
            return None

        cache_key = self._cache_key(f"asin:{asin}")
        cached = self._read_cache(cache_key)
        if cached:
            return cached[0] if cached else None

        payload = {
            "ItemIds": [asin],
            "PartnerTag": self._partner_tag,
            "PartnerType": "Associates",
            "Resources": [
                "ItemInfo.Title",
                "Offers.Listings.Price",
                "Images.Primary.Large",
            ],
        }

        try:
            response = self._signed_request("GetItems", payload)
            products = self._parse_items_response(response)
            self._write_cache(cache_key, products)
            return products[0] if products else None
        except Exception as e:
            logger.warning("[PAAPI] GetItems failed for %s: %s", asin, e)
            return None

    def _signed_request(self, operation: str, payload: dict) -> dict:
        """Make a signed PA-API request.

        R-31 (a) PA-API half: wired against the in-tree AWS Sig v4
        implementation in ``paapi_signing.py``. Pinning tests there
        verify the signing chain against AWS's published test
        vectors, so this method only owns the PA-API-specific
        plumbing (path, headers, host/region lookup, HTTP call,
        error mapping).

        Raises:
            ValueError: If the client isn't configured. ``search`` /
                ``get_by_asin`` already gate on ``is_configured``, so
                this branch is the defence in depth.
            requests.HTTPError: On a non-2xx response.
        """
        if not self.is_configured:
            raise ValueError("PA-API not configured: set PAAPI_ACCESS_KEY + PAAPI_SECRET_KEY")

        # PA-API operation → URL path + X-Amz-Target header
        op_lower = operation.lower()  # "SearchItems" → "searchitems"
        path = f"/paapi5/{op_lower}"
        target = f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{operation}"

        body = json.dumps(payload, separators=(",", ":"))

        # Region is host-derived. ``_HOSTS`` and ``PAAPI_REGION`` must
        # stay in sync — a regression test in test_paapi_signing.py
        # pins that every _HOSTS key has a region mapping.
        host_key = next((k for k, h in _HOSTS.items() if h == self._host), "us")
        region = PAAPI_REGION[host_key]

        extra_headers = {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Amz-Target": target,
            "Content-Encoding": "amz-1.0",
        }

        headers = sign_request_v4(
            method="POST",
            host=self._host,
            path=path,
            payload=body,
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=region,
            service=PAAPI_SERVICE,
            extra_headers=extra_headers,
        )

        url = f"https://{self._host}{path}"
        response = requests.post(url, data=body, headers=headers, timeout=10)

        # PA-API uses standard HTTP status codes; a non-2xx is a
        # signing error, a quota issue, or a malformed payload.
        # Surface as HTTPError so the caller's existing try/except
        # in ``search`` / ``get_by_asin`` logs + degrades gracefully.
        response.raise_for_status()
        return response.json()

    def _parse_search_response(self, response: dict) -> list[PaapiProduct]:
        """Parse PA-API SearchItems response."""
        products = []
        for item in response.get("SearchResult", {}).get("Items", []):
            products.append(self._item_to_product(item))
        return products

    def _parse_items_response(self, response: dict) -> list[PaapiProduct]:
        """Parse PA-API GetItems response."""
        products = []
        for item in response.get("ItemsResult", {}).get("Items", []):
            products.append(self._item_to_product(item))
        return products

    def _item_to_product(self, item: dict) -> PaapiProduct:
        """Convert a PA-API item dict to PaapiProduct."""
        title = item.get("ItemInfo", {}).get("Title", {}).get("DisplayValue", "")
        listing = (item.get("Offers", {}).get("Listings") or [{}])[0]
        price_info = listing.get("Price", {})
        image = item.get("Images", {}).get("Primary", {}).get("Large", {})

        return PaapiProduct(
            asin=item.get("ASIN", ""),
            title=title,
            price=price_info.get("Amount", 0),
            currency=price_info.get("Currency", "USD"),
            image_url=image.get("URL", ""),
            detail_url=item.get("DetailPageURL", ""),
        )

    def _cache_key(self, query: str) -> str:
        return hashlib.sha256(query.encode()).hexdigest()[:16]

    def _read_cache(self, key: str) -> list[PaapiProduct] | None:
        """Read from disk cache. Returns None if expired or missing."""
        path = self._cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            if time.time() - data.get("ts", 0) > self._cache_ttl:
                return None
            return [PaapiProduct(**p) for p in data.get("products", [])]
        except Exception:
            return None

    def _write_cache(self, key: str, products: list[PaapiProduct]) -> None:
        """Write products to disk cache."""
        path = self._cache_dir / f"{key}.json"
        try:
            from dataclasses import asdict

            data = {
                "ts": time.time(),
                "products": [asdict(p) for p in products],
            }
            path.write_text(json.dumps(data))
        except Exception as e:
            logger.debug("[PAAPI] Cache write failed: %s", e)
