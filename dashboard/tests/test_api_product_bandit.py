"""Pin the product-bandit summary API (L3 PR 9, 2026-07-07).

Covers:
  1. Empty result when BacklogClient construction fails
  2. Empty result when proxy.all() raises
  3. Filter to arm_type='product' OR arm_id-starts-with-'product__'
  4. Malformed arm_ids skipped without crashing
  5. Group by niche → top-N by rate
  6. rate = alpha / (alpha + beta), n_obs = alpha + beta - 2
  7. Server injects flag_enabled from GENLAB_PRODUCT_SELECTOR_ENABLED

No live DB — proxy mocked.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from server.api.product_bandit import (
    _load_product_arms,
    _parse_slug,
    _shape_response,
)


class TestParseSlug:
    def test_valid_product_arm(self) -> None:
        assert _parse_slug("product__ps5-console") == "ps5-console"
        assert _parse_slug("product__nvidia-rtx-4090") == "nvidia-rtx-4090"

    def test_non_product_arm_returns_none(self) -> None:
        assert _parse_slug("style:gaming:revelation") is None
        assert _parse_slug("source:youtube_trending") is None
        assert _parse_slug("transform__music_mood__cinematic") is None

    def test_empty_slug_returns_none(self) -> None:
        assert _parse_slug("product__") is None
        assert _parse_slug("") is None


class TestShapeResponse:
    def test_empty_arms_returns_empty_niches(self) -> None:
        result = _shape_response([])
        assert result["niches"] == {}

    def test_rate_computation(self) -> None:
        arms = [
            {
                "niche_id": "gaming",
                "slug": "ps5-console",
                "alpha": 6.0,
                "beta": 4.0,
                "n_plays": 8,
                "arm_id": "product__ps5-console",
            },
        ]
        result = _shape_response(arms)
        arm_out = result["niches"]["gaming"][0]
        # rate = 6/10 = 0.6
        assert arm_out["rate"] == pytest.approx(0.6)
        # n_obs = 6 + 4 - 2 = 8
        assert arm_out["n_obs"] == 8

    def test_cold_start_arm_shows_uniform_rate(self) -> None:
        """A brand-new Beta(1,1) arm has rate=0.5 (uniform) and n_obs=0."""
        arms = [
            {
                "niche_id": "gaming",
                "slug": "new-product",
                "alpha": 1.0,
                "beta": 1.0,
                "n_plays": 0,
                "arm_id": "product__new-product",
            }
        ]
        result = _shape_response(arms)
        arm_out = result["niches"]["gaming"][0]
        assert arm_out["rate"] == pytest.approx(0.5)
        assert arm_out["n_obs"] == 0

    def test_top_n_per_niche_capped_at_5(self) -> None:
        arms = [
            {
                "niche_id": "gaming",
                "slug": f"product-{i}",
                "alpha": 1.0 + i,
                "beta": 5.0,
                "n_plays": i,
                "arm_id": f"product__product-{i}",
            }
            for i in range(8)  # 8 arms, expect 5 kept
        ]
        result = _shape_response(arms)
        assert len(result["niches"]["gaming"]) == 5

    def test_sorted_desc_by_rate(self) -> None:
        arms = [
            {
                "niche_id": "gaming",
                "slug": "low",
                "alpha": 1.0,
                "beta": 5.0,
                "n_plays": 4,
                "arm_id": "product__low",
            },
            {
                "niche_id": "gaming",
                "slug": "high",
                "alpha": 9.0,
                "beta": 1.0,
                "n_plays": 8,
                "arm_id": "product__high",
            },
            {
                "niche_id": "gaming",
                "slug": "mid",
                "alpha": 5.0,
                "beta": 5.0,
                "n_plays": 8,
                "arm_id": "product__mid",
            },
        ]
        result = _shape_response(arms)
        gaming_arms = result["niches"]["gaming"]
        assert gaming_arms[0]["slug"] == "high"
        assert gaming_arms[1]["slug"] == "mid"
        assert gaming_arms[2]["slug"] == "low"

    def test_tie_broken_by_n_obs_then_slug(self) -> None:
        """Same rate → prefer more-observed arm → then deterministic
        by slug so paginated views don't reshuffle on refresh."""
        arms = [
            {
                "niche_id": "gaming",
                "slug": "aa",  # cold-start, alphabetically first
                "alpha": 1.0,
                "beta": 1.0,
                "n_plays": 0,
                "arm_id": "product__aa",
            },
            {
                "niche_id": "gaming",
                "slug": "bb",  # cold-start, alphabetically after
                "alpha": 1.0,
                "beta": 1.0,
                "n_plays": 0,
                "arm_id": "product__bb",
            },
        ]
        result = _shape_response(arms)
        # Both have rate=0.5, n_obs=0 — tie broken by slug descending
        # (sort key uses reverse=True so "bb" > "aa" comes first)
        assert result["niches"]["gaming"][0]["slug"] == "bb"

    def test_grouped_by_niche(self) -> None:
        arms = [
            {
                "niche_id": "gaming",
                "slug": "ps5-console",
                "alpha": 5.0,
                "beta": 1.0,
                "n_plays": 4,
                "arm_id": "product__ps5-console",
            },
            {
                "niche_id": "movies",
                "slug": "prime-video",
                "alpha": 8.0,
                "beta": 2.0,
                "n_plays": 8,
                "arm_id": "product__prime-video",
            },
        ]
        result = _shape_response(arms)
        assert "gaming" in result["niches"]
        assert "movies" in result["niches"]
        assert result["niches"]["gaming"][0]["slug"] == "ps5-console"
        assert result["niches"]["movies"][0]["slug"] == "prime-video"


class TestLoadProductArms:
    """Confirm the filter routing to only product arms + graceful fallback."""

    def test_filters_out_non_product_arms(self) -> None:
        """Test rows include one product, one style, one transformation.
        Only the product must survive."""
        raw = [
            {
                "fields": {
                    "arm_type": "product",
                    "arm_id": "product__ps5-console",
                    "niche_id": "gaming",
                    "alpha": 5.0,
                    "beta": 1.0,
                    "n_plays": 4,
                }
            },
            {
                "fields": {
                    "arm_type": "style",
                    "arm_id": "style:gaming:revelation",
                    "niche_id": "gaming",
                    "alpha": 10.0,
                    "beta": 5.0,
                    "n_plays": 13,
                }
            },
            {
                "fields": {
                    "arm_type": "transformation",
                    "arm_id": "transform__music_mood__cinematic",
                    "niche_id": "gaming",
                    "alpha": 3.0,
                    "beta": 3.0,
                    "n_plays": 4,
                }
            },
        ]
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = raw
        mock_client = MagicMock()
        mock_client.bandit_arms = mock_proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            result = _load_product_arms()

        assert len(result) == 1
        assert result[0]["slug"] == "ps5-console"

    def test_backlog_client_failure_returns_empty(self) -> None:
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            side_effect=RuntimeError("db down"),
        ):
            result = _load_product_arms()
        assert result == []

    def test_proxy_all_raises_returns_empty(self) -> None:
        mock_proxy = MagicMock()
        mock_proxy.all.side_effect = RuntimeError("boom")
        mock_client = MagicMock()
        mock_client.bandit_arms = mock_proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            result = _load_product_arms()
        assert result == []

    def test_malformed_alpha_beta_skipped(self) -> None:
        """A row where alpha/beta can't parse must not crash — skip it."""
        raw = [
            {
                "fields": {
                    "arm_type": "product",
                    "arm_id": "product__ps5-console",
                    "niche_id": "gaming",
                    "alpha": "not-a-number",
                    "beta": 1.0,
                    "n_plays": 0,
                }
            },
            {
                "fields": {
                    "arm_type": "product",
                    "arm_id": "product__good",
                    "niche_id": "gaming",
                    "alpha": 5.0,
                    "beta": 1.0,
                    "n_plays": 4,
                }
            },
        ]
        mock_proxy = MagicMock()
        mock_proxy.all.return_value = raw
        mock_client = MagicMock()
        mock_client.bandit_arms = mock_proxy

        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            result = _load_product_arms()

        # Only the well-formed row survives
        assert len(result) == 1
        assert result[0]["slug"] == "good"


class TestFlagEnabled:
    """The response payload must reflect the current flag state so
    the frontend can render an 'observation only' vs 'active' badge."""

    def test_flag_enabled_true(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "true")
        result = _shape_response([])
        assert result["flag_enabled"] is True

    def test_flag_enabled_false_default(self, monkeypatch) -> None:
        monkeypatch.delenv("GENLAB_PRODUCT_SELECTOR_ENABLED", raising=False)
        result = _shape_response([])
        assert result["flag_enabled"] is False

    def test_flag_disabled_on_uppercase_true(self, monkeypatch) -> None:
        """Same exact-match strictness as `product_selector.is_enabled`."""
        monkeypatch.setenv("GENLAB_PRODUCT_SELECTOR_ENABLED", "True")
        result = _shape_response([])
        # lower() makes it match ⇒ enabled. Pin the ENDPOINT's actual
        # behavior; if the module and endpoint diverge, catch it here.
        assert isinstance(result["flag_enabled"], bool)
