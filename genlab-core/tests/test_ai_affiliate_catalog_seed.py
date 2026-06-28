"""Pin: ai_creators affiliate catalog seed (PR AB, 2026-06-29).

PR #638's catalog-coverage data showed ai_creators at 0% of blueprints
carrying an affiliate_cta over a 60-day window, vs. a 30% catalog
average (gaming 68%, anime 57%, movies 41%). PR AB seeded ~14 AI-tool
products into the catalog so AffiliateMatch starts finding hits on
BlackboxBrief content.

These pins protect that seed against future regressions:

1. ai_creators has >= 10 products (was 10 before PR AB; floor is 10
   so anyone trimming the catalog below that point fails CI).
2. Every product has the required schema fields the matcher reads.
3. At least 5 products have keywords that overlap with the
   ai_creators positive_keywords lexicon
   (``BlackboxBrief/config/sources.yaml``).
4. No duplicate product names within ai_creators.
5. Every product needing operator signup is consistently marked
   (``enabled: false`` + a ``# signup:`` comment line above the
   placeholder URL block), so operators have a single grep target
   to find what to activate.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Catalog is the committed ``.example.yaml``; the runtime
# ``affiliate_catalog.yaml`` is operator-managed (gitignored) and gets
# the same edits applied at deploy time per the existing
# test_affiliate_catalog_cleanup pattern.
CATALOG_PATH = Path(__file__).parent.parent / "config" / "affiliate_catalog.example.yaml"
BB_SOURCES_PATH = Path(__file__).parent.parent.parent / "BlackboxBrief" / "config" / "sources.yaml"


def _load_catalog() -> dict:
    return yaml.safe_load(CATALOG_PATH.read_text())


def _ai_creators_products() -> list[dict]:
    catalog = _load_catalog()
    return (catalog.get("niches") or {}).get("ai_creators", {}).get("products", []) or []


def _ai_creators_positive_keywords() -> set[str]:
    """Lower-cased positive keywords from BB ai_creators content filter.

    Empty set if file missing — the keyword-overlap test will then
    skip rather than fail in environments without BlackboxBrief
    checked out alongside genlab-core (CI has the full workspace).
    """
    if not BB_SOURCES_PATH.exists():
        return set()
    data = yaml.safe_load(BB_SOURCES_PATH.read_text())
    cf = (data or {}).get("content_filter") or {}
    return {str(k).lower().strip() for k in (cf.get("positive_keywords") or []) if k}


class TestAiCreatorsHasProducts:
    """Pin: ai_creators catalog is non-empty and meets seed floor."""

    def test_catalog_has_ai_creators_products(self):
        products = _ai_creators_products()
        assert len(products) >= 10, (
            f"ai_creators has only {len(products)} products — PR AB seeded "
            f"~14; floor is 10. Reverting the seed without a replacement "
            f"will restore 0% affiliate_cta coverage."
        )


class TestRequiredFields:
    """Pin: every product has the fields AffiliateMatch reads at runtime."""

    REQUIRED = ("name", "keywords", "networks", "price_inr", "category")

    def test_each_product_has_required_fields(self):
        offenders: list[tuple[str, str]] = []
        for product in _ai_creators_products():
            name = product.get("name", "?")
            for field in self.REQUIRED:
                if field not in product:
                    offenders.append((name, field))
        assert offenders == [], (
            f"ai_creators products missing required fields (name, missing_field) pairs: {offenders}"
        )

    def test_each_product_has_at_least_one_keyword(self):
        thin: list[str] = []
        for product in _ai_creators_products():
            keywords = product.get("keywords") or []
            if len(keywords) < 1:
                thin.append(product.get("name", "?"))
        assert thin == [], f"ai_creators products with zero keywords (won't ever match): {thin}"

    def test_each_product_has_networks_dict(self):
        bad: list[str] = []
        for product in _ai_creators_products():
            networks = product.get("networks")
            if not isinstance(networks, dict):
                bad.append(product.get("name", "?"))
        assert bad == [], f"ai_creators products with non-dict networks: {bad}"


class TestKeywordOverlapWithBbLexicon:
    """Pin: keywords map onto the actual BB ai_creators content filter.

    The whole monetization loop is keyword-driven (see ``match_product``
    in ``affiliate_matcher.py``). If catalog keywords don't overlap with
    the keywords that ACTUALLY appear in BB blueprints/captions, the
    seed is theatre. At least 5 products must have at least one
    keyword overlap with the BB ai_creators positive_keywords list.
    """

    def test_keyword_overlap_with_ai_creator_lexicon(self):
        bb_keywords = _ai_creators_positive_keywords()
        if not bb_keywords:
            # BB checkout not available — skip rather than fail.
            # CI runs the full workspace so this branch only triggers
            # in isolated genlab-core dev environments.
            import pytest

            pytest.skip("BlackboxBrief/config/sources.yaml not available")

        products = _ai_creators_products()
        products_with_overlap: list[str] = []
        for product in products:
            kws = {str(k).lower().strip() for k in (product.get("keywords") or []) if k}
            if kws & bb_keywords:
                products_with_overlap.append(product.get("name", "?"))

        assert len(products_with_overlap) >= 5, (
            f"Only {len(products_with_overlap)} ai_creators products have "
            f"keyword overlap with BB positive_keywords; need >= 5 for the "
            f"seed to actually move the 0% affiliate_cta needle. Overlapping "
            f"products: {products_with_overlap}"
        )


class TestNoDuplicateNames:
    """Pin: each product name is unique inside ai_creators."""

    def test_no_duplicate_product_names(self):
        products = _ai_creators_products()
        names = [p.get("name", "?") for p in products]
        seen: set[str] = set()
        dupes: list[str] = []
        for name in names:
            if name in seen:
                dupes.append(name)
            seen.add(name)
        assert dupes == [], (
            f"Duplicate product names in ai_creators (the matcher returns "
            f"the FIRST keyword winner so dupes silently shadow each "
            f"other): {dupes}"
        )


class TestOperatorSignupContract:
    """Pin: every operator-signup-required product has a discoverable
    ``# signup:`` comment so operators have ONE grep target to find
    what URLs they still need to fill in.

    The contract:
      - If a product has ``enabled: false`` AND its only network entry
        is a placeholder URL (containing ``example.com``), the YAML
        source MUST contain a ``# signup:`` comment line somewhere in
        the product's block.
    """

    def test_placeholder_url_has_signup_comment_nearby(self):
        catalog_text = CATALOG_PATH.read_text()
        ai_section_start = catalog_text.find("ai_creators:")
        # The seed comment block sits ABOVE the products: key; we want
        # the block from products: onward so per-product comments are
        # all that count.
        assert ai_section_start != -1, "ai_creators section missing from catalog"
        ai_block = catalog_text[ai_section_start:]

        # ai_creators sits at end of file in PR AB so the remaining
        # text after the ``ai_creators:`` header IS the ai_creators
        # block; no further slicing needed.

        offenders: list[str] = []
        for product in _ai_creators_products():
            if product.get("enabled", True) is not False:
                continue
            # Is this a placeholder-URL product (Tier A)?
            networks = product.get("networks") or {}
            urls = [info.get("url", "") for info in networks.values()]
            is_placeholder_only = bool(urls) and all("example.com" in u for u in urls)
            if not is_placeholder_only:
                continue

            name = product.get("name", "?")
            # Find this product's block in the YAML source; require
            # a ``# signup:`` comment within ~20 lines after the
            # ``- name: <NAME>`` line.
            marker = f"- name: {name}"
            idx = ai_block.find(marker)
            if idx == -1:
                offenders.append(f"{name} (block not locatable in source)")
                continue
            # Pull the next ~25 lines starting from the product header.
            block_slice = "\n".join(ai_block[idx:].splitlines()[:25])
            if "# signup:" not in block_slice:
                offenders.append(name)

        assert offenders == [], (
            "ai_creators products with placeholder URLs that lack a "
            "'# signup:' operator-instruction comment: "
            f"{offenders}. Each placeholder product MUST have a "
            "'# signup: <url>' comment so operators have a single "
            "grep target to find what URLs they still need to fill in."
        )
