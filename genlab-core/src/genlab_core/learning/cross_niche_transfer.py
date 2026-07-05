"""Cross-Niche Hierarchical Bayesian Transfer (Intervention 2, 2026-07-01).

Each niche currently has independent bandit posteriors. Gaming's finding
that ``style:revelation`` outperforms ``style:patch_news`` has zero
influence on anime's arm priors — every new niche starts from Beta(1,1)
uniform priors and takes 4-8 weeks of observations to converge.

This module answers "what did we already learn across the other
niches?" and turns it into an informed cold-start prior. When a new
arm appears (either because a new niche launches, or because an
existing niche starts publishing a style it hadn't published before),
the arm inherits ``Beta(a, b)`` where ``(a, b)`` are moment-matched
from the CROSS-NICHE distribution of that style's observed reward
rates.

The mechanism
=============

The research doc proposed a PyMC-based hierarchical model:

    μ_style ~ Normal(0.15, 0.10)      # global reward mean for style
    σ_style ~ HalfNormal(0.05)         # cross-niche variance
    θ_niche_style ~ Normal(μ_style, σ_style)  # per-niche posterior

This module implements the same idea via **empirical Bayes moment
matching** on the Beta-Binomial conjugate — no PyMC dependency, no
MCMC sampling, no pytensor overhead. The Bayesian interpretation is
identical: we're fitting the hyperprior via method-of-moments to the
empirical distribution of per-niche observed rates.

Formula (for one style prefix like ``style:revelation``):

    For each niche i in {ai_creators, gaming, sports, movies, anime}:
        Collect (alpha_i, beta_i) from that niche's bandit_arms row
        observed_rate_i = alpha_i / (alpha_i + beta_i)

    μ = mean(observed_rate_i)               # global reward for this style
    σ² = variance(observed_rate_i)          # cross-niche variance

    Moment-match to Beta(a, b) via:
        μ  = a / (a + b)
        σ² = ab / ((a + b)² (a + b + 1))
        =>  a + b = μ(1-μ)/σ² - 1           # "effective sample size"
            a = μ * (a+b)
            b = (1-μ) * (a+b)

    Clamp ``a + b`` to :data:`_EFFECTIVE_N_RANGE` to prevent
    over-confident or degenerate priors when the cross-niche
    variance is very small or very large.

Guardrails
==========

* **Minimum 2 observing niches** per style. Below that, cross-
  niche variance is undefined and we skip the style (falls back
  to Beta(1, 1) at consumption time).
* **Minimum total observations per niche**. A niche with only 3
  observations of style:revelation is too noisy to contribute to
  the cross-niche mean — its ``observed_rate`` swings wildly with
  the next arriving reward. We require ≥ :data:`_MIN_OBS_PER_NICHE`
  before that niche's posterior counts.
* **Effective-N clamp**. Very high effective_n (small cross-niche
  variance) means the transfer prior is CONFIDENT — a new arm gets
  ~20 observations worth of head start. Too low means the transfer
  barely differs from Beta(1,1). Clamp to
  :data:`_EFFECTIVE_N_RANGE` = (2.0, 20.0).

Consumption
===========

Consumers call :func:`get_transferred_prior(niche_id, arm_id)` when
they're about to instantiate a new bandit_arms row. When the flag
``GENLAB_CROSS_NICHE_TRANSFER_ENABLED`` is off, the function returns
``None`` and the caller falls back to Beta(1, 1) — same behaviour as
today. When the flag is on AND cross-niche data is available for the
arm's style, the caller uses the returned ``(alpha, beta)`` as the
initial Beta posterior.

The reader is intentionally decoupled from the write side: the
weekly refit runner rewrites the priors JSON; consumers pick up the
new priors on their next call without any bandit-state migration.
Existing arms with accumulated posteriors are unaffected — this
intervention only speeds up COLD-START, not steady-state learning.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# ── Feature flag ────────────────────────────────────────────────────

_ENABLE_ENV_VAR: Final[str] = "GENLAB_CROSS_NICHE_TRANSFER_ENABLED"


def _integration_enabled() -> bool:
    """Exact-match feature flag. Same fail-closed pattern as the rest
    of the intelligence package."""
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


# Minimum observations (alpha + beta - 2) a niche must have on a
# given style before its posterior contributes to the cross-niche
# mean. Below this, the observed_rate is too noisy — one reward
# swings it 10 percentage points.
_MIN_OBS_PER_NICHE: Final[int] = 5

# Effective sample size clamp. ``a + b`` values outside this range
# indicate either near-zero variance (transferred prior is
# unrealistically confident) or huge variance (transferred prior is
# barely informative). Clamp to the sweet spot where a new arm
# starts with 2-20 observations worth of head start.
_EFFECTIVE_N_RANGE: Final[tuple[float, float]] = (2.0, 20.0)


# ── Data classes ───────────────────────────────────────────────────


@dataclass(frozen=True)
class TransferredPrior:
    """The moment-matched Beta prior for one style, learned from
    cross-niche observations.

    * ``style`` — the arm-id suffix this prior applies to, e.g.
      ``"revelation"``. Stored without the ``style:<niche>:`` prefix
      so the same prior applies across niches for the same style.
    * ``alpha`` / ``beta`` — the Beta prior parameters. Clamped so
      ``alpha + beta`` sits in :data:`_EFFECTIVE_N_RANGE`.
    * ``observed_rate_mean`` / ``_variance`` — the empirical
      distribution the moment-match was fit to. Exposed for
      dashboard/audit rendering.
    * ``n_niches`` — how many niches contributed to this style's
      cross-niche computation. Below :data:`_MIN_NICHES_FOR_TRANSFER`
      = 2, the style is skipped entirely (no prior emitted).
    """

    style: str
    alpha: float
    beta: float
    observed_rate_mean: float
    observed_rate_variance: float
    n_niches: int
    reasons: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


# ── Style-key extraction ──────────────────────────────────────────


def extract_style_suffix(arm_id: str) -> str | None:
    """Return the last segment of a ``style:...`` arm id, or None
    when the arm isn't a style arm.

    Handles the common shapes:
      * ``"style:revelation"``                 → ``"revelation"``
      * ``"style:gaming:revelation"``          → ``"revelation"``
      * ``"style:gaming:revelation__youtube"`` → ``"revelation"``
        (drops the ``__<platform>`` suffix used by PR #641)
      * ``"hour:12:instagram:gaming"``         → None
      * ``"source:youtube_trending__facebook"`` → None
    """
    if not arm_id.startswith("style:"):
        return None
    core = arm_id.split("__", 1)[0]  # strip platform suffix
    segments = core.split(":")
    if len(segments) < 2:
        return None
    return segments[-1] or None


def extract_transformation_suffix(arm_id: str) -> str | None:
    """Extract the cross-niche prior key from a transformation arm_id.

    Registered by PR 3 (#667), transformation arm_ids have the shape
    ``transform__<dimension>__<value>``. The cross-niche key is the
    ``<dimension>__<value>`` composite — same dimension + same value
    across niches shares the prior.

    Rationale for grouping by ``<dim>__<value>`` (not just ``<value>``):
    ``music_mood__cinematic`` and (hypothetically)
    ``caption_style__cinematic_style`` have completely different
    semantic scope — never share priors. The dimension is part of the
    key.

    Examples:
      * ``transform__music_mood__cinematic``  → ``music_mood__cinematic``
      * ``transform__hook_framing__question`` → ``hook_framing__question``
      * ``transform__audio_ducking__-12``     → ``audio_ducking__-12``
      * ``style:gaming:revelation``           → None
      * ``source:youtube_trending``           → None
      * ``transform__bad_shape``              → None (missing value segment)
    """
    if not arm_id.startswith("transform__"):
        return None
    rest = arm_id[len("transform__"):]
    # rest must be '<dim>__<value>' — needs at least one more '__'
    if "__" not in rest:
        return None
    # Must have both non-empty segments after split.
    parts = rest.split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return rest


def extract_prior_key(arm_id: str) -> str | None:
    """Unified extractor — routes to style / transformation / None.

    This is the function ``compute_transferred_priors`` and
    ``get_transferred_prior`` call to derive the cross-niche group
    key for any arm_id.

    Adding a new arm class (e.g. per-hour or per-source) that should
    participate in cross-niche transfer means adding a branch here
    and its extractor — the callers don't need to change.
    """
    style = extract_style_suffix(arm_id)
    if style is not None:
        return style
    return extract_transformation_suffix(arm_id)


# ── Moment matching ───────────────────────────────────────────────


def _moment_match_beta(mean: float, variance: float) -> tuple[float, float] | None:
    """Solve for ``(alpha, beta)`` such that ``Beta(a, b)`` has the
    given mean and variance. Returns None when the math is
    degenerate (variance ≥ mean(1-mean), variance ≤ 0, or
    effective_n out of the allowed clamp).

    Formulas:

        a + b = mean * (1 - mean) / variance - 1
        a     = mean * (a + b)
        b     = (1 - mean) * (a + b)

    The ``mean(1-mean)`` upper bound on variance is the largest
    variance a random variable in [0, 1] can have; beyond that,
    no Beta distribution exists. If we ever see it, the cross-
    niche sample is too disparate to justify transfer.
    """
    if variance <= 0.0 or not (0.0 < mean < 1.0):
        return None
    max_var = mean * (1.0 - mean)
    if variance >= max_var:
        return None
    effective_n = max_var / variance - 1.0
    lo, hi = _EFFECTIVE_N_RANGE
    if effective_n <= 0 or not (lo <= effective_n <= hi):
        # Explicit clamp instead of silent projection — a transfer
        # with effective_n=0.5 is a "we barely learned anything"
        # prior worth skipping to fall back to Beta(1,1), and
        # effective_n=100 is an over-confident prior that would
        # drown out the first 50 observations of the new arm.
        return None
    alpha = mean * effective_n
    beta = (1.0 - mean) * effective_n
    return alpha, beta


# ── Refit ─────────────────────────────────────────────────────────


def compute_transferred_priors(
    bandit_state: dict[str, dict[str, tuple[float, float]]],
) -> dict[str, TransferredPrior]:
    """Given ``{niche_id: {arm_id: (alpha, beta)}}``, compute one
    :class:`TransferredPrior` per style suffix that has enough
    cross-niche data to justify a transfer.

    Args:
        bandit_state: Bandit posteriors keyed by niche → arm →
            (alpha, beta). Missing niches / empty arms are handled
            gracefully. In production this is loaded via
            :func:`genlab_core.learning.arm_loader.load_all_arms`.

    Returns:
        Mapping ``{style_suffix: TransferredPrior}``. Styles that
        don't clear the minimum guardrails are absent from the
        returned dict — consumers treat "no key" as "fall back to
        Beta(1,1)".
    """
    # Group observed rates by prior-key across niches. ``prior_key``
    # is a string that unifies "style suffix" (legacy) and
    # "transformation dimension__value" (PR 13, 2026-07-05) — see
    # extract_prior_key for the routing.
    per_style: dict[str, list[tuple[str, float, int]]] = {}
    for niche_id, arms in bandit_state.items():
        for arm_id, (alpha, beta) in arms.items():
            style = extract_prior_key(arm_id)
            if style is None:
                continue
            # a + b - 2 is the "effective observation count" beyond
            # the Beta(1,1) uniform prior.
            n_obs = int(round(alpha + beta - 2.0))
            if n_obs < _MIN_OBS_PER_NICHE:
                continue
            total = alpha + beta
            if total <= 0:
                continue
            observed_rate = alpha / total
            per_style.setdefault(style, []).append((niche_id, observed_rate, n_obs))

    priors: dict[str, TransferredPrior] = {}
    for style, entries in per_style.items():
        if len({niche for niche, _, _ in entries}) < 2:
            continue

        # Sample stats: unweighted mean + Bessel-corrected variance.
        # Weighting by observation count would give bigger-niche
        # posteriors disproportionate say in the cross-niche mean;
        # equal weighting is what the "cross-niche" framing intends.
        rates = [r for _, r, _ in entries]
        mean = sum(rates) / len(rates)
        # ddof=1 (Bessel) for the unbiased estimator; with only 2
        # niches this bumps the variance denominator from 2 to 1.
        variance = sum((r - mean) ** 2 for r in rates) / (len(rates) - 1)

        matched = _moment_match_beta(mean, variance)
        if matched is None:
            continue
        alpha, beta = matched
        priors[style] = TransferredPrior(
            style=style,
            alpha=alpha,
            beta=beta,
            observed_rate_mean=mean,
            observed_rate_variance=variance,
            n_niches=len({niche for niche, _, _ in entries}),
            reasons=[
                f"effective_n={alpha + beta:.2f}",
                f"contributing_niches={sorted({niche for niche, _, _ in entries})}",
            ],
        )
    return priors


# ── Persistence ────────────────────────────────────────────────────


def _priors_path() -> Path:
    """``$GENLAB_TMP/cross-niche-transfer/priors.json`` — mirrors
    the .tmp-based artifact convention used by the trend-anticipation
    and DR modules."""
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "cross-niche-transfer" / "priors.json"


def persist_priors(
    priors: dict[str, TransferredPrior],
    *,
    output_path: Path | None = None,
) -> Path:
    """Write the priors dict to a single JSON artifact. Overwrite-
    at-refit is intentional — consumers always see the latest
    empirical Bayes fit; there's no versioning need."""
    path = output_path or _priors_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "priors": {style: p.to_json() for style, p in priors.items()},
    }
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def load_persisted_priors(
    *,
    input_path: Path | None = None,
) -> dict[str, TransferredPrior]:
    """Read the persisted priors artifact. Returns empty dict when
    the artifact is absent or malformed — same fail-open contract as
    the trend-anticipation read side."""
    path = input_path or _priors_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.debug("[cross_niche_transfer] priors read failed: %s", exc)
        return {}

    raw_priors = payload.get("priors") if isinstance(payload, dict) else None
    if not isinstance(raw_priors, dict):
        return {}
    out: dict[str, TransferredPrior] = {}
    for style, row in raw_priors.items():
        if not isinstance(row, dict):
            continue
        try:
            out[style] = TransferredPrior(
                style=str(row.get("style", style)),
                alpha=float(row.get("alpha", 1.0)),
                beta=float(row.get("beta", 1.0)),
                observed_rate_mean=float(row.get("observed_rate_mean", 0.0)),
                observed_rate_variance=float(row.get("observed_rate_variance", 0.0)),
                n_niches=int(row.get("n_niches", 0)),
                reasons=list(row.get("reasons") or []),
            )
        except (TypeError, ValueError):
            continue
    return out


# ── Consumer read side ────────────────────────────────────────────


def get_transferred_prior(
    niche_id: str,
    arm_id: str,
    *,
    input_path: Path | None = None,
) -> tuple[float, float] | None:
    """Return ``(alpha, beta)`` for a NEW arm's Beta prior, or None.

    Consumers call this when instantiating a bandit_arms row for a
    style that this niche hasn't seen before. The workflow:

    1. Consumer detects a new arm to create (a new niche launching,
       or an existing niche starting to publish a new style).
    2. Consumer calls ``get_transferred_prior(niche_id, arm_id)``.
    3. If the flag is off, no priors persisted, or no cross-niche
       data for this style — returns None; consumer falls back to
       ``Beta(1, 1)``.
    4. Otherwise returns the moment-matched ``(alpha, beta)`` for
       the row's initial state.

    The ``niche_id`` argument is currently unused in the transfer
    logic — same style prior applies across niches by construction.
    Kept in the signature so a future refinement (per-(niche, style)
    corrections, e.g. niche-cluster-aware transfer) can land without
    breaking the consumer contract.
    """
    _ = niche_id  # accepted for consumer-contract stability

    if not _integration_enabled():
        return None

    # PR 13 (2026-07-05): unified extractor handles both legacy
    # ``style:...`` arms AND transformation arms ``transform__dim__value``.
    style = extract_prior_key(arm_id)
    if style is None:
        return None

    priors = load_persisted_priors(input_path=input_path)
    prior = priors.get(style)
    if prior is None:
        return None
    return prior.alpha, prior.beta


__all__ = [
    "TransferredPrior",
    "extract_style_suffix",
    "extract_transformation_suffix",
    "extract_prior_key",
    "compute_transferred_priors",
    "persist_priors",
    "load_persisted_priors",
    "get_transferred_prior",
    "_moment_match_beta",
    "_MIN_OBS_PER_NICHE",
    "_EFFECTIVE_N_RANGE",
]
