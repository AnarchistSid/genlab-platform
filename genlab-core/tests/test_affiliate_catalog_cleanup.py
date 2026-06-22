"""Pin: affiliate_catalog.yaml is free of zero-value config (2026-06-22 cleanup).

After the 2026-06-22 audit revealed the catalog had compounding bugs
(evergreen defaults causing intent-mismatch CTAs + dead cuelinks
entries that earn ₹0/click), this test enforces the invariants:

1. NO product has ``evergreen_default: true`` — these caused catalog-
   wide fall-through to generic SKUs (Anime Figure for ALL anime
   reels, Fitness Tracker for ALL sports reels, Prime for ALL movies
   reels). Removing evergreen means unmatched stories get no
   affiliate CTA (which is correct — no CTA beats a wrong CTA).

2. NO product carries a ``cuelinks`` network. Per the 2026-06-14
   audit recorded in ``geo_link_resolver.py``, every cuelinks
   redirect earned ₹0 because cuelinks doesn't inject Amazon
   Associates tags. The dashboard's ``_best_network`` was still
   PICKING cuelinks for IN-geo visitors based on commission_pct
   ranking — a separate code path that ignored the audit.

3. ``settings.default_network_priority`` doesn't contain ``cuelinks``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# The runtime ``affiliate_catalog.yaml`` is operator-managed config
# (gitignored). The canonical source for what the catalog should
# look like is the ``.example.yaml`` template, which IS committed.
# These tests validate the EXAMPLE — anyone reintroducing the bug
# patterns to the template surface fails CI immediately. The runtime
# catalog gets the same cleanup applied at deploy time (separate
# SSH-applied edit; see PR description).
CATALOG_PATH = Path(__file__).parent.parent / "config" / "affiliate_catalog.example.yaml"


def _load_catalog() -> dict:
    return yaml.safe_load(CATALOG_PATH.read_text())


class TestNoEvergreenDefaults:
    """Pin: no product has evergreen_default=True after 2026-06-22 cleanup."""

    def test_no_product_has_evergreen_default_true(self):
        catalog = _load_catalog()
        offenders = []
        for niche_id, niche_data in catalog.get("niches", {}).items():
            for product in niche_data.get("products", []):
                if product.get("evergreen_default") is True:
                    offenders.append((niche_id, product.get("name", "?")))
        assert offenders == [], (
            f"evergreen_default: true reintroduced — these products will "
            f"silently fall-through-match ALL unmatched stories in their "
            f"niche, producing intent-mismatch CTAs: {offenders}"
        )

    def test_catalog_source_has_zero_active_evergreen_lines(self):
        """Pin: line-level check on the source file — catches anyone
        adding the flag back via raw YAML edit. (Comments mentioning
        the term in prose are excluded.)"""
        for line in CATALOG_PATH.read_text().splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            assert "evergreen_default" not in stripped, (
                f"reintroduced evergreen_default in line: {line!r}"
            )


class TestNoCuelinks:
    """Pin: catalog is fully cuelinks-free after 2026-06-22 cleanup."""

    def test_no_product_has_cuelinks_network(self):
        catalog = _load_catalog()
        offenders = []
        for niche_id, niche_data in catalog.get("niches", {}).items():
            for product in niche_data.get("products", []):
                networks = product.get("networks", {}) or {}
                if "cuelinks" in networks:
                    offenders.append((niche_id, product.get("name", "?")))
        assert offenders == [], (
            f"cuelinks network reintroduced into products — per 2026-06-14 "
            f"audit, cuelinks earns ₹0/click because it doesn't inject "
            f"Amazon Associates tags. Offenders: {offenders}"
        )

    def test_default_network_priority_excludes_cuelinks(self):
        catalog = _load_catalog()
        priority = catalog.get("settings", {}).get("default_network_priority", [])
        assert "cuelinks" not in priority, (
            f"settings.default_network_priority still includes 'cuelinks' "
            f"({priority}). Per 2026-06-14 audit, cuelinks adds zero value."
        )


class TestProductCount:
    """Pin: the cleanup didn't accidentally delete real products."""

    def test_all_5_niches_present(self):
        catalog = _load_catalog()
        niches = set(catalog.get("niches", {}).keys())
        # The 5 production niches per CLAUDE.md
        expected = {"gaming", "sports", "movies", "anime", "ai_creators"}
        assert expected.issubset(niches), (
            f"Lost niches in cleanup. Have: {niches}, expected: {expected}"
        )

    def test_each_niche_has_products(self):
        catalog = _load_catalog()
        for niche_id, niche_data in catalog.get("niches", {}).items():
            products = niche_data.get("products", [])
            assert len(products) >= 5, (
                f"Niche {niche_id} has only {len(products)} products "
                f"after cleanup — cleanup may have over-removed"
            )
