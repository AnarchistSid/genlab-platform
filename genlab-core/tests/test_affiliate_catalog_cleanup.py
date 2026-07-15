"""Pin: affiliate_catalog.yaml is free of zero-value config (2026-06-22 cleanup).

History of the evergreen invariant:

    * 2026-06-22 cleanup — REMOVED all ``evergreen_default: true`` on the
      theory that generic fall-through CTAs caused intent-mismatch
      (Anime Figure for ALL anime reels, Fitness Tracker for ALL sports
      reels, Prime for ALL movies reels).

    * 2026-07-14 prod evidence — 0 blueprints in 7 days matched an
      affiliate product across ALL 5 niches. The 2026-06-22 cleanup was
      empirically wrong: removing every evergreen killed the entire
      monetization capability rather than merely trimming intent-mismatch.
      Commit b0439a42 re-added ONE evergreen per niche (Xbox Game Pass
      for gaming, Sports Watch for sports, Prime Video for movies, Manga
      Subscription Box for anime, Claude Pro for ai_creators). Every
      story that fails specific-keyword matching now gets a curated
      evergreen CTA. Task memory + commit b0439a42 have the full
      per-niche picks + rationale.

Invariants pinned here:

1. EXACTLY ONE evergreen per niche — enough to close the fall-through
   gap without re-creating the intent-mismatch shape the 2026-06-22
   cleanup was fighting. If a niche loses its evergreen, monetization
   silently starves again (the 2026-07-14 shape). If a niche gains a
   second evergreen, the resolver picks non-deterministically among
   them and re-creates the intent-mismatch shape.

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


class TestExactlyOneEvergreenPerNiche:
    """Pin: every niche has EXACTLY ONE evergreen product (2026-07-14).

    Zero evergreens on a niche = the 2026-07-14 monetization outage
    (7 days of 0 affiliate matches, capability #6 structurally dead).
    Two or more evergreens on a niche = resolver picks non-deterministically
    → the intent-mismatch shape the 2026-06-22 cleanup was fighting.
    Exactly one is the operator-tuned floor + ceiling.
    """

    def test_each_niche_has_exactly_one_evergreen_default(self):
        catalog = _load_catalog()
        counts: dict[str, list[str]] = {}
        for niche_id, niche_data in catalog.get("niches", {}).items():
            evergreens = [
                product.get("name", "?")
                for product in niche_data.get("products", [])
                if product.get("evergreen_default") is True
            ]
            counts[niche_id] = evergreens

        errors = []
        for niche_id, evergreens in counts.items():
            if len(evergreens) == 0:
                errors.append(
                    f"{niche_id}: MISSING evergreen — every story that fails "
                    "specific-keyword matching will get no affiliate CTA, "
                    "re-creating the 2026-07-14 monetization outage."
                )
            elif len(evergreens) > 1:
                errors.append(
                    f"{niche_id}: has {len(evergreens)} evergreens ({evergreens}) — "
                    "the resolver will pick non-deterministically, re-creating "
                    "the intent-mismatch shape the 2026-06-22 cleanup fought."
                )
        assert not errors, "\n".join(errors)

    def test_all_5_niches_have_evergreen(self):
        """Belt-and-suspenders: the map-level check catches a niche that's
        entirely missing from the catalog (which would silently skip the
        per-niche count check above)."""
        catalog = _load_catalog()
        for niche_id in ("gaming", "sports", "movies", "anime", "ai_creators"):
            niche_data = catalog.get("niches", {}).get(niche_id, {})
            evergreens = [
                p for p in niche_data.get("products", [])
                if p.get("evergreen_default") is True
            ]
            assert len(evergreens) == 1, (
                f"{niche_id} must have exactly 1 evergreen_default: true "
                f"product; found {len(evergreens)}"
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
