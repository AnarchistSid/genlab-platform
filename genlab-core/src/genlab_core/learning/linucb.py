"""LinUCB contextual bandit implementation.

Upgrades the existing context-free Thompson Sampling to a contextual bandit
that uses a 6-dimensional feature vector (day_of_week, hour_utc, source_type,
duration_bucket, view_velocity, relevance_score).

Cold-start protection: arms with fewer than MIN_OBS_FOR_LINUCB observations
fall back to Thompson Sampling (existing alpha/beta posteriors).

LinUCB formula:
    p = theta^T x + alpha * sqrt(x^T A^{-1} x)
    where theta = A^{-1} b

Propensity logging (AGENT-AUTONOMY-RESEARCH Move #8, PR T)
==========================================================
Every selection now also returns the **propensity** ``p(arm | context)`` —
the probability with which the policy would have picked this arm at this
context. This is the foundational signal for Inverse Propensity Scoring
(IPS) counterfactual evaluation: "what would last month's reward have
been under a different policy?" Without per-decision propensity logged
at the time of the decision, IPS is impossible to reconstruct — the
doc explicitly flags this as **painful to add retroactively** but cheap
to add forward (one float per decision).

In default deterministic mode (``GENLAB_LINUCB_STOCHASTIC_ENABLED`` unset
or ``0``), ``select_with_propensity`` returns ``(argmax_arm, 1.0)`` —
the limit-case propensity for a pure-argmax policy. This preserves the
existing behavior + reward-interpretation of every historical observation
while still populating the propensity field with a meaningful value.

In stochastic mode (``GENLAB_LINUCB_STOCHASTIC_ENABLED=1``), arms are
sampled from a softmax over the UCB scores at temperature
``GENLAB_LINUCB_TEMPERATURE`` (default 0.5 — mildly stochastic).
Each arm's propensity equals its softmax weight. Lower temperatures
approach the deterministic limit; higher temperatures approach uniform
random. IPS estimators recover meaningful counterfactual estimates
only under stochastic policies — flipping the flag is the natural
prerequisite for the offline-policy-eval workflow Move #8 enables.

References:
    Li et al. (2010) "A Contextual-Bandit Approach to Personalized News Article
    Recommendation", WWW 2010.
    Eugene Yan, "Counterfactual Evaluation for Recommendation Systems".
    AGENT-AUTONOMY-RESEARCH.md, Move #8 (this repo's docs/).
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Context feature dimensionality (expanded from 6 to 12 — Break 12 fix)
CONTEXT_DIM = 12

# Minimum observations before LinUCB predictions are trusted.
# Below this threshold, fall back to Thompson Sampling.
MIN_OBS_FOR_LINUCB = 50

# Default softmax temperature when stochastic mode is enabled. 0.5 is
# mildly stochastic — argmax still wins most of the time but every arm
# carries non-zero, knowable propensity. Tunable via env so an operator
# can dial exploration up/down without code changes.
DEFAULT_TEMPERATURE = 0.5

# Numerical floor on per-arm propensity. IPS weights are 1/p, so an arm
# with p≈0 becomes an outlier that dominates the estimator (the variance
# explosion Move #8 calls out). Clamping to a small positive value
# guarantees an upper bound on the IPS weight + matches the conventional
# "truncation" mitigation from the offline-policy-eval literature.
_MIN_PROPENSITY = 1e-6


def _stochastic_mode_enabled() -> bool:
    """Single source of truth for the stochastic-mode opt-in flag.

    Default OFF — deterministic argmax is what every historical reward
    observation was generated under, so flipping the default would
    change the meaning of accumulated bandit_arms state without an
    operator intent. Opt-in pattern matches Lever C / K / O / G1 /
    G2 / I1+I3 / I2 throughout the learning subsystem.

    Only the literal ``"1"`` enables — ``"true"``/``"yes"`` are
    strictly disabled to avoid the env-string ambiguity that bit the
    AUTO #2 rollout (Memory note: "only '1' enables").
    """
    return os.environ.get("GENLAB_LINUCB_STOCHASTIC_ENABLED", "0").strip() == "1"


def _read_temperature() -> float:
    """Read softmax temperature from env, falling back to default on
    parse error or zero/negative values (which would be ill-defined
    in the softmax formula)."""
    raw = os.environ.get("GENLAB_LINUCB_TEMPERATURE", "").strip()
    if not raw:
        return DEFAULT_TEMPERATURE
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[LinUCB] invalid GENLAB_LINUCB_TEMPERATURE=%r — using default %s",
            raw,
            DEFAULT_TEMPERATURE,
        )
        return DEFAULT_TEMPERATURE
    if value <= 0.0:
        logger.warning(
            "[LinUCB] GENLAB_LINUCB_TEMPERATURE must be > 0 (got %s) — using default %s",
            value,
            DEFAULT_TEMPERATURE,
        )
        return DEFAULT_TEMPERATURE
    return value


def compute_softmax_probabilities(
    scores: dict[str, float],
    temperature: float = DEFAULT_TEMPERATURE,
) -> dict[str, float]:
    """Convert per-arm UCB scores to a softmax probability distribution.

    ``p(arm i) = exp(score_i / temperature) / Σ exp(score_j / temperature)``

    Numerically stable: subtracts the max score before the exponent
    (the classic softmax trick) so exp() never overflows when UCB
    scores are large.

    Returns a dict mapping arm_id → probability with probabilities
    summing to 1.0 (within float epsilon). Empty input returns empty
    dict. Probabilities are clamped to ``_MIN_PROPENSITY`` to bound
    downstream IPS weights — sum will still be ≈ 1.0 because the clamp
    only fires on near-zero values that don't affect the total.
    """
    if not scores:
        return {}
    arm_ids = list(scores.keys())
    raw = np.array([scores[a] for a in arm_ids], dtype=np.float64)
    # Numerical stability: subtract max before exp.
    shifted = (raw - raw.max()) / temperature
    exp_vals = np.exp(shifted)
    total = exp_vals.sum()
    if not np.isfinite(total) or total <= 0.0:
        # Degenerate case: fall back to uniform. Shouldn't happen with
        # finite scores + positive temperature, but the IPS estimator
        # downstream is safer with a defined fallback than a NaN.
        probs = np.full_like(exp_vals, 1.0 / len(arm_ids))
    else:
        probs = exp_vals / total
    # Clamp floor — never publish a propensity below _MIN_PROPENSITY.
    probs = np.maximum(probs, _MIN_PROPENSITY)
    return {arm_ids[i]: float(probs[i]) for i in range(len(arm_ids))}


# Source type encoding map
_SOURCE_TYPE_MAP: dict[str, float] = {
    "youtube": 0.0,
    "reddit": 0.33,
    "rss": 0.66,
    "twitch": 1.0,
}
_SOURCE_TYPE_DEFAULT = 0.5


class LinUCBArm:
    """Single arm with d-dimensional context.

    Maintains:
        A: d x d matrix (identity + sum of outer products of context vectors)
        b: d-vector (sum of reward * context vectors)
        n_obs: number of observations (for cold-start detection)
    """

    def __init__(self, d: int, alpha: float = 1.0) -> None:
        self.A = np.eye(d, dtype=np.float64)
        self.b = np.zeros(d, dtype=np.float64)
        self.alpha = alpha
        self.n_obs = 0
        # Cached inverse of A. Invariant: when not None, equals
        # ``np.linalg.inv(self.A)``. Invalidated (set to None) whenever
        # ``A`` mutates — only in :meth:`update` and :meth:`from_dict`.
        # Avoids recomputing the d×d inversion (O(d³)) on every
        # :meth:`predict` call. With the bandit selecting from N arms
        # per scoring decision, the matrix is unchanged across the N
        # predicts but was being inverted N times pre-cache.
        self._A_inv_cache: np.ndarray | None = None

    def _get_A_inv(self) -> np.ndarray:
        """Return cached ``A^{-1}``, computing it on cache miss.

        Raises :class:`numpy.linalg.LinAlgError` if ``A`` is singular —
        caller (:meth:`predict`) catches and degrades gracefully.
        """
        if self._A_inv_cache is None:
            self._A_inv_cache = np.linalg.inv(self.A)
        return self._A_inv_cache

    def predict(self, x: np.ndarray) -> float:
        """Compute UCB score for this arm given context x.

        p = theta^T x + alpha * sqrt(x^T A^{-1} x)
        where theta = A^{-1} b

        Guards against two numerical edge cases:
          1. Singular/near-singular matrix — np.linalg.inv raises LinAlgError.
             Returns a neutral 0.5 so the Thompson fallback upstream picks
             this arm's score into the normal ranking.
          2. Negative value inside sqrt (can happen with floating-point
             error on near-singular matrices even after inversion succeeds)
             — clamp the inner product to >= 0 before sqrt so we never
             propagate NaN into the reward signal.
        """
        try:
            A_inv = self._get_A_inv()
        except np.linalg.LinAlgError:
            logger.warning(
                "[LinUCB] singular matrix in arm predict (n_obs=%d) — "
                "falling back to neutral score",
                self.n_obs,
            )
            return 0.5
        theta = A_inv @ self.b
        exploitation = float(theta @ x)
        inner = float(x @ A_inv @ x)
        # Clamp to 0 — a positive semi-definite matrix should always give
        # x^T A^{-1} x >= 0, but floating-point drift on degenerate arms
        # can tip it negative. NaN from sqrt(-eps) would then corrupt the
        # reward signal downstream.
        exploration = self.alpha * float(np.sqrt(max(0.0, inner)))
        score = exploitation + exploration
        if not np.isfinite(score):
            logger.warning(
                "[LinUCB] non-finite score (n_obs=%d) — neutral fallback",
                self.n_obs,
            )
            return 0.5
        return score

    def update(self, x: np.ndarray, reward: float) -> None:
        """Update arm with observed reward for context x.

        A += x x^T
        b += reward * x
        """
        self.A += np.outer(x, x)
        self.b += reward * x
        self.n_obs += 1
        # Invalidate cached inverse — A just mutated.
        self._A_inv_cache = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize arm state to a JSON-compatible dict."""
        return {
            "A_matrix": self.A.tolist(),
            "b_vector": self.b.tolist(),
            "alpha": self.alpha,
            "n_obs": self.n_obs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LinUCBArm:
        """Restore arm state from a serialized dict."""
        A = np.array(data["A_matrix"], dtype=np.float64)
        d = A.shape[0]
        arm = cls(d=d, alpha=data.get("alpha", 1.0))
        arm.A = A
        arm.b = np.array(data["b_vector"], dtype=np.float64)
        arm.n_obs = data.get("n_obs", 0)
        # Don't pre-compute the inverse here — let the first predict
        # trigger it lazily. Restored arms are sometimes loaded but
        # never queried (e.g. cold-path persistence callers); paying
        # the inversion eagerly would waste it.
        arm._A_inv_cache = None
        return arm


class LinUCBBandit:
    """Contextual bandit with LinUCB algorithm.

    Manages multiple arms, each with their own A matrix and b vector.
    """

    def __init__(self, arm_ids: list[str], d: int, alpha: float = 1.0) -> None:
        self.d = d
        self.alpha = alpha
        self.arms: dict[str, LinUCBArm] = {aid: LinUCBArm(d, alpha) for aid in arm_ids}

    def select(self, context: np.ndarray) -> str:
        """Select the arm with the highest UCB score for the given context.

        Backward-compat alias for ``select_with_propensity`` that drops
        the propensity. New callers wanting IPS support should use
        ``select_with_propensity`` directly. Existing callers that
        unpack a single string keep working unchanged.
        """
        arm_id, _propensity = self.select_with_propensity(context)
        return arm_id

    def select_with_propensity(
        self,
        context: np.ndarray,
        *,
        stochastic: bool | None = None,
        temperature: float | None = None,
    ) -> tuple[str, float]:
        """Select an arm and return its selection propensity.

        Returns ``(arm_id, propensity)`` where ``propensity`` is
        ``p(arm_id | context)`` under the active selection policy:

        - **Deterministic mode** (default): argmax over UCB scores;
          propensity is 1.0 for the picked arm. This is the limit
          case — every other arm had propensity 0.0, which is fine
          to omit because IPS only uses the chosen arm's weight.
        - **Stochastic mode** (``stochastic=True`` or env opt-in):
          arms are sampled from softmax(UCB / temperature); propensity
          is the picked arm's softmax weight.

        Args:
            context: feature vector (shape (d,)) used by every arm's
                ``predict``.
            stochastic: explicit override of the env-based opt-in.
                ``None`` (default) consults ``GENLAB_LINUCB_STOCHASTIC_ENABLED``.
                Tests pass ``True``/``False`` directly to avoid env
                mutation.
            temperature: explicit override of ``GENLAB_LINUCB_TEMPERATURE``.
                ``None`` reads the env. Ignored in deterministic mode.

        AGENT-AUTONOMY-RESEARCH Move #8: this is the entry point for
        per-decision propensity logging. Caller writes the returned
        ``propensity`` alongside the arm_id + context into the
        ``pending_feedback`` row so future IPS-based offline policy
        evaluation can reconstruct the selection distribution.
        """
        if stochastic is None:
            stochastic = _stochastic_mode_enabled()
        if temperature is None:
            temperature = _read_temperature()

        scores = {aid: arm.predict(context) for aid, arm in self.arms.items()}
        if not scores:
            raise ValueError("LinUCBBandit.select_with_propensity: no arms configured")

        if not stochastic:
            # Deterministic argmax. Propensity = 1.0 for the picked arm
            # by definition of a degenerate policy. The clamp floor
            # (_MIN_PROPENSITY) deliberately doesn't apply here — for
            # a deterministic policy the chosen-arm weight IS 1.0;
            # clamping would be a lie.
            arm_id = max(scores, key=scores.get)  # type: ignore[arg-type]
            return arm_id, 1.0

        # Stochastic: softmax over UCB scores, sample one arm.
        probs = compute_softmax_probabilities(scores, temperature=temperature)
        arm_ids = list(probs.keys())
        weights = np.array([probs[a] for a in arm_ids], dtype=np.float64)
        # np.random.choice requires probabilities that exactly sum to 1.
        # The clamp floor in compute_softmax_probabilities can push the
        # sum slightly above 1 if many arms hit the floor. Renormalize
        # here so np.random.choice doesn't reject our weights.
        weights = weights / weights.sum()
        chosen_idx = int(np.random.choice(len(arm_ids), p=weights))
        chosen_arm = arm_ids[chosen_idx]
        # Return the original (clamped) propensity, not the
        # renormalized one — the floor is the value we want IPS to
        # use as the lower bound on weights.
        return chosen_arm, probs[chosen_arm]

    def update(self, arm_id: str, context: np.ndarray, reward: float) -> None:
        """Update the specified arm with the observed reward."""
        if arm_id not in self.arms:
            raise KeyError(f"Unknown arm: {arm_id}")
        self.arms[arm_id].update(context, reward)

    def add_arm(self, arm_id: str) -> None:
        """Add a new arm if it doesn't already exist."""
        if arm_id not in self.arms:
            self.arms[arm_id] = LinUCBArm(self.d, self.alpha)


_NICHE_ENCODING: dict[str, float] = {
    "ai_creators": 0.0,
    "gaming": 0.2,
    "sports": 0.4,
    "movies": 0.6,
    "anime": 0.8,
}


def build_content_context(
    story: dict[str, Any],
    niche_id: str,
    now: datetime | None = None,
) -> np.ndarray:
    """Build a 12-dimensional context feature vector for LinUCB.

    Dimensions (expanded from 6 — Break 12 fix):
        0: day_of_week [0, 1]
        1: hour_utc [0, 1]
        2: source_type [0, 1]
        3: duration_bucket [0, 1] — seconds / 60
        4: view_velocity [0, 1] — velocity / 5000 (scaled down for new channels)
        5: relevance_score [0, 1]
        6: hook_length [0, 1] — chars / 60
        7: niche_encoding [0, 1]
        8: has_affiliate [0, 1] — binary
        9: caption_length [0, 1] — chars / 200
       10: hashtag_count [0, 1] — count / 10
       11: trending_score [0, 1] — composite score

    Returns:
        np.ndarray of shape (CONTEXT_DIM,) with float64 values in [0, 1].
    """
    if now is None:
        now = datetime.now(UTC)

    # R-18: extract the content features from EITHER the nested story shape
    # (``content.hook`` / ``content.instagram.caption`` — what selection/predict
    # time passes) OR the flat blueprint shape (top-level ``hook`` / ``caption`` —
    # what the publisher carries at train time). Reading only the nested paths
    # silently zeroed dims 6 & 9 whenever a flat dict was passed, so the bandit
    # TRAINED on hook_length=0/caption_length=0 while PREDICTING on the real
    # lengths — a systematic predict/train mismatch on its two most important
    # content-quality signals.
    content = story.get("content", {})
    if not isinstance(content, dict):
        content = {}
    instagram = content.get("instagram", {})
    if not isinstance(instagram, dict):
        instagram = {}
    hook = content.get("hook") or story.get("hook") or story.get("hook_text") or ""
    caption = (
        instagram.get("caption") or story.get("caption") or story.get("instagram_caption") or ""
    )
    hashtags = story.get("hashtags", [])
    if isinstance(hashtags, str):
        hashtags = hashtags.split()

    return np.array(
        [
            now.weekday() / 6.0,
            now.hour / 23.0,
            _SOURCE_TYPE_MAP.get(
                story.get("source_type", story.get("source", "")), _SOURCE_TYPE_DEFAULT
            ),
            min(story.get("duration_seconds", 30) / 60.0, 1.0),
            min(story.get("view_velocity", 0) / 5000.0, 1.0),
            story.get("relevance_score", story.get("composite_score", 0.5)),
            min(len(hook) / 60.0, 1.0),
            _NICHE_ENCODING.get(niche_id, 0.5),
            1.0 if story.get("affiliate_product") else 0.0,
            min(len(caption) / 200.0, 1.0),
            min(len(hashtags) / 10.0, 1.0),
            min(story.get("composite_score", story.get("score", 0.5)), 1.0),
        ],
        dtype=np.float64,
    )
