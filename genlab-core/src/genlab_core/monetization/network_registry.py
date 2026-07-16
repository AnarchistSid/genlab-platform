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

import logging
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
    """Cuelinks affiliate adapter — routes non-Amazon merchant URLs
    through the V3 API for tracked attribution.

    ## 2026-07-16 re-integration (PR 3 of 3)

    The 2026-06-14 audit (commit ``1444565d``) removed Cuelinks
    entirely because the pre-V3 stub built ``linksredirect.com`` URLs
    that wrapped bare Amazon links WITHOUT the Amazon Associates tag.
    Every redirect earned ₹0.

    The V3 re-integration re-scopes Cuelinks: it's the broker for
    non-Amazon merchants (Flipkart, Myntra, Ajio, Meesho, Nykaa, etc.)
    where Cuelinks IS the actual affiliate network. Amazon URLs are
    HARD-BLOCKED by the client (``cuelinks_client.AmazonUrlNotAllowed``
    raises); this adapter catches that exception, logs at WARNING,
    and returns ``""`` so the resolver falls to the next candidate
    (which for Amazon products means the direct amazon_us / amazon_in
    adapter).

    ## Kwargs

    * ``product_url`` (required) — the merchant URL to route through
      Cuelinks. Must NOT be an Amazon storefront.
    * ``subid`` (optional) — channel/blueprint attribution string.
      Suggested: ``f"{niche_id}:{blueprint_id[:8]}"``. Empty allowed
      but makes downstream Cuelinks reporting harder.

    Returns the tracked Cuelinks URL on success, ``""`` on:
      - missing product_url
      - missing CUELINKS_V3_API_KEY env var
      - Amazon URL (caught + logged)
      - Cuelinks V3 API failure (network / auth / rate limit)
    """

    network_id: str = "cuelinks"
    display_name: str = "Cuelinks"
    publisher_id: str = field(default_factory=lambda: os.environ.get("CUELINKS_PUBLISHER_ID", ""))

    def validate_url(self, url: str) -> bool:
        # V3 tracked URLs use ``cuelinks.com`` as the host; the
        # pre-V3 ``linksredirect.com`` shape is also accepted for
        # backwards compat with any historical cached values.
        return "cuelinks.com" in url or "linksredirect.com" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        # Late import so importing this module doesn't force cuelinks_client
        # into every affiliate consumer's dep chain.
        from genlab_core.monetization import cuelinks_client

        product_url = kwargs.get("product_url", "")
        if not product_url:
            # No merchant URL supplied — cannot route through Cuelinks.
            # Returning "" lets the resolver fall to the next candidate.
            # (Pre-V3 stub used to synthesise an amazon.in URL here;
            # that was the exact ₹0-commission pathway the audit killed.)
            return ""

        subid = kwargs.get("subid", "")
        try:
            return cuelinks_client.convert_url(product_url, subid=subid)
        except cuelinks_client.AmazonUrlNotAllowed as exc:
            # Catalog contains a cuelinks entry pointing at an Amazon URL —
            # the pin test (test_no_product_uses_cuelinks_for_amazon_url)
            # is the primary defense but a race between config-load and
            # test-run could let one through. Log LOUDLY so operator sees
            # it in dashboard alerts and fix the catalog.
            logging.getLogger(__name__).warning(
                "[cuelinks] catalog_cuelinks_amazon_url_leak product_id=%s url=%s exc=%s",
                product_id,
                product_url[:80],
                exc,
            )
            return ""


@dataclass
class EarnKaroAdapter:
    """EarnKaro adapter — Indian deal aggregator.

    Live since task #34b (2026-06-13). Calls ``ekaro.in/api/converter/public``
    via ``earnkaro_client.convert_url`` to shorten a target merchant URL
    into an ekaro.in link carrying our publisher's attribution. Responses
    are disk-cached for 30 days because the shortened URLs are stable.

    Required kwargs:
        target_url: The merchant URL to shorten (e.g. amazon.in/dp/...).
                    Without it there's nothing to convert.

    Returns "" gracefully on any failure — env var missing, API down,
    response shape unrecognized. The affiliate matcher uses this to
    fall back to other networks in the candidate list.
    """

    network_id: str = "earnkaro"
    display_name: str = "EarnKaro"
    convert_key: str = field(default_factory=lambda: os.environ.get("EARNKARO_CONVERT_KEY", ""))

    def validate_url(self, url: str) -> bool:
        return "ekaro.in" in url or "earnkaro.com" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        if not self.convert_key:
            return ""
        target_url = kwargs.get("target_url", "")
        if not target_url:
            return ""
        # Lazy import — earnkaro_client touches the disk cache module,
        # which we don't want to load for adapter introspection paths
        # (registry walking, validate_url dispatch, etc.).
        from genlab_core.monetization.earnkaro_client import convert_url

        return convert_url(target_url)


@dataclass
class ImpactAdapter:
    """Impact.com adapter — global affiliate network.

    Live since task #34c (2026-06-13). Per-advertiser tracking templates
    are stored in ``genlab-core/config/impact_advertisers.yaml``;
    ``generate_url`` looks up the operator-chosen advertiser_key kwarg,
    substitutes the template with the target URL + sub_id, and returns
    the click-through URL.

    Required kwargs:
        advertiser_key: lookup key in impact_advertisers.yaml
        target_url:     merchant landing page

    Optional kwargs:
        sub_id:         tracking sub-ID (passed as subId1)

    Returns "" when the advertiser_key is missing, when the env
    IMPACT_ACCOUNT_SID is unset, or when the advertiser isn't in the
    YAML — letting the matcher fall back to other networks.
    """

    network_id: str = "impact"
    display_name: str = "Impact.com"
    account_sid: str = field(default_factory=lambda: os.environ.get("IMPACT_ACCOUNT_SID", ""))

    def validate_url(self, url: str) -> bool:
        return "impact.com" in url or "sjv.io" in url or "7eer.net" in url

    def generate_url(self, product_id: str, **kwargs) -> str:
        if not self.account_sid:
            return ""
        advertiser_key = kwargs.get("advertiser_key", "")
        target_url = kwargs.get("target_url", "")
        if not (advertiser_key and target_url):
            return ""
        from genlab_core.monetization.impact_client import build_deep_link

        return build_deep_link(
            advertiser_key=advertiser_key,
            target_url=target_url,
            account_sid=self.account_sid,
            sub_id=kwargs.get("sub_id", ""),
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
