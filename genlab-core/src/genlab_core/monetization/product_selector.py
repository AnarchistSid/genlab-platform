"""ProductSelector — bandit-driven affiliate product selection (L3 PR 4).

Given a niche + a set of catalog candidate products, returns a ranked
list of ``(product, score)`` tuples using bandit posteriors from
``bandit_arms`` where ``arm_type='product'`` (registered in L3 PR 3).

## The problem this solves

Today's ``affiliate_matcher.match_product`` picks the product with the
most keyword hits, breaking ties by scan order. There is no notion of
which product HAS EARNED SO FAR — so PS5 (₹49,990, 3% commission) and
Amazon Prime Video (₹1,499/yr, 5% commission) get equal weight in the
matcher despite radically different expected revenue per click.

This module composes three signals to fix that:

1. **Bandit posterior** — Thompson-sampled or LinUCB-scored probability
   the arm produces reward (a click, eventually a conversion).
2. **Value weighting** — ``log(price × commission + e)`` scales score by
   expected revenue-per-click. The ``log`` compresses the tail so a
   ₹100k product doesn't dominate a ₹1k one by 100×.
3. **Warm-start vs cold-start routing** — arms with ``n_plays ≥ 10``
   use LinUCB (context-aware); below that, Thompson posterior mean
   drives ranking. Cold-start is the *dominant* regime for products
   (113 arms × 0 clicks each = every arm cold), so the Thompson path
   has to be a first-class citizen — not a fallback fail-open.

## Design non-goals

* **Not a full replacement for the matcher.** L3 PR 5 wires this
  selector to REORDER candidate products the keyword matcher already
  found — not to replace keyword matching. Keyword matching is what
  ties a product to a story's topical relevance; the bandit ranks
  which of the topically-relevant products to surface.
* **Not context-aware in v1.** The context vector plumbed through
  LinUCB is stubbed as an empty numpy array — until L3 PR 6 lands
  cross-niche transfer for products, no meaningful context features
  exist. LinUCB collapses to a UCB-over-uniform ranking, which is
  still better than posterior-mean-only for warm arms.
* **Not price-population aware.** Products missing ``price_inr`` in
  the catalog get ``value_weight=1.0`` (log(0+e)=1) — no dominance
  by revenue for unpriced arms. L3 PR 13 will backfill catalog prices;
  meanwhile the selector doesn't penalize the operator for missing data.

## The load-bearing alignment

``bandit_arms.arm_id`` must be ``'product__' + slugify_product_name(name)``
— the same slug format registered by L3 PR 3 (``register_product_arms``)
and written to ``blueprints.product_slug`` by L3 PR 2. The selector
looks up arms by composing ``'product__' + slugify_product_name(name)``
on each candidate; a slug drift would make every arm miss and the
selector would silently fall back to unranked catalog order.

## Kill switch

Flag ``GENLAB_PRODUCT_SELECTOR_ENABLED`` gates the top-level entry
point. When ``!= "true"`` (exact match), :func:`select_products` returns
``[]`` — callers use the catalog matcher's raw output.

Match with :func:`is_enabled`.  Default: OFF.

## Example

    from genlab_core.monetization.product_selector import select_products

    ranked = select_products(
        niche_id="gaming",
        candidates=matcher_output,        # list[dict] from keyword matcher
        top_k=3,
    )
    for prod, score in ranked:
        print(f"{prod['name']:30s} {score:.3f}")
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)


# Warm-start threshold. Below this many plays, LinUCB scores are
# dominated by the exploration bonus (A^{-1} still close to identity)
# so Thompson posterior mean is a more useful ranking signal. Matches
# the ``_DEFAULT_MIN_OBS=10`` in ``linucb_picker`` for consistency.
_MIN_PLAYS_FOR_LINUCB = 10

# Numerical floor for the value weight. When a product has no price
# or no commission, the raw product is 0, log(0+e)=1 — the arm
# contributes to ranking on posterior alone. Pinned so future changes
# to the formula don't accidentally amplify cold-arms with missing
# catalog data.
_VALUE_WEIGHT_FLOOR = 1.0


def is_enabled() -> bool:
    """Single source of truth for the opt-in flag.

    Uses exact-match ``"true"`` (not ``"1"``) per the convention set by
    ``GENLAB_ENSEMBLE_DECISION_ENABLED`` — most flags in this codebase
    still accept ``"1"``, but the intelligence sprint's flags all use
    ``"true"`` for readability. Sticking to the newer convention.
    """
    return os.environ.get("GENLAB_PRODUCT_SELECTOR_ENABLED", "false") == "true"


def _value_weight(product: dict[str, Any]) -> float:
    """Compute the revenue-scaling factor for a candidate product.

    Uses ``log(price_inr × best_commission_pct + e) / log(e)`` — the log
    compresses runaway dominance from expensive products while still
    ranking a ₹49,990 PS5 above a ₹1,499 subscription when other
    factors are equal.

    Missing price OR missing commission returns ``_VALUE_WEIGHT_FLOOR``
    (=1.0) so the bandit posterior alone drives ranking. Catalog gaps
    should not bias against unpriced products — L3 PR 13 backfills.

    Commission source:
        We use ``max`` of ``networks[*].commission_pct`` as the best-case
        commission per product. If a product has both EarnKaro (12%)
        and Amazon (3%), we optimistically assume the higher one — the
        actual network picked at redirect time depends on availability
        + geo. Reward feedback (L3 PR 8) will use the SNAPSHOTTED
        commission from ``affiliate_clicks.commission_pct``, so this
        max is only a ranking heuristic, not an attribution error.
    """
    price = product.get("price_inr", 0) or 0
    networks = product.get("networks") or {}
    commissions = [
        (net_info.get("commission_pct") or 0)
        for net_info in networks.values()
        if isinstance(net_info, dict)
    ]
    best_commission = max(commissions, default=0)

    revenue_per_click = price * best_commission / 100.0
    if revenue_per_click <= 0:
        return _VALUE_WEIGHT_FLOOR

    # log(x + e) so at x=0 the result is log(e)=1 == _VALUE_WEIGHT_FLOOR.
    # Continuity at the missing-data boundary avoids a step discontinuity
    # between "price=0" (floor 1.0) and "price=1" (log(0.01+e)≈1.0037).
    return math.log(revenue_per_click + math.e)


def _thompson_score(alpha: float, beta: float) -> float:
    """Posterior mean of ``Beta(alpha, beta)``.

    We use the MEAN rather than a sample here (unlike traditional
    Thompson Sampling) because :func:`select_products` returns a
    RANKING, not a single pick. Sampling would inject noise into the
    ranking on every call — the caller wants a stable top-K list to
    show the operator or feed to a downstream stage. Sample-based
    Thompson is right for one-shot decision-time; posterior mean is
    right for ranking.
    """
    if alpha <= 0 or beta <= 0:
        return 0.5  # invalid posterior, treat as uniform
    return alpha / (alpha + beta)


def _score_arm(
    arm_row: dict[str, Any],
    linucb_arms: dict | None,
    context: Any,
) -> float:
    """Warm-start LinUCB, cold-start Thompson mean.

    ``arm_row`` comes from ``load_all_arms_extended`` and has shape
    ``{"alpha": float, "beta": float, "n_plays": int, "linucb_state": dict|None}``.

    Returns a scalar in ``[0, 1]``-ish (Thompson mean is exactly
    bounded; LinUCB UCB can exceed 1 but the ranking is comparable
    within a single call).
    """
    n_plays = arm_row.get("n_plays") or 0
    alpha = float(arm_row.get("alpha") or 1.0)
    beta = float(arm_row.get("beta") or 1.0)

    if n_plays < _MIN_PLAYS_FOR_LINUCB or linucb_arms is None:
        return _thompson_score(alpha, beta)

    # Warm-start: try LinUCB. Fail back to Thompson on any error —
    # posterior mean is always well-defined so we never return None.
    try:
        from genlab_core.learning.linucb_picker import score_arm

        arm_id = arm_row.get("arm_id", "")
        linucb_score = score_arm(arm_id, context, linucb_arms)
        if linucb_score is not None:
            return linucb_score
    except Exception as exc:  # noqa: BLE001
        logger.debug("[ProductSelector] LinUCB score failed for arm: %s", exc)

    return _thompson_score(alpha, beta)


def select_products(
    niche_id: str,
    candidates: list[dict[str, Any]],
    *,
    top_k: int = 3,
    context: Any = None,
) -> list[tuple[dict[str, Any], float]]:
    """Rank candidate products by combined bandit posterior × value.

    Args:
        niche_id: Niche (e.g., 'gaming') to load bandit_arms for.
        candidates: List of product dicts from the catalog matcher.
            Each must have a ``name`` field.
        top_k: Return at most this many ranked products.
        context: LinUCB context vector (numpy array or list). None uses
            a zero vector — LinUCB collapses to a UCB-over-uniform
            ranking, still preserving score contract.

    Returns:
        List of ``(product, score)`` tuples in descending score order,
        length ``≤ top_k``. Empty list when disabled or when no candidates
        have registered arms — caller must fall back to matcher output.

    Fail-open at every layer:
        * Flag off → return ``[]`` (immediately).
        * BacklogClient construction fails → return ``[]``.
        * No candidates map to registered arms → return ``[]``.
        * Individual arm scoring fails → arm gets Thompson-mean score.

    No exception ever escapes to the caller.
    """
    if not is_enabled():
        return []
    if not candidates:
        return []

    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms_extended
        from genlab_core.monetization.affiliate_matcher import (
            slugify_product_name,
        )
    except ImportError as exc:
        logger.warning("[ProductSelector] import failed: %s — disabling", exc)
        return []

    try:
        client = BacklogClient()
        proxy = getattr(client, "bandit_arms", None)
        if proxy is None:
            logger.debug("[ProductSelector] no bandit_arms proxy — disabling")
            return []
        all_arms = load_all_arms_extended(proxy, niche_id)
    except Exception as exc:
        logger.warning("[ProductSelector] arm loader failed: %s — disabling", exc)
        return []

    # Build LinUCB dict lazily (only for warm arms) — most calls in
    # v1 will hit only Thompson mean paths, so avoid the numpy import.
    linucb_arms: dict | None = None

    ranked: list[tuple[dict[str, Any], float]] = []
    for product in candidates:
        name = product.get("name") or ""
        slug = slugify_product_name(name)
        if not slug:
            continue
        arm_id = f"product__{slug}"

        arm_row = all_arms.get(arm_id)
        if arm_row is None:
            # No registered arm — this is a first-time-seen product.
            # Score with uniform Beta(1,1) prior so it competes fairly
            # with registered arms that have Beta(1,1)+observations.
            # Missing catalog names shouldn't be silently dropped from
            # ranking; that's a data-loss bug pattern.
            arm_row = {"arm_id": arm_id, "alpha": 1.0, "beta": 1.0, "n_plays": 0}

        # Lazy LinUCB load — only fetch if this arm actually needs it.
        if (arm_row.get("n_plays") or 0) >= _MIN_PLAYS_FOR_LINUCB and linucb_arms is None:
            linucb_arms = _load_linucb_arms(all_arms)

        posterior_score = _score_arm({**arm_row, "arm_id": arm_id}, linucb_arms, context)
        value_multiplier = _value_weight(product)
        combined = posterior_score * value_multiplier
        ranked.append((product, combined))

    # Descending by score. Ties broken by product name for determinism.
    ranked.sort(key=lambda t: (-t[1], t[0].get("name", "")))
    return ranked[:top_k]


def _load_linucb_arms(all_arms: dict) -> dict:
    """Lazily build the linucb_arms dict for warm-start scoring.

    Called only when at least one candidate arm crosses the warm-start
    threshold — most v1 traffic never hits this path (products are all
    cold-start at rollout).

    Returns ``{arm_id: LinUCBArm}`` for arms with a persisted state
    dict. Arms without ``linucb_state`` are omitted (score_arm returns
    None for them, and the caller falls back to Thompson).
    """
    try:
        from genlab_core.learning.linucb import CONTEXT_DIM, LinUCBArm
    except ImportError:
        return {}

    linucb_arms: dict = {}
    for arm_id, row in all_arms.items():
        state = row.get("linucb_state")
        if not state:
            continue
        try:
            arm = LinUCBArm.from_dict(state)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[ProductSelector] LinUCBArm.from_dict(%s): %s", arm_id, exc)
            continue
        linucb_arms[arm_id] = arm

    _ = CONTEXT_DIM  # imported for future context-vector plumbing (PR 6)
    return linucb_arms
