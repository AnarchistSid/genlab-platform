"""Tests for :mod:`genlab_core.monetization.affiliate_economics`.

Audit ref: R-32.
"""

from __future__ import annotations

import pytest
from genlab_core.monetization import affiliate_economics


@pytest.fixture(autouse=True)
def _reset_cache():
    """Drop the lru_cache between tests so monkeypatched paths take effect."""
    affiliate_economics.reset_cache()
    yield
    affiliate_economics.reset_cache()


# ---------------------------------------------------------------------------
# AffiliateEconomics — pure-logic methods (no YAML I/O)
# ---------------------------------------------------------------------------


class TestEconomicsModel:
    def test_avg_order_for_known_niche(self) -> None:
        model = affiliate_economics.AffiliateEconomics(
            avg_order_inr={"gaming": 17000.0},
            default_avg_order_inr=10000.0,
        )
        assert model.avg_order_for("gaming") == 17000.0

    def test_avg_order_falls_back_to_default(self) -> None:
        model = affiliate_economics.AffiliateEconomics(
            avg_order_inr={"gaming": 17000.0},
            default_avg_order_inr=10000.0,
        )
        assert model.avg_order_for("brand_new_niche") == 10000.0

    def test_commission_for_known_network(self) -> None:
        model = affiliate_economics.AffiliateEconomics(
            commission_pct={"amazon": 0.04},
            default_commission_pct=0.05,
        )
        assert model.commission_for("amazon") == 0.04

    def test_commission_falls_back_to_default(self) -> None:
        model = affiliate_economics.AffiliateEconomics(
            commission_pct={"amazon": 0.04},
            default_commission_pct=0.05,
        )
        assert model.commission_for("unknown_network") == 0.05

    def test_estimate_revenue_uses_canonical_formula(self) -> None:
        model = affiliate_economics.AffiliateEconomics(
            avg_order_inr={"gaming": 10000.0},
            commission_pct={"amazon": 0.05},
            conversion_rate=0.02,
        )
        # 100 clicks × 0.02 conv = 2 conversions
        # 2 conversions × 10000 INR avg_order = 20,000 INR gross
        # 20,000 × 0.05 commission = 1000 INR
        assert model.estimate_revenue(niche_id="gaming", network="amazon", clicks=100) == 1000.0

    def test_estimate_revenue_with_zero_clicks(self) -> None:
        model = affiliate_economics.AffiliateEconomics()
        assert model.estimate_revenue(niche_id="gaming", network="amazon", clicks=0) == 0.0

    def test_estimate_revenue_uses_fallbacks(self) -> None:
        # Both niche and network unknown — must still produce a number.
        model = affiliate_economics.AffiliateEconomics(
            default_avg_order_inr=10000.0,
            default_commission_pct=0.05,
            conversion_rate=0.02,
        )
        # 100 × 0.02 × 10000 × 0.05 = 1000.0
        assert (
            model.estimate_revenue(niche_id="unknown_niche", network="unknown_net", clicks=100)
            == 1000.0
        )


# ---------------------------------------------------------------------------
# get_affiliate_economics — YAML loading + caching
# ---------------------------------------------------------------------------


class TestLoader:
    def test_returns_defaults_when_yaml_missing(self, tmp_path, monkeypatch) -> None:
        # Point _config_path at a non-existent file.
        monkeypatch.setattr(
            affiliate_economics,
            "_config_path",
            lambda: tmp_path / "missing.yaml",
        )
        model = affiliate_economics.get_affiliate_economics()
        assert isinstance(model, affiliate_economics.AffiliateEconomics)
        # Defaults from the model field declarations.
        assert model.default_avg_order_inr == 11640.0
        assert model.default_commission_pct == 0.05
        assert model.conversion_rate == 0.02

    def test_loads_yaml_when_present(self, tmp_path, monkeypatch) -> None:
        yaml_file = tmp_path / "affiliate_economics.yaml"
        yaml_file.write_text(
            """
avg_order_inr:
  gaming: 99999.0
default_avg_order_inr: 50000.0
commission_pct:
  amazon: 0.10
default_commission_pct: 0.08
conversion_rate: 0.05
default_currency: USD
"""
        )
        monkeypatch.setattr(affiliate_economics, "_config_path", lambda: yaml_file)
        model = affiliate_economics.get_affiliate_economics()
        assert model.avg_order_for("gaming") == 99999.0
        assert model.default_avg_order_inr == 50000.0
        assert model.commission_for("amazon") == 0.10
        assert model.default_commission_pct == 0.08
        assert model.conversion_rate == 0.05
        assert model.default_currency == "USD"

    def test_loader_is_cached(self, tmp_path, monkeypatch) -> None:
        yaml_file = tmp_path / "affiliate_economics.yaml"
        yaml_file.write_text("default_avg_order_inr: 1.0\n")
        monkeypatch.setattr(affiliate_economics, "_config_path", lambda: yaml_file)
        m1 = affiliate_economics.get_affiliate_economics()
        # Mutate the file — m2 should still be m1 thanks to lru_cache.
        yaml_file.write_text("default_avg_order_inr: 999.0\n")
        m2 = affiliate_economics.get_affiliate_economics()
        assert m1 is m2

    def test_loader_returns_defaults_on_malformed_yaml(self, tmp_path, monkeypatch) -> None:
        yaml_file = tmp_path / "broken.yaml"
        yaml_file.write_text("default_avg_order_inr: [this is a list]\n")
        monkeypatch.setattr(affiliate_economics, "_config_path", lambda: yaml_file)
        # Pydantic raises on type-mismatch → loader catches → defaults.
        model = affiliate_economics.get_affiliate_economics()
        assert model.default_avg_order_inr == 11640.0


# ---------------------------------------------------------------------------
# Real config sanity — protects against drift in the bundled YAML.
# ---------------------------------------------------------------------------


class TestBundledConfig:
    def test_bundled_yaml_loads_without_error(self) -> None:
        """The real ``genlab-core/config/affiliate_economics.yaml`` parses
        cleanly and produces sensible values."""
        model = affiliate_economics.get_affiliate_economics()
        # All 5 niches present.
        for niche in ("gaming", "sports", "movies", "anime", "ai_creators"):
            assert niche in model.avg_order_inr
        # Conservative sanity: every commission rate is between 1% and 20%.
        for network, pct in model.commission_pct.items():
            assert 0.01 <= pct <= 0.20, f"{network}: {pct}"
        assert 0.0 < model.conversion_rate < 0.10
        assert model.default_currency == "INR"

    def test_bundled_path_resolves_to_repo_config(self) -> None:
        """_config_path() lands on the bundled YAML, not somewhere weird."""
        p = affiliate_economics._config_path()
        assert p.name == "affiliate_economics.yaml"
        assert p.parent.name == "config"
        assert p.exists(), f"bundled YAML should exist at {p}"
