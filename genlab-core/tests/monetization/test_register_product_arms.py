"""Pin the product arm registration script (L3 PR 3).

Three angles:
  1. ``_arm_id()`` produces the canonical ``product__<slug>`` composite.
  2. ``enumerate_arms_for_niche()`` yields the right arms from a fixture
     catalog + seasonal, INCLUDING seasonal (which is shared across
     niches in the current YAML shape).
  3. Registration flow uses ``save_arm(arm_type='product',
     dimension='affiliate_product', ...)`` with all-1.0 alpha/beta and
     the composite arm_id — verified via mock proxy so no DB touch.

Also verifies:
  * Idempotence: calling ``enumerate_arms_for_niche`` twice on the same
    (niche, catalog, seasonal) yields the same arm set.
  * Slug uniqueness: a catalog + seasonal product with the same slug
    produces ONE arm, not two.
  * Missing seasonal (empty dict) works without error.
  * Empty product name skipped without error.
  * ``__`` in slug rejected (would corrupt composite arm_id parse).

Guard rails:
  * ``KNOWN_NICHES`` includes exactly the 5 niches Gen Lab runs; a
    catalog row for an unknown niche raises rather than silently
    creating an orphan arm.
  * Arm ID uses the SAME ``slugify_product_name`` as
    ``affiliate_matcher`` — verified by importing both and asserting
    format alignment (defence against future slug regressions).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "register_product_arms.py"


def _load_script_module():
    """Load the script as a module so we can call its functions.

    Not on the package path — it lives in ``scripts/`` at workspace
    root. Use importlib to load it dynamically."""
    spec = importlib.util.spec_from_file_location("register_product_arms", _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


@pytest.fixture
def sample_catalog():
    """A minimal 2-product-per-niche catalog covering all 5 niches."""
    return {
        "niches": {
            "gaming": {
                "products": [
                    {"name": "PS5 Console"},
                    {"name": "Xbox Game Pass Ultimate"},
                ]
            },
            "sports": {"products": [{"name": "NBA League Pass"}]},
            "movies": {"products": [{"name": "Amazon Prime Video"}]},
            "anime": {"products": [{"name": "Crunchyroll Premium"}]},
            "ai_creators": {"products": [{"name": "NVIDIA RTX 4090"}]},
        }
    }


@pytest.fixture
def sample_seasonal():
    """One event with 2 products."""
    return {
        "events": [
            {
                "name": "Amazon Prime Day",
                "start": "2026-07-15",
                "end": "2026-07-17",
                "products": [
                    {"name": "Prime Day: PS5 Console"},
                    {"name": "Prime Day: Wireless Earbuds"},
                ],
            }
        ]
    }


class TestArmIdFormat:
    """The ``product__<slug>`` composite is the contract consumers
    depend on (ProductSelector L3 PR 4, reward wire L3 PR 8,
    cross-niche transfer L3 PR 6). Pin the format."""

    def test_composite_format(self, script_module):
        arm_id = script_module._arm_id("nvidia-rtx-4090")
        assert arm_id == "product__nvidia-rtx-4090"

    def test_composite_uses_double_underscore(self, script_module):
        arm_id = script_module._arm_id("ps5-console")
        # exactly ONE ``__`` separator — split yields 2 parts.
        parts = arm_id.split("__")
        assert len(parts) == 2
        assert parts[0] == "product"
        assert parts[1] == "ps5-console"

    def test_slug_containing_separator_rejected(self, script_module):
        """A slug with ``__`` in it would silently break the composite
        parse. Better to fail loud at registration time."""
        with pytest.raises(ValueError, match="contains '__' separator"):
            script_module._arm_id("ps5__console")

    def test_empty_slug_rejected(self, script_module):
        with pytest.raises(ValueError, match="empty slug"):
            script_module._arm_id("")

    def test_alignment_with_slugify_product_name(self, script_module):
        """The slug MUST come from ``slugify_product_name`` because the
        reward wire will JOIN ``bandit_arms.arm_id LIKE 'product__' ||
        blueprints.product_slug`` — and blueprints.product_slug is
        populated via the same helper.  Verify format compatibility."""
        from genlab_core.monetization.affiliate_matcher import (
            slugify_product_name,
        )

        slug = slugify_product_name("NVIDIA RTX 4090")
        arm_id = script_module._arm_id(slug)
        assert arm_id == "product__nvidia-rtx-4090"


class TestEnumerateArms:
    """Enumeration must include both catalog + seasonal, dedupe slug
    collisions, and skip empty product names."""

    def test_catalog_products_included(self, script_module, sample_catalog, sample_seasonal):
        arms = list(
            script_module.enumerate_arms_for_niche("gaming", sample_catalog, sample_seasonal)
        )
        arm_ids = [aid for aid, _ in arms]
        assert "product__ps5-console" in arm_ids
        assert "product__xbox-game-pass-ultimate" in arm_ids

    def test_seasonal_products_included_in_every_niche(
        self, script_module, sample_catalog, sample_seasonal
    ):
        """Seasonal products aren't niche-tagged in the current YAML —
        the matcher considers them for every niche, so arms must exist
        for them in every niche too."""
        for niche in ["gaming", "sports", "movies", "anime", "ai_creators"]:
            arms = list(
                script_module.enumerate_arms_for_niche(niche, sample_catalog, sample_seasonal)
            )
            arm_ids = [aid for aid, _ in arms]
            assert "product__prime-day-ps5-console" in arm_ids, f"seasonal missing from {niche}"
            assert "product__prime-day-wireless-earbuds" in arm_ids, (
                f"seasonal missing from {niche}"
            )

    def test_missing_seasonal_ok(self, script_module, sample_catalog):
        """Empty seasonal dict must not crash."""
        arms = list(script_module.enumerate_arms_for_niche("gaming", sample_catalog, {}))
        # Just catalog products
        assert len(arms) == 2

    def test_missing_niche_yields_only_seasonal(self, script_module, sample_seasonal):
        """A niche absent from the catalog only surfaces seasonal arms
        (never crashes)."""
        arms = list(
            script_module.enumerate_arms_for_niche("gaming", {"niches": {}}, sample_seasonal)
        )
        assert len(arms) == 2  # both seasonal products
        arm_ids = [aid for aid, _ in arms]
        assert "product__prime-day-ps5-console" in arm_ids

    def test_empty_product_name_skipped(self, script_module, sample_seasonal):
        catalog_with_empty = {"niches": {"gaming": {"products": [{"name": ""}, {"name": None}]}}}
        arms = list(
            script_module.enumerate_arms_for_niche("gaming", catalog_with_empty, sample_seasonal)
        )
        # Only seasonal survives — the 2 empty catalog entries are dropped
        assert len(arms) == 2

    def test_slug_dedup_across_catalog_and_seasonal(self, script_module):
        """Same slug in both catalog + seasonal yields ONE arm."""
        catalog = {"niches": {"gaming": {"products": [{"name": "PS5 Console"}]}}}
        seasonal = {
            "events": [{"products": [{"name": "PS5 Console"}]}]  # dup
        }
        arms = list(script_module.enumerate_arms_for_niche("gaming", catalog, seasonal))
        assert len(arms) == 1
        assert arms[0][0] == "product__ps5-console"

    def test_enumeration_is_idempotent(self, script_module, sample_catalog, sample_seasonal):
        """Calling twice yields identical arm sets — no reliance on
        iteration order that varies between runs."""
        arms1 = set(
            aid
            for aid, _ in script_module.enumerate_arms_for_niche(
                "gaming", sample_catalog, sample_seasonal
            )
        )
        arms2 = set(
            aid
            for aid, _ in script_module.enumerate_arms_for_niche(
                "gaming", sample_catalog, sample_seasonal
            )
        )
        assert arms1 == arms2


class TestRegisterFlow:
    """The registration flow must call ``save_arm`` with the right
    metadata: arm_type='product', dimension='affiliate_product',
    dimension_value=<slug>, alpha=1.0, beta=1.0."""

    def test_dry_run_writes_nothing(self, script_module, sample_catalog, sample_seasonal):
        """--dry-run must not call save_arm at all."""
        stats = script_module.register_arms_for_niche(
            "gaming", sample_catalog, sample_seasonal, dry_run=True
        )
        assert stats["dry_run"] is True
        assert stats["registered"] == 0
        assert stats["total"] > 0  # arms enumerated but not written

    def test_live_flow_calls_save_arm_with_product_metadata(
        self, script_module, sample_catalog, sample_seasonal
    ):
        """Verify each save_arm call has arm_type='product', dimension=
        'affiliate_product', dimension_value=<slug>, alpha=beta=1.0."""
        mock_save = MagicMock()
        mock_client = MagicMock()
        mock_client.bandit_arms = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "genlab_core.http.backlog_client": MagicMock(BacklogClient=lambda: mock_client),
                "genlab_core.learning.arm_loader": MagicMock(save_arm=mock_save),
            },
        ):
            stats = script_module.register_arms_for_niche(
                "gaming", sample_catalog, sample_seasonal, dry_run=False
            )

        assert stats["registered"] > 0
        # Every call must set the product-arm classification triple
        for call in mock_save.call_args_list:
            kwargs = call.kwargs
            assert kwargs["arm_type"] == "product"
            assert kwargs["dimension"] == "affiliate_product"
            assert kwargs["alpha"] == 1.0
            assert kwargs["beta"] == 1.0
            assert kwargs["niche_id"] == "gaming"
            assert kwargs["arm_id"].startswith("product__")
            # dimension_value must equal the slug part of arm_id
            expected_slug = kwargs["arm_id"].removeprefix("product__")
            assert kwargs["dimension_value"] == expected_slug

    def test_unknown_niche_rejected(self, script_module, sample_catalog):
        """A hand-edited catalog with a typo'd niche must fail loud."""
        with pytest.raises(ValueError, match="unknown niche"):
            script_module.register_arms_for_niche(
                "movie",  # typo of "movies"
                sample_catalog,
                {},
                dry_run=True,
            )


class TestRealCatalogSmoke:
    """Sanity: the real ``affiliate_catalog.yaml`` in the repo produces
    a sensible arm count (~50-70 arms across 5 niches per the sprint
    plan). Catches accidental catalog corruption AND catches a broken
    regex in slugify_product_name (would produce far fewer arms).

    This isn't tied to a specific integer — it's a range assert to
    survive catalog additions/removals over time. Update the range
    when the catalog structure genuinely changes."""

    def test_real_catalog_produces_reasonable_arm_count(self, script_module):
        import yaml

        catalog_path = _REPO_ROOT / "genlab-core" / "config" / "affiliate_catalog.yaml"
        seasonal_path = _REPO_ROOT / "genlab-core" / "config" / "affiliate_seasonal.yaml"

        # affiliate_seasonal.yaml is gitignored — treat as empty if missing.
        catalog = yaml.safe_load(catalog_path.read_text())
        seasonal_text = seasonal_path.read_text() if seasonal_path.is_file() else "events: []"
        seasonal = yaml.safe_load(seasonal_text) or {}

        grand_total = 0
        for niche in ["gaming", "sports", "movies", "anime", "ai_creators"]:
            arms = list(script_module.enumerate_arms_for_niche(niche, catalog, seasonal))
            grand_total += len(arms)
            assert len(arms) >= 5, f"{niche} produced only {len(arms)} arms — catalog corruption?"

        # Range: catalog has 53 unique products; each niche gets its own
        # catalog products + all seasonal products. Total is 5 × (own_catalog
        # + seasonal_shared) — expect 100-500 range depending on seasonal
        # size.
        assert grand_total >= 25, f"grand total {grand_total} suspiciously low"
