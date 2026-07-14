"""Transformation bandit observability API (PR 14, 2026-07-05).

Surface for the intelligent transformation sprint (see memory:
sprint-intelligent-transformation-commit-2026-07-05). Reads the
``bandit_arms`` table filtered to ``arm_type = 'transformation'`` and
returns per-niche + per-dimension top-arm data for the Mission
Control cards to render.

Same "observation before consumer wire" pattern as the other
intervention cards — the operator can eyeball posteriors + reward
before flipping ``GENLAB_INTELLIGENT_TRANSFORM_ENABLED``.

Payload shape (single endpoint serves all 5 cards' data):

    {
      "status": "success",
      "data": {
        "generated_at": "2026-07-05T21:00:00+00:00",
        "flag_enabled": false,
        "niches": {
          "gaming": {
            "music_mood": [
              {"value": "cinematic", "rate": 0.65, "n_obs": 12,
               "alpha": 8.0, "beta": 4.0,
               "arm_id": "transform__music_mood__cinematic"},
              ...  # top 3 by rate
            ],
            "hook_framing": [...],
            ...  # all 11 dimensions
          },
          ...
        }
      }
    }

Empty niches (no arms registered yet for that niche) are absent from
the response. Frontend renders as "No transformation arms yet".
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "transformation_bandit_api",
    __name__,
    url_prefix="/api/v1/transformation-bandit",
)


_ARM_PREFIX = "transform__"
_TOP_N_PER_DIMENSION = 3


def _flag_enabled() -> bool:
    """Whether the render-side transformation is currently active.

    2026-07-14: replaced inline hardcoded truthy-set with canonical
    env_true helper. Prior inline set duplicated env_true's contract
    with no way to keep them synchronized; a future addition to
    env_true (e.g. accepting "y" / "t") would have silently diverged.
    """
    from genlab_core.settings import env_true

    return env_true("GENLAB_INTELLIGENT_TRANSFORM_ENABLED")


def _parse_arm_id(arm_id: str) -> tuple[str, str] | None:
    """Parse ``transform__<dim>__<value>`` → ``(dim, value)`` or None."""
    if not arm_id.startswith(_ARM_PREFIX):
        return None
    rest = arm_id[len(_ARM_PREFIX):]
    parts = rest.split("__", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def _load_transformation_arms() -> list[dict[str, Any]]:
    """Query bandit_arms for arms with arm_type='transformation'.

    Returns a list of ``{niche_id, dimension, value, alpha, beta,
    n_plays, arm_id}`` dicts. Returns empty list on any DB error —
    endpoint responds with ``data: null`` in that case rather than
    surfacing a 500 to the frontend (matches sibling cards).
    """
    try:
        # Lazy import to avoid module-load-time DB coupling — matches
        # the pattern the other API modules use for BacklogClient.
        from genlab_core.http.backlog_client import BacklogClient

        client = BacklogClient()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "transformation_bandit: BacklogClient construction failed: %s", exc
        )
        return []

    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        logger.warning("transformation_bandit: no bandit_arms proxy")
        return []

    try:
        raw_items = proxy.all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("transformation_bandit: proxy.all() failed: %s", exc)
        return []

    out: list[dict[str, Any]] = []
    for item in raw_items:
        fields = item.get("fields", item)
        arm_type = fields.get("arm_type") or ""
        arm_id = fields.get("arm_id") or ""
        # Filter to transformation arms only. Backfill scripts may
        # have set arm_type; historical rows fall through if arm_id
        # matches the composite prefix (belt AND suspenders).
        is_transform = (
            arm_type == "transformation" or arm_id.startswith(_ARM_PREFIX)
        )
        if not is_transform:
            continue
        parsed = _parse_arm_id(arm_id)
        if parsed is None:
            continue
        dim, value = parsed
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
                "dimension": dim,
                "value": value,
                "alpha": alpha,
                "beta": beta,
                "n_plays": n_plays,
                "arm_id": arm_id,
            }
        )
    return out


def _shape_response(arms: list[dict[str, Any]]) -> dict[str, Any]:
    """Group arms by niche + dimension, keep top-N by rate per group.

    ``rate = alpha / (alpha + beta)`` is the Beta posterior mean —
    the natural "expected reward" for this arm under the current
    observations.

    ``n_obs = alpha + beta - 2`` counts observations beyond the
    Beta(1,1) uniform prior. An arm with alpha=1, beta=1 has 0 obs
    (cold-start); alpha=6, beta=4 has 8 obs.
    """
    by_niche_dim: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for arm in arms:
        niche = arm["niche_id"]
        dim = arm["dimension"]
        alpha = arm["alpha"]
        beta = arm["beta"]
        total = alpha + beta
        rate = alpha / total if total > 0 else 0.0
        n_obs = max(0, int(round(total - 2.0)))
        by_niche_dim[niche][dim].append(
            {
                "value": arm["value"],
                "rate": round(rate, 4),
                "n_obs": n_obs,
                "alpha": round(alpha, 3),
                "beta": round(beta, 3),
                "arm_id": arm["arm_id"],
            }
        )

    # Keep top-N per dimension by rate; break ties by n_obs (prefer
    # more-observed arms in ties).
    shaped_niches: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for niche, dims in by_niche_dim.items():
        shaped_dims: dict[str, list[dict[str, Any]]] = {}
        for dim, arms_in_dim in dims.items():
            arms_in_dim.sort(
                key=lambda a: (a["rate"], a["n_obs"]),
                reverse=True,
            )
            shaped_dims[dim] = arms_in_dim[:_TOP_N_PER_DIMENSION]
        shaped_niches[niche] = shaped_dims

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "flag_enabled": _flag_enabled(),
        "niches": shaped_niches,
    }


@bp.route("/summary", methods=["GET"])
def get_summary():
    """Return per-niche transformation-bandit summary for MC cards.

    Response:
        {"status": "success", "data": {...}}

    ``data.niches`` empty when:
      * No transformation arms registered yet (pre-PR-3-run install)
      * BacklogClient / bandit_arms proxy unavailable
      * Postgres migration not yet applied (arm_type column absent)

    Frontend renders any of these as "No transformation arms yet —
    run scripts/register_transformation_arms.py after deploy."
    """
    arms = _load_transformation_arms()
    payload = _shape_response(arms)
    return jsonify({"status": "success", "data": payload})
