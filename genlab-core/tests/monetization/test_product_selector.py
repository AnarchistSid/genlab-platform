"""Pin the ProductSelector module (L3 PR 4).

Angles:
  1. Flag gate — disabled by default, returns [] when off.
  2. Value weight — log(price × commission + e) formula; missing data floor.
  3. Score routing — Thompson mean below _MIN_PLAYS_FOR_LINUCB, LinUCB above.
  4. Ranking — descending by combined score; ties broken by name.
  5. Fail-open — every I/O path (BacklogClient, arm loader, LinUCB) can fail
     without exception escaping.
  6. Slug alignment — arm_id lookup uses slugify_product_name (defence
     against regression of the load-bearing JOIN key).

Uses mock proxies to avoid DB touch.
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.monetization.product_selector import (
    _MIN_PLAYS_FOR_LINUCB,
    _thompson_score,
    _value_weight,
    is_enabled,
    select_products,
)


class TestFlag:
    """Selector is off by default and only activates on exact match ``"true"``."""

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_PRODUCT_SELECTOR_ENABLED", raising=False)
        assert is_enabled() is False

    def test_disabled_on_common_falsy_values(self, monkeypatch):
        for val in ("0", "false", "False", "no", ""):
            monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", val)
            assert is_enabled() is False, f"expected off for {val!r}"

    def test_enabled_only_on_lowercase_true(self, monkeypatch):
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")
        assert is_enabled() is True

    def test_disabled_on_uppercase_true(self, monkeypatch):
        """Sprint convention (Intervention 6) uses exact-match lowercase.
        A ``"True"`` would silently disable — pin the strictness."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "True")
        assert is_enabled() is False

    def test_select_returns_empty_when_disabled(self, monkeypatch):
        monkeypatch.delenv("GENLAB_PRODUCT_SELECTOR_ENABLED", raising=False)
        result = select_products(
            "gaming",
            [{"name": "PS5 Console", "price_inr": 49990}],
        )
        assert result == []


class TestValueWeight:
    """The revenue-scaling multiplier — must handle missing data
    gracefully and never dominate rankings via runaway growth."""

    def test_missing_price_returns_floor(self):
        product = {"name": "X", "networks": {"amazon": {"commission_pct": 3.0}}}
        assert _value_weight(product) == 1.0

    def test_missing_commission_returns_floor(self):
        product = {"name": "X", "price_inr": 1000, "networks": {}}
        assert _value_weight(product) == 1.0

    def test_missing_both_returns_floor(self):
        assert _value_weight({"name": "X"}) == 1.0

    def test_priced_product_scales_above_floor(self):
        """A priced product with real commission must weight above the
        floor. Value: 49990 × 3.0 / 100 = 1499.7 → log(1499.7 + e) ≈ 7.31."""
        product = {
            "name": "PS5",
            "price_inr": 49990,
            "networks": {"amazon": {"commission_pct": 3.0}},
        }
        w = _value_weight(product)
        assert w > 1.0
        assert w == pytest.approx(math.log(1499.7 + math.e), rel=1e-3)

    def test_log_compression_limits_dominance(self):
        """Product 100× more valuable shouldn't get 100× the weight —
        log compression must be meaningful. 100× revenue → ~5× weight."""
        cheap = {
            "name": "cheap",
            "price_inr": 100,
            "networks": {"amazon": {"commission_pct": 5.0}},
        }
        expensive = {
            "name": "expensive",
            "price_inr": 10000,
            "networks": {"amazon": {"commission_pct": 5.0}},
        }
        ratio = _value_weight(expensive) / _value_weight(cheap)
        assert 2 < ratio < 20, f"log dominance ratio {ratio} out of bounds"

    def test_best_commission_selected_across_networks(self):
        """Product with EarnKaro 12% + Amazon 3% uses the max."""
        product = {
            "name": "X",
            "price_inr": 1000,
            "networks": {
                "amazon": {"commission_pct": 3.0},
                "earnkaro": {"commission_pct": 12.0},
            },
        }
        # Uses 12%: 1000 × 0.12 = 120 → log(120 + e) ≈ 4.81
        w = _value_weight(product)
        assert w == pytest.approx(math.log(120 + math.e), rel=1e-3)

    def test_zero_commission_returns_floor(self):
        product = {
            "name": "X",
            "price_inr": 1000,
            "networks": {"amazon": {"commission_pct": 0}},
        }
        assert _value_weight(product) == 1.0


class TestThompsonScore:
    """Posterior mean of Beta(α, β) — deterministic, bounded [0,1]."""

    def test_uniform_prior(self):
        assert _thompson_score(1.0, 1.0) == 0.5

    def test_biased_high(self):
        # α=9, β=1 → 9/10 = 0.9
        assert _thompson_score(9.0, 1.0) == pytest.approx(0.9)

    def test_biased_low(self):
        # α=1, β=9 → 1/10 = 0.1
        assert _thompson_score(1.0, 9.0) == pytest.approx(0.1)

    def test_invalid_returns_uniform_fallback(self):
        """Invalid posterior (zero or negative) → 0.5 (uniform prior)."""
        assert _thompson_score(0, 0) == 0.5
        assert _thompson_score(-1, 5) == 0.5

    def test_deterministic_repeatable(self):
        """Posterior mean must be the SAME on repeated calls — a random
        sample would flake the ranking API. Pin this contract."""
        r1 = _thompson_score(3.0, 7.0)
        r2 = _thompson_score(3.0, 7.0)
        assert r1 == r2


class TestSelectProducts:
    """End-to-end ranking behavior with mocked DB."""

    @pytest.fixture(autouse=True)
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")

    def _install_arm_loader(self, monkeypatch, arms_by_id):
        """Patch load_all_arms_extended to return the fixture arms."""
        # Provide a fake BacklogClient so the code doesn't try real DB.
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        monkeypatch.setattr(
            "genlab_core.http.backlog_client.BacklogClient",
            lambda: fake_client,
        )
        monkeypatch.setattr(
            "genlab_core.learning.arm_loader.load_all_arms_extended",
            lambda proxy, niche_id: arms_by_id,
        )

    def test_no_candidates_returns_empty(self):
        assert select_products("gaming", []) == []

    def test_single_candidate_returns_one(self, monkeypatch):
        self._install_arm_loader(monkeypatch, {})
        candidates = [{"name": "PS5 Console", "price_inr": 49990}]
        result = select_products("gaming", candidates, top_k=3)
        assert len(result) == 1
        product, score = result[0]
        assert product["name"] == "PS5 Console"
        assert score > 0

    def test_top_k_respected(self, monkeypatch):
        self._install_arm_loader(monkeypatch, {})
        candidates = [{"name": f"Product {i}", "price_inr": 100 * (i + 1)} for i in range(5)]
        result = select_products("gaming", candidates, top_k=2)
        assert len(result) == 2

    def test_ranking_descending(self, monkeypatch):
        self._install_arm_loader(monkeypatch, {})
        candidates = [
            {
                "name": "cheap",
                "price_inr": 100,
                "networks": {"a": {"commission_pct": 3.0}},
            },
            {
                "name": "expensive",
                "price_inr": 10000,
                "networks": {"a": {"commission_pct": 3.0}},
            },
        ]
        result = select_products("gaming", candidates, top_k=2)
        # Expensive should rank higher via value multiplier
        assert result[0][0]["name"] == "expensive"
        assert result[0][1] > result[1][1]

    def test_arm_boosts_score(self, monkeypatch):
        """A product with α=9, β=1 posterior should outrank one with
        α=1, β=1 for equal value weight."""
        arms = {
            "product__winner": {
                "alpha": 9.0,
                "beta": 1.0,
                "n_plays": 8,  # below LinUCB threshold
                "linucb_state": None,
            },
            "product__loser": {
                "alpha": 1.0,
                "beta": 9.0,
                "n_plays": 8,
                "linucb_state": None,
            },
        }
        self._install_arm_loader(monkeypatch, arms)
        candidates = [
            {"name": "winner", "price_inr": 100},
            {"name": "loser", "price_inr": 100},
        ]
        result = select_products("gaming", candidates, top_k=2)
        assert result[0][0]["name"] == "winner"

    def test_missing_arm_uses_uniform_prior(self, monkeypatch):
        """A candidate with no registered arm should score as if α=β=1
        rather than being silently dropped."""
        self._install_arm_loader(monkeypatch, {})
        candidates = [{"name": "brand new product", "price_inr": 1000}]
        result = select_products("gaming", candidates, top_k=1)
        assert len(result) == 1

    def test_empty_name_skipped(self, monkeypatch):
        self._install_arm_loader(monkeypatch, {})
        candidates = [
            {"name": "", "price_inr": 100},
            {"name": None, "price_inr": 200},
            {"name": "real", "price_inr": 300},
        ]
        result = select_products("gaming", candidates, top_k=3)
        assert len(result) == 1
        assert result[0][0]["name"] == "real"


class TestFailOpen:
    """Every I/O + import failure path returns [] — no exception ever
    escapes to the caller. Selector must be safe to add anywhere."""

    @pytest.fixture(autouse=True)
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")

    def test_import_error_returns_empty(self, monkeypatch):
        """A degraded environment (no genlab_core installed) must not
        crash the caller."""
        import sys

        # Simulate ImportError by nuking the modules from sys.modules cache
        # and injecting a raising module.
        broken = MagicMock()
        broken.__spec__ = None
        with patch.dict(
            sys.modules,
            {"genlab_core.http.backlog_client": None},
        ):
            # patch.dict with None won't trigger ImportError on `from ... import`;
            # instead, mock the BacklogClient to raise.
            with patch(
                "genlab_core.http.backlog_client.BacklogClient",
                side_effect=RuntimeError("db unreachable"),
            ):
                result = select_products("gaming", [{"name": "PS5"}])
                assert result == []

    def test_arm_loader_exception_returns_empty(self, monkeypatch):
        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        monkeypatch.setattr(
            "genlab_core.http.backlog_client.BacklogClient",
            lambda: fake_client,
        )
        monkeypatch.setattr(
            "genlab_core.learning.arm_loader.load_all_arms_extended",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        result = select_products("gaming", [{"name": "PS5"}])
        assert result == []

    def test_bandit_arms_proxy_none_returns_empty(self, monkeypatch):
        fake_client = MagicMock()
        fake_client.bandit_arms = None
        monkeypatch.setattr(
            "genlab_core.http.backlog_client.BacklogClient",
            lambda: fake_client,
        )
        result = select_products("gaming", [{"name": "PS5"}])
        assert result == []


class TestSlugAlignment:
    """The arm_id lookup MUST use slugify_product_name — a drift here
    would make the JOIN back from clicks impossible."""

    def test_arm_lookup_uses_canonical_slug(self, monkeypatch):
        """A product named "NVIDIA RTX 4090" must look up arm
        ``product__nvidia-rtx-4090`` (matches L3 PR 3's registration)."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")

        fake_client = MagicMock()
        fake_client.bandit_arms = MagicMock()
        monkeypatch.setattr(
            "genlab_core.http.backlog_client.BacklogClient",
            lambda: fake_client,
        )
        # Arm registered under the canonical slug
        arms = {
            "product__nvidia-rtx-4090": {
                "alpha": 9.0,
                "beta": 1.0,
                "n_plays": 5,
                "linucb_state": None,
            }
        }
        monkeypatch.setattr(
            "genlab_core.learning.arm_loader.load_all_arms_extended",
            lambda proxy, niche_id: arms,
        )

        result = select_products(
            "gaming",
            [{"name": "NVIDIA RTX 4090", "price_inr": 100}],
        )
        assert len(result) == 1
        # The α=9,β=1 posterior mean (0.9) must beat the default 0.5 —
        # confirms the arm was actually looked up under the slug.
        # value_weight = log(3+e) for price=100 comm=3% (falls to floor 1.0
        # if commission missing), so posterior dominates the score.
        _, score = result[0]
        assert score > 0.5


class TestWarmStartThreshold:
    """Route: n_plays < 10 → Thompson; n_plays ≥ 10 → try LinUCB.
    Ensure the threshold constant hasn't drifted."""

    def test_threshold_constant(self):
        assert _MIN_PLAYS_FOR_LINUCB == 10
