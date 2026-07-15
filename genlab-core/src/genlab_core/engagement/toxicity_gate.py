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
    # 2026-07-14 (audit F76): after this many consecutive check_inbound
    # exceptions, the gate switches to fail-CLOSED (drops all comments
    # by treating them as toxic) so a persistently-broken Detoxify
    # doesn't let toxic content through indefinitely. First-N failures
    # remain fail-open (the original design's rationale — avoid dropping
    # ALL inbound engagement on a transient hiccup).
    _MAX_CONSECUTIVE_INBOUND_FAILURES = 10
    # R-77: inbound harassment can hide in any of these dimensions, not just the
    # aggregate "toxicity" score. Detoxify "original-small" emits the same
    # Jigsaw categories as "original" (including "obscene", which we
    # deliberately exclude — mild profanity is common in comments and isn't
    # harassment we should refuse to engage with).
    _INBOUND_TOXIC_DIMS = ("toxicity", "severe_toxicity", "threat", "insult", "identity_attack")

    # U-04 (2026-06-17): swap "original" → "original-small". Same Jigsaw
    # category set, Albert backbone instead of BERT, ~3× lighter on RAM —
    # which matters because the engagement worker runs on a 4 GB Hetzner
    # box where Detoxify's BERT load competes with the publish pipeline
    # (R-03). The available model_type keys are validated at import time
    # against detoxify.MODEL_URLS in the test suite.
    _MODEL_TYPE = "original-small"

    def __init__(self) -> None:
        self._model: Any = None
        # 2026-07-14 (audit F76): track consecutive check_inbound
        # failures for the tiered fail-open → fail-closed escalation.
        self._inbound_consecutive_failures: int = 0

    def _get_model(self) -> Any:
        if self._model is None:
            from detoxify import Detoxify

            self._model = Detoxify(self._MODEL_TYPE)
            logger.info("[TOXICITY] Detoxify model loaded (model_type=%s)", self._MODEL_TYPE)
        return self._model

    def check_inbound(self, text: str) -> ToxicityResult:
        """Check inbound comment toxicity. Returns a ToxicityResult.

        Tiered fail-behavior (2026-07-14 audit F76):
          - First ``_MAX_CONSECUTIVE_INBOUND_FAILURES`` (10) exceptions:
            fail-OPEN (return non-toxic) so a transient Detoxify hiccup
            doesn't drop all inbound engagement.
          - After that threshold: fail-CLOSED (return is_toxic=True) so
            a persistently-broken Detoxify doesn't let toxic content
            through indefinitely. Emits ERROR log per failure past
            threshold for operator visibility.
          - Any successful check resets the counter (transient failures
            self-heal without operator intervention).
        """
        try:
            scores = self._get_model().predict(text)
            max_dim = max(scores, key=scores.get)
            max_val = scores[max_dim]
            # R-77: gate on ANY harassment-relevant dimension, not just the
            # aggregate "toxicity" — a threat/insult/identity attack can score
            # high while "toxicity" stays under the line, and the bot would then
            # earnestly reply to abuse.
            is_toxic = any(
                scores.get(dim, 0.0) > self.INBOUND_THRESHOLD for dim in self._INBOUND_TOXIC_DIMS
            )
            # Success — reset the consecutive-failure counter so a
            # transient earlier hiccup doesn't accumulate toward
            # fail-closed if the gate recovered.
            self._inbound_consecutive_failures = 0
            return ToxicityResult(
                is_toxic=is_toxic,
                max_dimension=max_dim,
                max_score=max_val,
                all_scores=dict(scores),
            )
        except Exception as e:
            self._inbound_consecutive_failures += 1
            if self._inbound_consecutive_failures >= self._MAX_CONSECUTIVE_INBOUND_FAILURES:
                # Fail-CLOSED — treat as toxic so the comment gets
                # skipped. Prevents a persistently-broken Detoxify
                # (missing model, permanent import error, disk full)
                # from letting toxic replies through forever.
                logger.error(
                    "[TOXICITY] Inbound check failed for the %dth consecutive time (%s) — "
                    "SWITCHING TO FAIL-CLOSED: dropping comment. "
                    "Detoxify infrastructure needs operator attention.",
                    self._inbound_consecutive_failures,
                    e,
                )
                return ToxicityResult(
                    is_toxic=True,
                    max_dimension="detoxify_broken",
                    max_score=1.0,
                    all_scores={},
                )
            logger.warning(
                "[TOXICITY] Inbound check failed (%d/%d consecutive): %s — allowing comment",
                self._inbound_consecutive_failures,
                self._MAX_CONSECUTIVE_INBOUND_FAILURES,
                e,
            )
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

    def check_outbound(self, text: str) -> ToxicityResult:
        """Score a GENERATED REPLY's toxicity for routing decisions (R-75).

        Distinct from check_inbound: this scores the reply we are about to post,
        not the comment we received, and it fails CLOSED — on model error it
        returns max_score=1.0 so the reply is treated as toxic (never silently
        auto-posted). Any dimension over OUTBOUND_THRESHOLD marks it toxic.
        """
        try:
            scores = self._get_model().predict(text)
            max_dim = max(scores, key=scores.get)
            return ToxicityResult(
                is_toxic=any(v > self.OUTBOUND_THRESHOLD for v in scores.values()),
                max_dimension=max_dim,
                max_score=scores[max_dim],
                all_scores=dict(scores),
            )
        except Exception as e:
            logger.warning("[TOXICITY] Outbound check failed: %s — treating reply as toxic", e)
            return ToxicityResult(
                is_toxic=True, max_dimension="error", max_score=1.0, all_scores={}
            )

    def is_clean_outbound(self, text: str) -> bool:
        """Return True if generated reply passes all outbound toxicity checks.

        Fail-closed: on model error, blocks the reply.
        """
        return not self.check_outbound(text).is_toxic
