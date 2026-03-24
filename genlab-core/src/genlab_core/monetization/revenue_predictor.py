"""Revenue prediction for affiliate-matched posts.

Estimates potential affiliate revenue before publishing using a simple
heuristic. When sufficient click data accumulates (100+ attributed clicks),
can be replaced with a trained model.

Usage:
    predictor = RevenuePredictor()
    score = predictor.predict(story)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CONVERSION_RATE = 0.02
_DEFAULT_AVG_CLICKS_PER_POST = 5


class RevenuePredictor:
    """Predict affiliate revenue for a story using heuristic scoring."""

    def __init__(self, conversion_rate: float = _DEFAULT_CONVERSION_RATE) -> None:
        self._conversion_rate = conversion_rate

    def predict(self, story: dict[str, Any]) -> float:
        """Predict revenue for a story with affiliate match.

        Returns estimated revenue in INR. Returns 0.0 if no affiliate match.

        Formula:
            revenue = avg_clicks × conversion_rate × price × commission_pct

        Boosted by match_quality (keyword hit ratio).
        """
        product_name = story.get("affiliate_product", "")
        if not product_name:
            return 0.0

        commission_pct = story.get("affiliate_commission_pct", 0.0)
        if isinstance(commission_pct, str):
            try:
                commission_pct = float(commission_pct)
            except (ValueError, TypeError):
                commission_pct = 0.0

        # Commission is stored as percentage (e.g., 4.0 for 4%), convert to decimal
        if commission_pct >= 1.0:
            commission_pct = commission_pct / 100.0

        price_inr = story.get("_affiliate_price_inr", 0)
        if not price_inr:
            price_inr = _estimate_price_by_category(story.get("_affiliate_category", ""))

        match_quality = story.get("_affiliate_match_quality", 0.5)

        estimated_clicks = _DEFAULT_AVG_CLICKS_PER_POST * (0.5 + match_quality)
        revenue = estimated_clicks * self._conversion_rate * price_inr * commission_pct

        logger.debug(
            "[RevenuePred] %s: clicks=%.1f × conv=%.3f × price=%d × comm=%.3f = ₹%.2f",
            product_name, estimated_clicks, self._conversion_rate,
            price_inr, commission_pct, revenue,
        )
        return round(revenue, 2)

    def score_stories(self, stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Add affiliate_predicted_revenue to each story. Returns same list."""
        for story in stories:
            story["affiliate_predicted_revenue"] = self.predict(story)
        return stories


def _estimate_price_by_category(category: str) -> int:
    """Estimate average price when price_inr is not available."""
    return {
        "hardware": 15000,
        "peripheral": 5000,
        "subscription": 1000,
        "gear": 5000,
        "merchandise": 2000,
        "tool": 2000,
        "educational": 3000,
        "supplement": 2000,
    }.get(category, 3000)
