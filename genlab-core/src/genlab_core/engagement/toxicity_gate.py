"""Local toxicity screening using Detoxify.

Detoxify runs entirely locally (no API cost, no data sent externally).
AUC of 98.28 on the Jigsaw dataset.

Two thresholds:
  inbound:  Skip comments with toxicity > 0.7. No engagement with harassment.
  outbound: Block replies where any toxicity dimension > 0.3.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToxicityResult:
    """Result of an inbound toxicity check."""

    is_toxic: bool
    max_dimension: str
    max_score: float
    all_scores: dict[str, float]


class ToxicityGate:
    INBOUND_THRESHOLD = 0.7
    OUTBOUND_THRESHOLD = 0.3

    def __init__(self) -> None:
        self._model: Any = None

    def _get_model(self) -> Any:
        if self._model is None:
            from detoxify import Detoxify

            self._model = Detoxify("original")
            logger.info("[TOXICITY] Detoxify model loaded")
        return self._model

    def check_inbound(self, text: str) -> ToxicityResult:
        """Check inbound comment toxicity. Returns a ToxicityResult.

        Fail-open: on model error, returns non-toxic result so comments
        aren't silently dropped.
        """
        try:
            scores = self._get_model().predict(text)
            max_dim = max(scores, key=scores.get)
            max_val = scores[max_dim]
            return ToxicityResult(
                is_toxic=scores.get("toxicity", 0.0) > self.INBOUND_THRESHOLD,
                max_dimension=max_dim,
                max_score=max_val,
                all_scores=dict(scores),
            )
        except Exception as e:
            logger.warning("[TOXICITY] Inbound check failed: %s — allowing comment", e)
            return ToxicityResult(
                is_toxic=False, max_dimension="error", max_score=0.0, all_scores={}
            )

    def is_toxic_inbound(self, text: str) -> bool:
        """Return True if comment should be silently skipped (too toxic).

        Convenience wrapper around check_inbound() for callers that only
        need the bool. Merged from platform/engagement_engine.py (F-10).
        """
        result = self.check_inbound(text)
        if result.is_toxic:
            logger.debug(
                "[TOXICITY] Skipping toxic comment: score=%.2f",
                result.all_scores.get("toxicity", result.max_score),
            )
        return result.is_toxic

    def is_clean_outbound(self, text: str) -> bool:
        """Return True if generated reply passes all outbound toxicity checks.

        Fail-closed: on model error, blocks the reply.
        """
        try:
            scores = self._get_model().predict(text)
            return all(v <= self.OUTBOUND_THRESHOLD for v in scores.values())
        except Exception as e:
            logger.warning("[TOXICITY] Outbound check failed: %s — blocking reply", e)
            return False
