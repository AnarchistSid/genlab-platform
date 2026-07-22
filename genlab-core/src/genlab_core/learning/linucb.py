"""LinUCB contextual bandit implementation.

Upgrades the existing context-free Thompson Sampling to a contextual
bandit that uses a **13-dimensional** feature vector. See
``build_content_context()`` below for the full feature layout. Task
#634 (2026-07-09) corrected stale docstring — the pre-#634 header
claimed "6-dimensional feature vector" which was accurate at
initial ship (2026-05) but has been outdated since Phase 2 L2 bumped
to 13-D on 2026-06-30 (adding hook_length, caption_length,
hashtag_count, has_affiliate, trending_score, content_type_showcase).

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

# Context feature dimensionality (12 → 13 — Phase 2 L2, 2026-06-30:
# content_type_showcase as a binary feature so the bandit can learn
# "showcase on Instagram outperforms showcase on X" instead of
# averaging across content types).
CONTEXT_DIM = 13

# Previous dimensionality. Used by from_dict to detect legacy serialized
# arms + pad them up to CONTEXT_DIM without resetting prior learning on
# the original 12 dims.
_LEGACY_CONTEXT_DIM = 12


# Intervention 9 (2026-07-01) — extended context with cyclical time
# encoding. The default LinUCB decision path stays at CONTEXT_DIM = 13
# so 40+ live arms with 13×13 A matrices keep working byte-identical.
# The v2 encoding is produced by :func:`build_content_context_v2` and
# consumed by ancillary tools (DR estimator, bandit_validation,
# ensemble) that benefit from correctly-encoded periodic time features.
#
# Dimensionality:
#   v1 (13-D):  [weekday/6, hour/23, source, ..., content_type_showcase]
#   v2 (15-D):  [weekday_sin, weekday_cos, hour_sin, hour_cos, source, ...,
#                content_type_showcase]
#
# The four cyclical dims replace the two static-integer time dims in
# v1. All non-time dims are preserved in order — so a v1→v2 remap is
# ``v2[:4] = cyclical_time`` + ``v2[4:] = v1[2:]``. This lets a future
# migration retrain LinUCB on v2 by carrying forward the same
# non-time coefficients while learning new θ entries for the cyclical
# time features from scratch.
EXTENDED_CONTEXT_DIM = 15


def _temporal_context_enabled() -> bool:
    """Exact-match feature flag. When ``GENLAB_TEMPORAL_CONTEXT_ENABLED``
    is ``true`` / ``TRUE`` / ``True``, ancillary consumers may opt into
    the v2 (cyclical-time, 15-D) feature vector. The LinUCB decision
    path is UNAFFECTED by this flag — it always uses the v1 (13-D)
    vector — because flipping LinUCB's context dimensionality at
    runtime would invalidate the shipped A/A_inv matrices without a
    coordinated retrain.

    The flag is here as the coordination point for the future
    LinUCB-v2 rollout: when a follow-up PR is ready to retrain LinUCB
    on the v2 vector, it flips this flag AND updates ``CONTEXT_DIM``
    together.

    Strictness NOTE (2026-07-14 audit-verified): only literal
    ``"true"``/``"TRUE"``/``"True"`` enable. ``"1"`` / ``"yes"`` / ``"on"``
    do NOT enable — this is pinned by
    ``test_linucb_context_v2.py::TestTemporalContextFlag.
    test_non_exact_true_stays_off``. Matches the same strict pattern
    as sibling ``_stochastic_mode_enabled`` (line 140 comment: "the
    env-string ambiguity that bit the AUTO #2 rollout"). Do NOT
    migrate to ``env_true`` — that would break the pin.
    """
    return os.environ.get("GENLAB_TEMPORAL_CONTEXT_ENABLED", "") in ("true", "TRUE", "True")


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
# 2026-07-21 Agent-3 fix: added scorebat/tmdb_trailer/anilist/
# jikan_promos/youtube_trending/twitch_trending/steam_spike aliases
# for the non-gaming niches' fetchers. Pre-fix, 4/5 niches' stories
# fell through to _SOURCE_TYPE_DEFAULT=0.5 → LinUCB source-type
# feature was constant across all non-gaming stories → bandit
# context vector degraded to niche/hour/weekday only → learning
# signal near-mean for all non-gaming.
_SOURCE_TYPE_MAP: dict[str, float] = {
    "youtube": 0.0,
    "youtube_trending": 0.0,
    "tmdb_trailer": 0.0,  # TMDB trailers → YouTube video IDs
    "anilist": 0.0,  # anime trailers → YouTube
    "jikan_promos": 0.0,  # anime PVs → YouTube
    "reddit": 0.33,
    "rss": 0.66,
    "scorebat": 0.66,  # sports RSS
    "steam_spike": 0.66,  # gaming RSS-like feed
    "twitch": 1.0,
    "twitch_trending": 1.0,
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
             Returns ``-inf`` so ``select`` argmax never picks a broken
             arm over any healthy one.
          2. Negative value inside sqrt (can happen with floating-point
             error on near-singular matrices even after inversion succeeds)
             — clamp the inner product to >= 0 before sqrt so we never
             propagate NaN into the reward signal.

        2026-07-14 design-review fix: switched from 0.5 to -inf. The
        original docstring claimed "Thompson fallback upstream picks
        this arm's score into the normal ranking" — but tracing
        ``LinUCBBandit.select_with_propensity:585`` shows pure argmax
        over ALL arms' predict() results, with NO Thompson fallback.
        In mature bandit state where healthy exploitation scores can
        be small or negative (e.g. -0.2 for a bad-content prediction),
        a broken arm returning 0.5 would WIN argmax and get selected
        for publish. -inf guarantees a broken arm never beats a
        healthy one. Sibling cold-start fallback lives at
        ``linucb_picker.score_arm:117`` (returns None on n_obs <
        min_obs), which is different from the numerical-failure path
        this fn handles.
        """
        try:
            A_inv = self._get_A_inv()
        except np.linalg.LinAlgError:
            logger.warning(
                "[LinUCB] singular matrix in arm predict (n_obs=%d) — "
                "returning -inf so argmax skips this arm",
                self.n_obs,
            )
            return float("-inf")
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
                "[LinUCB] non-finite score (n_obs=%d) — returning -inf",
                self.n_obs,
            )
            return float("-inf")
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

    # L12 (2026-07-08 audit) — thresholds pulled out for pin testing.
    # A condition number this high means the matrix is effectively
    # singular at float64 precision — inversion would give garbage
    # even if it doesn't raise. 1e12 is well below float64's ~1e16
    # dynamic range, so a legitimate well-fit posterior stays under
    # this even after hundreds of updates.
    _CONDITION_NUMBER_LIMIT: float = 1.0e12

    @classmethod
    def _validate_state(
        cls, A: np.ndarray, b: np.ndarray, *, n_obs: int
    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Validate a restored (A, b) pair. Return the pair if healthy,
        None if the state is corrupt and the caller should hard-reset.

        Checks (L12):
          1. ``A`` must be finite everywhere (no NaN, no Inf). A single
             NaN entry propagates through matrix inversion and taints
             every subsequent predict.
          2. ``b`` must be finite everywhere. Same reasoning.
          3. ``A`` must not be near-singular (condition number below
             ``_CONDITION_NUMBER_LIMIT``). A near-singular matrix means
             the inversion at predict-time produces amplified rounding
             error — a "successful" predict with garbage output. Better
             to reset than to steer traffic on garbage.
          4. ``A`` must be square. Non-square gets caught by later
             shape checks too but this fails fast.

        Returns None on any failure; caller then hard-resets to a fresh
        arm. On success returns the (possibly-unchanged) (A, b) pair.
        """
        # 1 + 2: NaN / Inf guard. np.isfinite is the standard way — it
        # returns True for finite floats and False for NaN, +Inf, -Inf.
        if not np.isfinite(A).all():
            logger.warning(
                "[LinUCB] restored A_matrix contains NaN/Inf entries "
                "(n_obs=%d) — resetting arm to fresh state",
                n_obs,
            )
            return None
        if not np.isfinite(b).all():
            logger.warning(
                "[LinUCB] restored b_vector contains NaN/Inf entries "
                "(n_obs=%d) — resetting arm to fresh state",
                n_obs,
            )
            return None

        # 4: Square check. Do this BEFORE the condition-number computation
        # because ``np.linalg.cond`` on a non-square matrix would raise
        # anyway; failing fast here gives a cleaner log line.
        if A.ndim != 2 or A.shape[0] != A.shape[1]:
            logger.warning(
                "[LinUCB] restored A_matrix has non-square shape %s "
                "(n_obs=%d) — resetting arm to fresh state",
                A.shape,
                n_obs,
            )
            return None

        # 3: Condition number. ``np.linalg.cond`` returns +inf for a
        # truly singular matrix, so the same comparison catches both
        # "singular" and "near-singular". Wrap in try/except in case
        # BLAS itself throws on a pathological matrix — treat as
        # corruption too.
        try:
            cond = float(np.linalg.cond(A))
        except (np.linalg.LinAlgError, ValueError) as exc:
            logger.warning(
                "[LinUCB] cond(A_matrix) raised %s (n_obs=%d) — resetting arm to fresh state",
                type(exc).__name__,
                n_obs,
            )
            return None
        if not np.isfinite(cond) or cond > cls._CONDITION_NUMBER_LIMIT:
            logger.warning(
                "[LinUCB] restored A_matrix is (near-)singular "
                "(cond=%.2e, limit=%.2e, n_obs=%d) — resetting arm",
                cond,
                cls._CONDITION_NUMBER_LIMIT,
                n_obs,
            )
            return None

        return A, b

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LinUCBArm:
        """Restore arm state from a serialized dict.

        Phase 2 L2 (2026-06-30) — dimension migration: when the
        serialized state has a smaller A matrix than CONTEXT_DIM (e.g.
        legacy 12-D arms after the 13-D bump for content_type_showcase),
        pad A and b with identity / zeros so the arm continues to be
        usable WITHOUT losing the prior 12 dimensions' learning. The
        new dimension starts fresh (zero entries in b, identity row/col
        in A) — exactly what we want: the bandit learns the new feature
        from scratch but keeps its existing posterior on the old ones.

        Larger-than-current state is left alone (no truncation) — that
        path indicates a rollback to a smaller CONTEXT_DIM, which would
        need explicit operator intent. We log a warning + return the
        arm as-is; the caller's downstream predict/update will mismatch
        and fall through the metric_collector's existing shape-guard.

        L12 (2026-07-08 audit) — state validation. Corrupt persisted
        state (NaN/Inf entries, singular / near-singular A) would
        silently produce garbage predictions or raise inside predict()
        every call. On corruption we log LOUDLY and reset the arm to
        a fresh identity/zero baseline. Better to lose one arm's
        learning than to steer live traffic with a NaN posterior; the
        reset arm re-accumulates through normal `update()` calls.
        See :meth:`_validate_state` for the exact checks.
        """
        A = np.array(data["A_matrix"], dtype=np.float64)
        b = np.array(data["b_vector"], dtype=np.float64)

        # L12 validation runs BEFORE dimensional migration so a corrupt
        # legacy 12-D arm doesn't get padded up and cascade the NaN
        # through to the CONTEXT_DIM shape. If the pre-pad matrix is
        # corrupt we reset to a fresh (post-pad) arm.
        validated = cls._validate_state(A, b, n_obs=int(data.get("n_obs", 0)))
        if validated is None:
            # Full reset — return a fresh arm at CONTEXT_DIM so the
            # caller's dict still lines up with the live decision path.
            # ``alpha`` is preserved because it's a per-arm tuning
            # parameter, not affected by the corruption.
            return cls(d=CONTEXT_DIM, alpha=data.get("alpha", 1.0))
        A, b = validated
        d_stored = A.shape[0]

        # ONLY pad legacy production arms (_LEGACY_CONTEXT_DIM == 12).
        # Don't pad arbitrary smaller arms — unit-test scenarios use
        # d=2, d=3 etc. and expect round-trip with no shape change.
        # The legacy production state is exactly 12-D from before this
        # PR; that's the only dim we migrate.
        if d_stored == _LEGACY_CONTEXT_DIM and CONTEXT_DIM > _LEGACY_CONTEXT_DIM:
            # Pad up: extend A with identity rows/cols (matches
            # __init__'s np.eye(d) baseline so the new dimension's
            # contribution to x^T A^{-1} x starts at 1.0 — same as a
            # fresh arm). Extend b with zeros.
            pad = CONTEXT_DIM - d_stored
            A_new = np.eye(CONTEXT_DIM, dtype=np.float64)
            A_new[:d_stored, :d_stored] = A
            b_new = np.zeros(CONTEXT_DIM, dtype=np.float64)
            b_new[:d_stored] = b
            logger.info(
                "[LinUCB] padded legacy %d-D arm state to %d-D (n_obs=%d) — "
                "prior dims preserved, new %d dim(s) start fresh",
                d_stored,
                CONTEXT_DIM,
                data.get("n_obs", 0),
                pad,
            )
            A = A_new
            b = b_new
            d = CONTEXT_DIM
        elif d_stored > CONTEXT_DIM:
            # Stored state is BIGGER than current CONTEXT_DIM — likely a
            # rollback. We DON'T silently truncate (would lose data the
            # operator may want back if they re-upgrade); we keep the
            # arm at its stored shape and let the caller deal with the
            # shape mismatch downstream. Logged loudly so an ops dump
            # surfaces this.
            logger.warning(
                "[LinUCB] serialized arm has %d-D state but CONTEXT_DIM=%d "
                "(rollback?). Restoring at stored shape; predict/update will "
                "fall through metric_collector's shape-guard.",
                d_stored,
                CONTEXT_DIM,
            )
            d = d_stored
        else:
            d = d_stored

        arm = cls(d=d, alpha=data.get("alpha", 1.0))
        arm.A = A
        arm.b = b
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


def _extract_content_type(story: dict[str, Any]) -> str:
    """Best-effort extraction of content_type from a story dict.

    Looks in three places (in order):
      1. Top-level ``content_type`` (set by some sources directly)
      2. ``gallery_metadata.content_type`` (pixwith / gallery scrapers)
      3. ``source_config.content_type`` (when source YAML metadata is
         carried alongside the entry)

    Returns the lowercased string, or "" when unset. Caller decides
    whether to map to the showcase binary feature.
    """
    ct = story.get("content_type")
    if isinstance(ct, str) and ct:
        return ct.strip().lower()
    gm = story.get("gallery_metadata") or {}
    if isinstance(gm, dict):
        ct = gm.get("content_type")
        if isinstance(ct, str) and ct:
            return ct.strip().lower()
    sc = story.get("source_config") or {}
    if isinstance(sc, dict):
        ct = sc.get("content_type")
        if isinstance(ct, str) and ct:
            return ct.strip().lower()
    return ""


def build_content_context(
    story: dict[str, Any],
    niche_id: str,
    now: datetime | None = None,
) -> np.ndarray:
    """Build a 13-dimensional context feature vector for LinUCB.

    ## Feature layout

    Dimensions (expanded from 12 — Phase 2 L2, 2026-06-30):
        0: day_of_week [0, 1]                             — TIME
        1: hour_utc [0, 1]                                — TIME
        2: source_type [0, 1]                             — CONTENT
        3: duration_bucket [0, 1] — seconds / 60          — CONTENT
        4: view_velocity [0, 1] — velocity / 5000         — CONTENT
        5: relevance_score [0, 1]                         — CONTENT
        6: hook_length [0, 1] — chars / 60                — STYLE ← see note
        7: niche_encoding [0, 1]                          — META
        8: has_affiliate [0, 1] — binary                  — META
        9: caption_length [0, 1] — chars / 200            — STYLE ← see note
       10: hashtag_count [0, 1] — count / 10              — STYLE ← see note
       11: trending_score [0, 1] — composite score        — CONTENT
       12: content_type_showcase [0, 1] — binary          — META

    ## Task #634 (2026-07-09) — mixed-feature architectural note

    Dims 6, 9, and 10 are WRITER-DETERMINED style features
    (hook length, caption length, hashtag count) — they depend on
    what the writer produces, not on inherent content properties.
    The comment at ``metric_collector.py:1461`` restricts LinUCB
    updates to content_type arms with the stated justification
    "context encodes content properties, not style". That claim was
    accurate pre-Phase-2 (when the vector was 12-D and did not
    include hook_length) but has been stale since 2026-06-30.

    Two paths forward, both operator-decision (audit #634):
        (A) purge dims 6/9/10 back to a content-only context
            → LinUCB v3 retrain PR; invalidates all 26 currently
            warm content_type arms
        (B) drop the ``item_arm == content_type`` gate at
            ``metric_collector.py:1466``
            → style arms accumulate LinUCB state on the current
            mixed-feature context; ~24 additional arms would
            start warm-training over the next few weeks

    Neither is ship-tonight scope. The mixed nature is documented
    here so future auditors don't re-diagnose the same drift.

    ## Why content_type as a feature (not an arm)

    The bandit already splits arms on content_type (showcase / news
    / etc.), so each arm sees only its own type at training time.
    To learn cross-cutting effects like "showcase on Instagram
    outperforms showcase on X" we need showcase as a CONTEXTUAL
    feature WITHIN the arm's reward model — the LinUCB theta vector
    then captures the per-arm sensitivity to showcase-vs-not.

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

    content_type = _extract_content_type(story)
    # 2026-07-22: niche-based fallback for the content_type_showcase feature.
    # Agent 3's audit (see [[audit-2026-07-22-comprehensive-followups]]) found
    # NO fetcher stage propagates `content_type` into story dicts across ANY
    # niche — so this feature had been a constant-zero dimension since the
    # 13-D bump landed. LinUCB can't learn from a constant feature.
    #
    # BlackboxBrief `_fetch.py:252` filters sources.yaml for
    # `content_type: showcase` — every ai_creators story that survives
    # `creator_only_mode` is definitionally showcase content. Backfill the
    # signal here rather than plumbing it through every fetcher's
    # `to_story()` shape (which would touch 5+ files and risk regressions).
    #
    # Other 4 niches genuinely aren't showcase (they're highlight/trailer/
    # clip/gameplay). Feature correctly stays 0.0 for them; a future v3
    # feature bump could add per-niche content-type dimensions if the
    # bandit benefits from it.
    if not content_type and niche_id == "ai_creators":
        content_type = "showcase"

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
            1.0 if content_type == "showcase" else 0.0,
        ],
        dtype=np.float64,
    )


# ── Intervention 9 (2026-07-01): cyclical time encoding ─────────────


def encode_weekday_cyclical(weekday: int) -> tuple[float, float]:
    """Return ``(weekday_sin, weekday_cos)`` for a Python weekday int
    in ``[0, 6]`` (Monday=0).

    The identity ``sin² + cos² == 1`` is preserved for every valid
    input — the transform is unit-circle-preserving by construction.
    This is the key reason a bandit learns weekday effects better with
    cyclical encoding: Sunday(6) and Monday(0) live NEXT to each other
    on the unit circle even though they're maximally distant on the
    integer scale.

    The tuple return matches numpy's ``.item()`` convention and keeps
    the caller free to insert either dim independently — useful for
    building the v2 vector where the two dims are contiguous but the
    concatenation point differs by builder.
    """
    import math

    theta = 2 * math.pi * weekday / 7.0
    return math.sin(theta), math.cos(theta)


def encode_hour_cyclical(hour: int) -> tuple[float, float]:
    """Return ``(hour_sin, hour_cos)`` for an integer hour in
    ``[0, 23]``. Same unit-circle-preserving property as
    :func:`encode_weekday_cyclical` — hour 23 sits next to hour 0
    despite the maximally-distant integer coding."""
    import math

    theta = 2 * math.pi * hour / 24.0
    return math.sin(theta), math.cos(theta)


def build_content_context_v2(
    story: dict[str, Any],
    niche_id: str,
    now: datetime | None = None,
) -> np.ndarray:
    """Return the 15-dimensional v2 context vector with cyclical time.

    Layout (indices):
        0: weekday_sin ∈ [-1, 1]
        1: weekday_cos ∈ [-1, 1]
        2: hour_sin    ∈ [-1, 1]
        3: hour_cos    ∈ [-1, 1]
        4: source_type       (was v1[2])
        5: duration_bucket   (was v1[3])
        6: view_velocity     (was v1[4])
        7: relevance_score   (was v1[5])
        8: hook_length       (was v1[6])
        9: niche_encoding    (was v1[7])
       10: has_affiliate     (was v1[8])
       11: caption_length    (was v1[9])
       12: hashtag_count     (was v1[10])
       13: trending_score    (was v1[11])
       14: content_type_showcase (was v1[12])

    Design guarantees:

    * Length == ``EXTENDED_CONTEXT_DIM`` (15). Callers can assert
      shape without a runtime dim lookup.
    * Non-time dims (indices 4-14) are byte-identical to v1 dims 2-12.
      A follow-up LinUCB retrain can carry forward the eleven
      non-time θ coefficients unchanged; only the new cyclical time
      dims need to be learned from scratch.
    * Independent of the ``GENLAB_TEMPORAL_CONTEXT_ENABLED`` flag —
      the flag gates CONSUMER opt-in, not the builder. Any caller
      can request the v2 vector; the flag exists so a future
      LinUCB-v2 rollout has a single coordination point.
    * Reads the same nested/flat story shape as
      :func:`build_content_context`. R-18 fix (nested content path
      vs flat blueprint path) applies here too so v2 doesn't
      re-introduce the train/predict mismatch v1 had before that
      fix.
    """
    if now is None:
        now = datetime.now(UTC)

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

    content_type = _extract_content_type(story)

    weekday_sin, weekday_cos = encode_weekday_cyclical(now.weekday())
    hour_sin, hour_cos = encode_hour_cyclical(now.hour)

    return np.array(
        [
            weekday_sin,
            weekday_cos,
            hour_sin,
            hour_cos,
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
            1.0 if content_type == "showcase" else 0.0,
        ],
        dtype=np.float64,
    )
