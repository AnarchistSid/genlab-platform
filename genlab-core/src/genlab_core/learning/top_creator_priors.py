"""Top-creator prior read + math (B.3, 2026-07-08).

Consumer half of the B.1 / B.2 pipeline. Reads B.2's per-niche
``top_creator_priors.json`` artifact and exposes:

* ``load_correlations(niche_id) -> dict[str, float] | None`` — flat
  ``{feature: spearman_r}`` map, or ``None`` if artifact missing /
  malformed / stale beyond the fallback window
* ``correlation_to_prior_delta(r, weight) -> tuple[float, float]`` —
  pure math converting a Spearman correlation into a weak Beta prior
  adjustment ``(delta_alpha, delta_beta)`` in the [0, ``MAX_DELTA``]
  range
* ``get_arm_prior_adjustment(niche_id, feature) -> (dα, dβ) | None``
  — the surface B.3's future consumer wire calls at bandit-arm
  instantiation for a specific arm↔feature mapping

## What this ships (and what it doesn't)

**Ships**: the READ side + the pure math. Any future caller can
import ``get_arm_prior_adjustment`` at bandit-arm-registration time
and apply a weak boost to α / β.

**Does not ship**: the actual wire at arm-instantiation site. The
current arm ID space is ``content_type__platform`` (see
``meta_prior.TRANSFER_MATRIX``); B.1's feature space is
title/description/timing. There is no natural 1:1 mapping today.
When a stable arm↔feature mapping emerges (typically after the
transformation-orchestrator's 11 dimensions accumulate reward
data), the consumer wire becomes one-line at ``arm_loader.save_arm``.

This mirrors the discipline the codebase has established:

* Intervention 2 (cross-niche transfer) shipped
  ``get_transferred_prior`` — one-line consumer adopter deferred
* B.1 shipped ``top_creator_features.compute_feature_view_correlation``
  — B.2 was the first consumer
* Every intelligence engine ships observability + math first, flag-
  guarded, before the consumer wire that mutates behaviour

## The math

Correlation ``r`` ∈ [-1, 1]. Positive ``r`` means the feature
correlates with higher view velocity in the top-100 sample; negative
``r`` means it correlates with lower.

    ``|r|`` scaled to [0, 1] is the strength of the signal. Multiply
    by ``weight`` ∈ [0, 1] (caller controls the confidence of the
    transfer — same lever as ``meta_prior.EXPLORATION_INFLATION``).
    Cap at ``MAX_DELTA``.

Positive correlations bump α (favouring exploration of this
pattern); negative correlations bump β (favouring avoidance).

Zero correlation contributes zero delta — never over-adjusts arms
without evidence.

## Why WEAK

The scope memo (``[[top-creator-learning-scope-2026-07-07]]``) makes
the honest case explicit: correlation on top-100 external videos
IS NOT causation on a 200-subscriber channel. Simpson's paradox
territory. The prior boost is bounded at ``MAX_DELTA = 2.0`` — an
order of magnitude weaker than actual observed reward (which drives
α / β up by ~1 per observation without any cap). The bandit's own
observations quickly override the boost.

## Flag

``GENLAB_TOP_CREATOR_PRIORS_ENABLED`` (exact-match ``"true"``) gates
whether the read/adjustment functions actually return non-``None``.
Off by default. Same rule as every other intelligence-package flag.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

# Maximum α / β delta from a single feature. Bounds the total prior
# boost across all features at (∑features × MAX_DELTA) — with 13
# features that's ≤ 26 in absolute terms, but the correlation-weight
# product means realistic per-feature deltas are 0.1-0.5 in steady
# state. Real observed reward (α += 1 per positive observation)
# quickly overwhelms.
MAX_DELTA: Final[float] = 2.0

# Default consumer weight when no explicit ``weight`` passed. Chosen
# to keep the per-feature delta modest — a strong correlation |r|=0.6
# at default weight gives (0.6 * 0.5) * 2.0 = 0.6 delta, well under
# what a single observation contributes.
_DEFAULT_WEIGHT: Final[float] = 0.5

# Flag env var — read at each call, not cached. Matches every other
# intelligence-package flag (fail-closed).
_ENABLE_ENV_VAR: Final[str] = "GENLAB_TOP_CREATOR_PRIORS_ENABLED"


def _integration_enabled() -> bool:
    """Exact-match ``"true"`` / ``"TRUE"`` / ``"True"``. Same
    discipline as ``trend_anticipation._integration_enabled`` —
    NO strip, whitespace around the value counts as unset. This
    is deliberate: consistent flag-parsing rules across all
    intelligence engines so operators don't have to remember
    which flags are lenient about whitespace."""
    return os.environ.get(_ENABLE_ENV_VAR, "") in ("true", "TRUE", "True")


def _artifact_dir() -> Path:
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "top-creator-priors"


def _load_artifact_file(path: Path) -> dict[str, float] | None:
    """Parse one YYYYMMDD-<niche>.json. Fail-open on IOError /
    JSONDecodeError / missing ``correlations`` key."""
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("[top-creator-priors] malformed %s: %s", path.name, exc)
        return None

    correlations = payload.get("correlations")
    if not isinstance(correlations, dict) or not correlations:
        return None

    # Coerce all values to float; drop non-numeric silently. Real B.2
    # output is always numeric but treat other producers defensively.
    out: dict[str, float] = {}
    for k, v in correlations.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out or None


def load_correlations(niche_id: str) -> dict[str, float] | None:
    """Read the most recent B.2 artifact for ``niche_id``.

    Prefers today's UTC-dated file; falls back to yesterday's when
    today's hasn't been written yet (weekly cadence means most
    days won't have a fresh artifact). Beyond that, returns
    ``None`` — a two-week-stale correlation set is not worth
    steering on.

    Fail-open: any read / parse error returns ``None`` rather than
    raising.

    Flag-gated: when ``GENLAB_TOP_CREATOR_PRIORS_ENABLED`` is not
    ``"true"``, returns ``None`` even if the artifact exists. This
    is the observation-off-by-default discipline.
    """
    if not _integration_enabled():
        return None

    dir_ = _artifact_dir()
    if not dir_.is_dir():
        return None

    now = datetime.now(UTC)
    # Look back up to 8 days: covers the case where the weekly Sunday
    # refit fires but the operator polls on the following Saturday.
    for delta_days in range(0, 9):
        stamp = (now - timedelta(days=delta_days)).strftime("%Y%m%d")
        path = dir_ / f"{stamp}-{niche_id}.json"
        if path.is_file():
            return _load_artifact_file(path)

    return None


def correlation_to_prior_delta(
    correlation: float,
    weight: float = _DEFAULT_WEIGHT,
) -> tuple[float, float]:
    """Convert a Spearman r ∈ [-1, 1] to a Beta prior adjustment.

    Args:
        correlation: Spearman rank correlation with view velocity.
            Values outside [-1, 1] are clamped.
        weight: Caller-controlled confidence multiplier ∈ [0, 1].
            Default 0.5 matches ``meta_prior``'s 0.5 exploration
            inflation. Higher = trust the correlation more; lower =
            treat it as weaker evidence.

    Returns:
        ``(delta_alpha, delta_beta)`` — both in ``[0, MAX_DELTA]``.
        Positive ``r`` → delta_alpha > 0; negative ``r`` → delta_beta > 0.
        Zero ``r`` → both zero.
    """
    r = max(-1.0, min(1.0, float(correlation)))
    w = max(0.0, min(1.0, float(weight)))
    magnitude = min(MAX_DELTA, abs(r) * w * MAX_DELTA)
    if r > 0:
        return (magnitude, 0.0)
    if r < 0:
        return (0.0, magnitude)
    return (0.0, 0.0)


def get_arm_prior_adjustment(
    niche_id: str,
    feature: str,
    weight: float = _DEFAULT_WEIGHT,
) -> tuple[float, float] | None:
    """Consumer-side helper: look up correlation for ``feature`` in
    ``niche_id``'s latest B.2 artifact, convert to a prior delta.

    Returns ``None`` when:
    * Flag off
    * No artifact for this niche
    * Feature not in the correlation map (unknown feature key)

    Returns ``(0.0, 0.0)`` when the feature IS in the map but has
    correlation 0 — this is an informative zero (feature exists but
    doesn't correlate with view velocity in this niche's top-100).

    Caller integrates like::

        adj = get_arm_prior_adjustment("gaming", "title_ends_with_question")
        if adj is not None:
            alpha += adj[0]
            beta += adj[1]

    Same "informative-None vs informative-zero" discipline as
    ``trend_anticipation._signal_creator_upload_lead``.
    """
    correlations = load_correlations(niche_id)
    if correlations is None:
        return None

    r = correlations.get(feature)
    if r is None:
        return None

    return correlation_to_prior_delta(r, weight)


__all__ = [
    "MAX_DELTA",
    "correlation_to_prior_delta",
    "get_arm_prior_adjustment",
    "load_correlations",
]
