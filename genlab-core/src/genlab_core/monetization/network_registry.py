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
    """EarnKaro adapter — Indian deal aggregator.

    Link generation requires a live POST to ``https://ekaro.in/api/converter/public``
    with ``EARNKARO_CONVERT_KEY`` as the API token. The shortener returns
    an ``ekaro.in/...`` URL that retains attribution. Until the HTTP
    client + caching layer ships (task #34b), ``generate_url`` is
    intentionally a credential-gated stub: returns empty string when
    the env var is missing, raises an explicit error when it's set but
    the live integration hasn't been turned on.
    """

    network_id: str = "earnkaro"
    display_name: str = "EarnKaro"
    convert_key: str = field(default_factory=lambda: os.environ.get("EARNKARO_CONVERT_KEY", ""))

    def validate_url(self, url: str) -> bool:
        return "ekaro.in" in url or "earnkaro.com" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        if not self.convert_key:
            # Graceful skip — the affiliate matcher can fall back to the
            # next network in the candidate list rather than crashing.
            return ""
        raise NotImplementedError(
            "EarnKaro live API integration not yet shipped — see task #34b. "
            "EARNKARO_CONVERT_KEY is set, but the HTTP converter call "
            "hasn't been wired. Until then, EarnKaro links must be "
            "pre-generated via earnkaro.com and supplied to the matcher."
        )


@dataclass
class ImpactAdapter:
    """Impact.com adapter — global affiliate network.

    Impact.com's deep links are advertiser-specific: each campaign has
    a tracking template like
    ``https://{publisher}.sjv.io/c/{account_sid}/{advertiser_id}/{campaign_id}?...``.
    Adding live support requires a per-advertiser config map plus
    OAuth credential rotation, which is task #34c.
    """

    network_id: str = "impact"
    display_name: str = "Impact.com"
    account_sid: str = field(default_factory=lambda: os.environ.get("IMPACT_ACCOUNT_SID", ""))
    api_token: str = field(default_factory=lambda: os.environ.get("IMPACT_API_TOKEN", ""))

    def validate_url(self, url: str) -> bool:
        return "impact.com" in url or "sjv.io" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        if not (self.account_sid and self.api_token):
            return ""  # graceful skip — let the matcher try the next network
        raise NotImplementedError(
            "Impact.com live API integration not yet shipped — see task #34c. "
            "IMPACT_ACCOUNT_SID + IMPACT_API_TOKEN are both set, but the "
            "per-advertiser deep-link generation needs a per-campaign "
            "config map that hasn't been built. Until then, Impact links "
            "must be pre-generated and supplied to the matcher."
        )


@dataclass
class ShareASaleAdapter:
    """ShareASale adapter — US affiliate network.

    Link template:
        https://shareasale.com/r.cfm?b={banner_id}&u={user_id}&m={merchant_id}&afftrack={sub_id}&urllink={encoded_target}

    Requires no API call — pure template substitution. The publisher's
    ``user_id`` is constant (one env var); ``banner_id`` and
    ``merchant_id`` come from the product record (each affiliated product
    knows which ShareASale merchant + banner it's promoting).

    Falls back to empty string when ``SHAREASALE_USER_ID`` isn't set
    so the matcher can pick a different network without crashing.
    """

    network_id: str = "shareasale"
    display_name: str = "ShareASale"
    user_id: str = field(default_factory=lambda: os.environ.get("SHAREASALE_USER_ID", ""))

    def validate_url(self, url: str) -> bool:
        return "shareasale.com" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        if not self.user_id:
            return ""  # graceful skip
        banner_id = kwargs.get("banner_id", "")
        merchant_id = kwargs.get("merchant_id", "")
        if not (banner_id and merchant_id):
            # The product record didn't carry ShareASale-specific keys;
            # nothing to template against. Skip rather than build an
            # invalid URL.
            return ""
        sub_id = kwargs.get("sub_id", "")
        target_url = kwargs.get("target_url", "")
        params = {
            "b": str(banner_id),
            "u": self.user_id,
            "m": str(merchant_id),
        }
        if sub_id:
            params["afftrack"] = str(sub_id)
        if target_url:
            params["urllink"] = target_url
        return "https://shareasale.com/r.cfm?" + urllib.parse.urlencode(params)


@dataclass
class CJAffiliateAdapter:
    """CJ Affiliate adapter — global affiliate network.

    Link template:
        https://www.{cj_domain}/click-{publisher_id}-{ad_id}?url={encoded_target}&sid={sub_id}

    CJ rotates traffic across multiple click-tracking domains
    (anrdoezrs.net, kqzyfj.com, dpbolvw.net, tkqlhce.com, jdoqocy.com).
    For deterministic output the adapter defaults to anrdoezrs.net; the
    caller can override via ``cj_domain`` kwarg.

    Requires no API call — pure template substitution. The publisher's
    ``publisher_id`` is constant (one env var); ``ad_id`` comes from
    the product record.
    """

    network_id: str = "cj"
    display_name: str = "CJ Affiliate"
    publisher_id: str = field(default_factory=lambda: os.environ.get("CJ_PUBLISHER_ID", ""))

    def validate_url(self, url: str) -> bool:
        # CJ uses multiple click-tracking subdomains. Validate any of them.
        cj_domains = (
            "cj.com",
            "anrdoezrs.net",
            "dpbolvw.net",
            "kqzyfj.com",
            "tkqlhce.com",
            "jdoqocy.com",
        )
        return any(d in url for d in cj_domains)

    def generate_url(self, product_id: str, **kwargs) -> str:
        if not self.publisher_id:
            return ""  # graceful skip
        ad_id = kwargs.get("ad_id", "")
        if not ad_id:
            return ""
        cj_domain = kwargs.get("cj_domain", "anrdoezrs.net")
        target_url = kwargs.get("target_url", "")
        sub_id = kwargs.get("sub_id", "")
        base = f"https://www.{cj_domain}/click-{self.publisher_id}-{ad_id}"
        params: dict[str, str] = {}
        if target_url:
            params["url"] = target_url
        if sub_id:
            params["sid"] = str(sub_id)
        if not params:
            return base
        return f"{base}?{urllib.parse.urlencode(params)}"


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
