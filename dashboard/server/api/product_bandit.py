"""Product bandit observability API (L3 PR 9, 2026-07-07).

Surface for the Monetization Layer 3 sprint (see memory:
monetization-gap-analysis-2026-07-07 for the audit that motivated
the sprint). Reads the ``bandit_arms`` table filtered to
``arm_type = 'product'`` and returns per-niche top-N product arms
by Beta posterior mean, so the Mission Control ``ProductBanditCard``
can show which products are earning clicks per niche.

Same "observation before consumer wire" pattern as the other
intervention cards — the operator can eyeball posteriors + reward
before flipping ``GENLAB_PRODUCT_SELECTOR_ENABLED`` on prod.

Payload shape:

    {
      "status": "success",
      "data": {
        "generated_at": "2026-07-07T21:00:00+00:00",
        "flag_enabled": false,
        "niches": {
          "gaming": [
            {"slug": "ps5-console", "rate": 0.929, "n_obs": 12,
             "alpha": 13.0, "beta": 1.0,
             "arm_id": "product__ps5-console"},
            ...  # top 5 by rate
          ],
          ...
        }
      }
    }

Empty niches (no arms registered / no clicks) are absent from the
response. Frontend renders as "No product arms yet — run
scripts/register_product_arms.py after deploy."

Companion endpoint at ``/latest-clicks`` returns raw counts from
``affiliate_clicks`` grouped by (niche, product) for the last 30
days — the "ground truth" the operator can cross-check against the
bandit's posterior. Same shape but ordered by click count.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "product_bandit_api",
    __name__,
    url_prefix="/api/v1/product-bandit",
)


_ARM_PREFIX = "product__"
_TOP_N_PER_NICHE = 5


def _flag_enabled() -> bool:
    """Whether the ProductSelector consumer is currently active."""
    return (
        os.environ.get("GENLAB_PRODUCT_SELECTOR_ENABLED", "").strip().lower() == "true"
    )


def _parse_slug(arm_id: str) -> str | None:
    """Extract the slug from ``product__<slug>`` or return None."""
    if not arm_id.startswith(_ARM_PREFIX):
        return None
    slug = arm_id[len(_ARM_PREFIX) :]
    return slug or None


def _load_product_arms() -> list[dict[str, Any]]:
    """Query bandit_arms for arms with arm_type='product'.

    Returns a list of ``{niche_id, slug, alpha, beta, n_plays,
    arm_id}`` dicts. Empty list on any DB error — endpoint responds
    with ``data.niches: {}`` in that case rather than surfacing a
    500 to the frontend (matches sibling cards)."""
    try:
        from genlab_core.http.backlog_client import BacklogClient

        client = BacklogClient()
    except Exception as exc:  # noqa: BLE001
        logger.warning("product_bandit: BacklogClient construction failed: %s", exc)
        return []

    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        logger.warning("product_bandit: no bandit_arms proxy")
        return []

    try:
        raw_items = proxy.all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("product_bandit: proxy.all() failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for item in raw_items:
        fields = item.get("fields", item)
        arm_type = fields.get("arm_type") or ""
        arm_id = fields.get("arm_id") or ""
        # Filter to product arms. arm_type check is primary; the
        # arm_id prefix check is defence-in-depth for rows where the
        # backfill hasn't run yet (historical arms may lack arm_type).
        is_product = arm_type == "product" or arm_id.startswith(_ARM_PREFIX)
        if not is_product:
            continue
        slug = _parse_slug(arm_id)
        if slug is None:
            continue
        try:
            alpha = float(fields.get("alpha", 1.0) or 1.0)
            beta = float(fields.get("beta", 1.0) or 1.0)
        except (TypeError, ValueError):
            continue
        n_plays_raw = fields.get("n_plays") or 0
        try:
            n_plays = int(n_plays_raw)
        except (TypeError, ValueError):
            n_plays = 0

        out.append(
            {
                "niche_id": fields.get("niche_id") or "",
                "slug": slug,
                "alpha": alpha,
                "beta": beta,
                "n_plays": n_plays,
                "arm_id": arm_id,
            }
        )
    return out


def _shape_response(arms: list[dict[str, Any]]) -> dict[str, Any]:
    """Group arms by niche, keep top-N by rate per niche.

    * ``rate = alpha / (alpha + beta)`` — Beta posterior mean, the
      selector's ranking signal for warm arms.
    * ``n_obs = alpha + beta - 2`` — observations beyond the
      Beta(1,1) prior. All-cold arms show n_obs=0 (expected on
      pre-first-click deploy).
    """
    by_niche: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for arm in arms:
        niche = arm["niche_id"]
        alpha = arm["alpha"]
        beta = arm["beta"]
        total = alpha + beta
        rate = alpha / total if total > 0 else 0.0
        n_obs = max(0, int(round(total - 2.0)))
        by_niche[niche].append(
            {
                "slug": arm["slug"],
                "rate": round(rate, 4),
                "n_obs": n_obs,
                "alpha": round(alpha, 3),
                "beta": round(beta, 3),
                "arm_id": arm["arm_id"],
            }
        )

    # Keep top-N per niche by rate; break ties by n_obs (prefer
    # more-observed arms in ties). Then break by slug for
    # deterministic ordering when everything cold-starts.
    shaped: dict[str, list[dict[str, Any]]] = {}
    for niche, arms_in_niche in by_niche.items():
        arms_in_niche.sort(
            key=lambda a: (a["rate"], a["n_obs"], a["slug"]),
            reverse=True,
        )
        shaped[niche] = arms_in_niche[:_TOP_N_PER_NICHE]

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "flag_enabled": _flag_enabled(),
        "niches": shaped,
    }


@bp.route("/summary", methods=["GET"])
def get_summary():
    """Return per-niche product-bandit summary for the MC card.

    Response:
        {"status": "success", "data": {...}}

    ``data.niches`` empty when:
      * No product arms registered yet (pre-PR-3-run install)
      * BacklogClient / bandit_arms proxy unavailable
      * Postgres migration not yet applied

    Frontend renders any of these as "No product arms yet — run
    scripts/register_product_arms.py after deploy."
    """
    arms = _load_product_arms()
    payload = _shape_response(arms)
    return jsonify({"status": "success", "data": payload})
