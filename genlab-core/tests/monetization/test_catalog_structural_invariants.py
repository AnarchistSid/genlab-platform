"""Pin catalog structural invariants (L3 PR 13, 2026-07-07).

The Monetization Layer 3 sprint's ProductSelector + reward wire depend
on several implicit assumptions about the catalog YAML shape. The
audit that motivated the sprint (memory:
monetization-gap-analysis-2026-07-07) found that the catalog is
CURRENTLY well-formed on all these dimensions — every enabled product
has a price, every network has a commission, no URL is a placeholder.

But the catalog is hand-edited operator YAML; a stray edit could
regress any of these silently, breaking the value-weight ranking in
:func:`product_selector._value_weight` or leaving affiliate URLs
pointing at example.com.

These pin tests lock in the invariants. Adding a new product without
a `price_inr` (or with a `direct` network that omits `commission_pct`)
will fail CI at the pin, not silently in production.

Invariants:
  1. Every enabled product has ``price_inr`` (int > 0).
  2. Every network entry has ``commission_pct`` (float, may be 0.0
     for direct-brand links that have no affiliate program).
  3. No product URL contains the placeholder ``example.com``.
  4. Every network URL is HTTPS.
  5. ``enabled`` field is boolean, not string (a common YAML typo
     is to write ``"true"`` instead of ``true`` — the string is
     truthy, so ``if enabled:`` passes, but downstream code that
     checks ``is True`` fails).
  6. Every product has a ``keywords`` list (matcher requires it to
     score keyword hits).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "affiliate_catalog.yaml"


@pytest.fixture(scope="module")
def catalog() -> dict:
    """Load the real catalog once for all tests."""
    with open(_CATALOG_PATH) as f:
        return yaml.safe_load(f) or {}


def _enabled_products(catalog: dict) -> list[tuple[str, str, dict]]:
    """Yield (niche_id, product_name, product_dict) for every enabled
    product in the catalog. Disabled products are excluded — the
    matcher skips them at runtime and they're not held to production
    invariants."""
    out = []
    for niche_id, niche in (catalog.get("niches") or {}).items():
        for product in niche.get("products") or []:
            if not product.get("enabled", True):
                continue
            out.append((niche_id, product.get("name", ""), product))
    return out


class TestPriceCoverage:
    """Invariant 1: every enabled product must have ``price_inr``.

    The ProductSelector's value_weight formula is
    ``log(price × commission + e)`` — a missing price collapses the
    weight to the floor (1.0), so the arm ranks purely on posterior
    with no revenue signal. That's a graceful degradation, but a
    catalog operator wouldn't INTEND to ship an unpriced product; the
    price is present-and-correct on every existing entry today.
    Pin so a future entry can't silently omit it."""

    def test_every_enabled_product_has_price_inr(self, catalog):
        missing = []
        for niche_id, name, product in _enabled_products(catalog):
            if not product.get("price_inr"):
                missing.append(f"{niche_id} / {name}")
        assert not missing, f"{len(missing)} enabled product(s) missing price_inr: {missing[:5]}"

    def test_price_inr_is_positive_int(self, catalog):
        wrong_type = []
        for niche_id, name, product in _enabled_products(catalog):
            price = product.get("price_inr")
            if price is None:
                continue  # covered by previous test
            if not isinstance(price, int) or price <= 0:
                wrong_type.append(f"{niche_id} / {name}: {price!r}")
        assert not wrong_type, f"non-int-or-non-positive price_inr: {wrong_type[:5]}"


class TestCommissionCoverage:
    """Invariant 2: every network must have ``commission_pct``,
    including direct-brand links (they should be explicit 0.0, not
    missing — see the direct-network trap documented in the L3 sprint
    audit)."""

    def test_every_network_has_commission_pct(self, catalog):
        missing = []
        for niche_id, name, product in _enabled_products(catalog):
            for net_name, net_info in (product.get("networks") or {}).items():
                if not isinstance(net_info, dict):
                    continue
                if "commission_pct" not in net_info:
                    missing.append(f"{niche_id} / {name} / {net_name}")
        assert not missing, f"{len(missing)} network(s) missing commission_pct: {missing[:5]}"

    def test_commission_pct_is_non_negative_number(self, catalog):
        """0.0 is valid (direct-brand link with no affiliate program).
        Negative commissions are always a bug."""
        bad = []
        for niche_id, name, product in _enabled_products(catalog):
            for net_name, net_info in (product.get("networks") or {}).items():
                if not isinstance(net_info, dict):
                    continue
                comm = net_info.get("commission_pct")
                if comm is None:
                    continue
                if not isinstance(comm, (int, float)) or comm < 0:
                    bad.append(f"{niche_id} / {name} / {net_name}: {comm!r}")
        assert not bad, f"invalid commission_pct: {bad[:5]}"


class TestUrlHygiene:
    """Invariants 3 + 4: URLs must be real HTTPS links, not placeholders."""

    def test_no_placeholder_urls(self, catalog):
        """example.com is the canonical placeholder — an operator
        pasting example.com into the catalog is a config mistake, not
        a valid product entry. Blocking here catches the mistake at
        commit rather than at click-time when the redirect handler
        would 404."""
        placeholders = []
        for niche_id, name, product in _enabled_products(catalog):
            for net_name, net_info in (product.get("networks") or {}).items():
                if not isinstance(net_info, dict):
                    continue
                url = net_info.get("url", "")
                if "example.com" in url or "placeholder" in url.lower():
                    placeholders.append(f"{niche_id} / {name} / {net_name}: {url}")
        assert not placeholders, f"placeholder URLs found: {placeholders}"

    def test_all_urls_https(self, catalog):
        """HTTP-only URLs get downranked by every platform + browsers
        show insecure warnings. Bio-link hub should never redirect to
        http://. Placeholder URLs from L3 PR 13 use ${ENV_VAR}
        substitution which passes through the check (won't have http:).
        """
        non_https = []
        for niche_id, name, product in _enabled_products(catalog):
            for net_name, net_info in (product.get("networks") or {}).items():
                if not isinstance(net_info, dict):
                    continue
                url = net_info.get("url", "")
                if not url:
                    continue
                # env-var substitutions like ${AMAZON_US_AFFILIATE_TAG}
                # are legitimate — the sub happens in-process, the
                # final URL is https:. Only fail if the literal URL
                # (with any placeholders left) does NOT start https:.
                if not url.startswith("https://"):
                    non_https.append(f"{niche_id} / {name} / {net_name}: {url}")
        assert not non_https, f"non-HTTPS URLs: {non_https}"


class TestFieldTypes:
    """Invariant 5: ``enabled`` and other structural fields must be
    bool/list, not strings. A YAML typo like ``enabled: "true"`` is
    truthy but breaks any code that does ``is True``."""

    def test_enabled_is_bool(self, catalog):
        wrong_type = []
        for niche_id, niche in (catalog.get("niches") or {}).items():
            for product in niche.get("products") or []:
                name = product.get("name", "")
                enabled_val = product.get("enabled", True)
                if not isinstance(enabled_val, bool):
                    wrong_type.append(f"{niche_id} / {name}: {enabled_val!r}")
        assert not wrong_type, f"enabled field is not bool (likely YAML typo): {wrong_type[:5]}"


class TestKeywordsCoverage:
    """Invariant 6: every enabled product needs a keywords list. The
    ``affiliate_matcher._keyword_hits`` function requires it to score
    matches; a missing/empty list means the product can NEVER match a
    story and shouldn't be in the catalog."""

    def test_every_enabled_product_has_keywords(self, catalog):
        missing = []
        for niche_id, name, product in _enabled_products(catalog):
            keywords = product.get("keywords") or []
            if not keywords:
                missing.append(f"{niche_id} / {name}")
        assert not missing, f"{len(missing)} enabled product(s) missing keywords: {missing[:5]}"
