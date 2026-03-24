"""Tests for genlab_core.monetization.revenue_predictor."""
from __future__ import annotations

import pytest

from genlab_core.monetization.revenue_predictor import RevenuePredictor, _estimate_price_by_category


def _base_story(**overrides: object) -> dict:
    story = {
        "affiliate_product": "Test Headset",
        "affiliate_commission_pct": 4.0,
        "_affiliate_price_inr": 5000,
        "_affiliate_match_quality": 0.5,
    }
    story.update(overrides)
    return story


class TestPredictWithAffiliateMatch:
    def test_predict_with_affiliate_match(self) -> None:
        predictor = RevenuePredictor()
        story = _base_story()
        revenue = predictor.predict(story)
        assert revenue > 0.0

    def test_predict_formula(self) -> None:
        """Verify exact formula output."""
        predictor = RevenuePredictor(conversion_rate=0.02)
        story = _base_story(
            affiliate_commission_pct=4.0,
            _affiliate_price_inr=5000,
            _affiliate_match_quality=0.5,
        )
        # clicks = 5 * (0.5 + 0.5) = 5.0
        # revenue = 5.0 * 0.02 * 5000 * 0.04 = 20.0
        assert predictor.predict(story) == pytest.approx(20.0)


class TestPredictNoAffiliate:
    def test_predict_no_affiliate(self) -> None:
        predictor = RevenuePredictor()
        story = {"title": "Some story with no affiliate"}
        assert predictor.predict(story) == 0.0

    def test_predict_empty_product_name(self) -> None:
        predictor = RevenuePredictor()
        story = _base_story(affiliate_product="")
        assert predictor.predict(story) == 0.0


class TestPredictHighCommission:
    def test_predict_high_commission(self) -> None:
        predictor = RevenuePredictor()
        low = predictor.predict(_base_story(affiliate_commission_pct=2.0))
        high = predictor.predict(_base_story(affiliate_commission_pct=10.0))
        assert high > low

    def test_predict_commission_proportional(self) -> None:
        predictor = RevenuePredictor()
        rev_2pct = predictor.predict(_base_story(affiliate_commission_pct=2.0))
        rev_4pct = predictor.predict(_base_story(affiliate_commission_pct=4.0))
        assert rev_4pct == pytest.approx(rev_2pct * 2.0)


class TestPredictHighPrice:
    def test_predict_high_price(self) -> None:
        predictor = RevenuePredictor()
        low = predictor.predict(_base_story(_affiliate_price_inr=1000))
        high = predictor.predict(_base_story(_affiliate_price_inr=20000))
        assert high > low

    def test_predict_price_proportional(self) -> None:
        predictor = RevenuePredictor()
        rev_1k = predictor.predict(_base_story(_affiliate_price_inr=1000))
        rev_2k = predictor.predict(_base_story(_affiliate_price_inr=2000))
        assert rev_2k == pytest.approx(rev_1k * 2.0)


class TestPredictHighMatchQuality:
    def test_predict_high_match_quality(self) -> None:
        predictor = RevenuePredictor()
        low = predictor.predict(_base_story(_affiliate_match_quality=0.0))
        high = predictor.predict(_base_story(_affiliate_match_quality=1.0))
        assert high > low

    def test_predict_match_quality_affects_clicks(self) -> None:
        """match_quality=0.0 → clicks=2.5; match_quality=1.0 → clicks=7.5 (3× ratio)."""
        predictor = RevenuePredictor()
        rev_low = predictor.predict(_base_story(_affiliate_match_quality=0.0))
        rev_high = predictor.predict(_base_story(_affiliate_match_quality=1.0))
        assert rev_high == pytest.approx(rev_low * 3.0)


class TestCommissionPercentageConversion:
    def test_commission_percentage_conversion(self) -> None:
        """4.0 stored as percent should be treated as 4%, not 400%."""
        predictor = RevenuePredictor(conversion_rate=0.02)
        story = _base_story(affiliate_commission_pct=4.0, _affiliate_price_inr=5000, _affiliate_match_quality=0.5)
        revenue = predictor.predict(story)
        # Expected: 5 * 1.0 * 0.02 * 5000 * 0.04 = 20.0
        # Wrong (400%): 5 * 1.0 * 0.02 * 5000 * 4.0 = 2000.0
        assert revenue == pytest.approx(20.0)
        assert revenue < 100.0  # Sanity: must not be the 400% value

    def test_commission_already_decimal_unchanged(self) -> None:
        """Values ≤ 1.0 are treated as already-decimal — not divided by 100."""
        predictor = RevenuePredictor(conversion_rate=0.02)
        story_decimal = _base_story(affiliate_commission_pct=0.04)
        story_percent = _base_story(affiliate_commission_pct=4.0)
        assert predictor.predict(story_decimal) == pytest.approx(predictor.predict(story_percent))

    def test_commission_string_parsed(self) -> None:
        """String commission values are parsed to float."""
        predictor = RevenuePredictor()
        story = _base_story(affiliate_commission_pct="4.0")
        assert predictor.predict(story) > 0.0

    def test_commission_bad_string_defaults_zero(self) -> None:
        """Unparseable commission string returns 0.0."""
        predictor = RevenuePredictor()
        story = _base_story(affiliate_commission_pct="n/a")
        assert predictor.predict(story) == 0.0


class TestScoreStoriesAddsField:
    def test_score_stories_adds_field(self) -> None:
        predictor = RevenuePredictor()
        stories = [_base_story(), _base_story(affiliate_product="")]
        result = predictor.score_stories(stories)
        assert result is stories  # same list returned
        assert "affiliate_predicted_revenue" in stories[0]
        assert "affiliate_predicted_revenue" in stories[1]

    def test_score_stories_values_correct(self) -> None:
        predictor = RevenuePredictor()
        stories = [_base_story(), _base_story(affiliate_product="")]
        predictor.score_stories(stories)
        assert stories[0]["affiliate_predicted_revenue"] > 0.0
        assert stories[1]["affiliate_predicted_revenue"] == 0.0

    def test_score_stories_empty_list(self) -> None:
        predictor = RevenuePredictor()
        result = predictor.score_stories([])
        assert result == []


class TestCategoryPriceEstimation:
    def test_hardware_estimated_higher_than_subscription(self) -> None:
        assert _estimate_price_by_category("hardware") > _estimate_price_by_category("subscription")

    def test_hardware_price(self) -> None:
        assert _estimate_price_by_category("hardware") == 15000

    def test_subscription_price(self) -> None:
        assert _estimate_price_by_category("subscription") == 1000

    def test_unknown_category_default(self) -> None:
        assert _estimate_price_by_category("unknown_category") == 3000

    def test_all_known_categories_positive(self) -> None:
        categories = ["hardware", "peripheral", "subscription", "gear", "merchandise", "tool", "educational", "supplement"]
        for cat in categories:
            assert _estimate_price_by_category(cat) > 0

    def test_category_used_when_price_missing(self) -> None:
        """When _affiliate_price_inr is absent, category fallback is used."""
        predictor = RevenuePredictor()
        story_hw = {
            "affiliate_product": "Some GPU",
            "affiliate_commission_pct": 4.0,
            "_affiliate_category": "hardware",
            "_affiliate_match_quality": 0.5,
        }
        story_sub = {
            "affiliate_product": "Some SaaS",
            "affiliate_commission_pct": 4.0,
            "_affiliate_category": "subscription",
            "_affiliate_match_quality": 0.5,
        }
        assert predictor.predict(story_hw) > predictor.predict(story_sub)
