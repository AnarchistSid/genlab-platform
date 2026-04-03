"""Network adapter registry for affiliate link generation and validation.

Each network adapter knows how to:
1. Validate a tracking link
2. Generate a tracking link from a product identifier
3. (Future) Fetch commission rates via API

Usage:
    from genlab_core.monetization.network_registry import get_adapter, ADAPTERS
    adapter = get_adapter("amazon_onelink")
    url = adapter.generate_url("B0CY5QW186", tag="your-tag-20")
"""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass, field
from typing import Protocol


class NetworkAdapter(Protocol):
    """Protocol for affiliate network adapters."""
    network_id: str
    display_name: str

    def validate_url(self, url: str) -> bool: ...
    def generate_url(self, product_id: str, **kwargs) -> str: ...


@dataclass
class AmazonOneLinkAdapter:
    network_id: str = "amazon_onelink"
    display_name: str = "Amazon (OneLink)"
    default_tag: str = field(default_factory=lambda: os.environ.get("AMAZON_US_AFFILIATE_TAG", ""))

    def validate_url(self, url: str) -> bool:
        return "amazon.com" in url and "tag=" in url and "linkCode" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        tag = kwargs.get("tag", self.default_tag)
        return f"https://www.amazon.com/dp/{product_id}?tag={tag}&linkCode=ll1"


@dataclass
class AmazonINAdapter:
    network_id: str = "amazon"
    display_name: str = "Amazon India"
    default_tag: str = field(default_factory=lambda: os.environ.get("AMAZON_IN_AFFILIATE_TAG", ""))

    def validate_url(self, url: str) -> bool:
        return "amazon.in" in url and "tag=" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        tag = kwargs.get("tag", self.default_tag)
        return f"https://www.amazon.in/dp/{product_id}?tag={tag}"


@dataclass
class AmazonUSAdapter:
    network_id: str = "amazon_us"
    display_name: str = "Amazon US"
    default_tag: str = field(default_factory=lambda: os.environ.get("AMAZON_US_AFFILIATE_TAG", ""))

    def validate_url(self, url: str) -> bool:
        return "amazon.com" in url and "tag=" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        tag = kwargs.get("tag", self.default_tag)
        return f"https://www.amazon.com/dp/{product_id}?tag={tag}"


@dataclass
class CuelinksAdapter:
    network_id: str = "cuelinks"
    display_name: str = "Cuelinks"
    publisher_id: str = field(default_factory=lambda: os.environ.get("CUELINKS_PUBLISHER_ID", ""))

    def validate_url(self, url: str) -> bool:
        return "linksredirect.com" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        product_url = kwargs.get("product_url", "")
        if not product_url:
            _tag = os.environ.get("AMAZON_IN_AFFILIATE_TAG", "")
            product_url = f"https://www.amazon.in/dp/{product_id}?tag={_tag}"
        encoded = urllib.parse.quote(product_url, safe="")
        return f"https://linksredirect.com/?cid={self.publisher_id}&source=linkkit&url={encoded}"


@dataclass
class EarnKaroAdapter:
    network_id: str = "earnkaro"
    display_name: str = "EarnKaro"

    def validate_url(self, url: str) -> bool:
        return "ekaro.in" in url or "earnkaro.com" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        # EarnKaro links must be generated via their website/API
        raise NotImplementedError("EarnKaro links must be generated via earnkaro.com")


@dataclass
class ImpactAdapter:
    network_id: str = "impact"
    display_name: str = "Impact.com"

    def validate_url(self, url: str) -> bool:
        return "impact.com" in url or "sjv.io" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        raise NotImplementedError("Impact.com links require campaign-specific setup")


@dataclass
class ShareASaleAdapter:
    network_id: str = "shareasale"
    display_name: str = "ShareASale"

    def validate_url(self, url: str) -> bool:
        return "shareasale.com" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        raise NotImplementedError("ShareASale credentials not configured")


@dataclass
class CJAffiliateAdapter:
    network_id: str = "cj"
    display_name: str = "CJ Affiliate"

    def validate_url(self, url: str) -> bool:
        return "cj.com" in url or "anrdoezrs.net" in url or "dpbolvw.net" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        raise NotImplementedError("CJ Affiliate credentials not configured")


# Global registry
ADAPTERS: dict[str, NetworkAdapter] = {
    "amazon_onelink": AmazonOneLinkAdapter(),
    "amazon": AmazonINAdapter(),
    "amazon_us": AmazonUSAdapter(),
    "cuelinks": CuelinksAdapter(),
    "earnkaro": EarnKaroAdapter(),
    "impact": ImpactAdapter(),
    "shareasale": ShareASaleAdapter(),
    "cj": CJAffiliateAdapter(),
}


def get_adapter(network_id: str) -> NetworkAdapter | None:
    """Get a network adapter by ID. Returns None if not found."""
    return ADAPTERS.get(network_id)


def validate_affiliate_url(url: str) -> str | None:
    """Detect which network an affiliate URL belongs to. Returns network_id or None."""
    for nid, adapter in ADAPTERS.items():
        if adapter.validate_url(url):
            return nid
    return None
