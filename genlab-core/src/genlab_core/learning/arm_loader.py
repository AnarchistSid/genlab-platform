"""
Lightweight bandit arm loader for genlab-core.
Reads/writes BanditArm-like dicts from SharePoint lists.
Each niche has its own list: {NicheDisplayName}_BanditArms

Accepts a proxy object with `.all()`, `.create()`, `.update()` methods
matching the GraphTableProxy interface.

LinUCB state (A_matrix, b_vector) is persisted as a JSON string in the
``LinUCB_State`` column. When this field is empty the arm falls back to
Thompson Sampling (alpha/beta only).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# Map niche_id to SharePoint list display name
BANDIT_LIST_NAMES = {
    "gaming": "CriticalRush_BanditArms",
    "ai_creators": "BlackboxBrief_BanditArms",
    "sports": "ClutchWire_BanditArms",
    "movies": "SpliceReel_BanditArms",
    "anime": "FrameDrift_BanditArms",
}


def load_all_arms(proxy, niche_id: str) -> dict[str, tuple[float, float]]:
    """Load bandit arms for ``niche_id``. Returns {arm_id: (alpha, beta)}.

    Args:
        proxy: Object with ``.all()`` returning list of ``{id, fields}`` dicts.
               In the PG-backed deployment the proxy maps to the global
               ``bandit_arms`` table — RLS only filters when
               ``app.niche_id`` is set on the session, which is not
               guaranteed at every call site. So we always filter by
               ``niche_id`` in Python as a defense-in-depth measure.
        niche_id: Rows with a different ``niche_id`` field are dropped.
                  Rows with no ``niche_id`` field at all are kept (legacy
                  SharePoint single-list backend).
    """
    try:
        items = proxy.all()
        arms: dict[str, tuple[float, float]] = {}
        for item in items:
            fields = item.get("fields", item)
            row_niche = fields.get("niche_id")
            if row_niche and row_niche != niche_id:
                continue
            arm_id = fields.get("arm_id") or fields.get("Title") or ""
            alpha = float(fields.get("alpha") if "alpha" in fields else fields.get("Alpha", 1.0))
            beta = float(fields.get("beta") if "beta" in fields else fields.get("Beta", 1.0))
            if arm_id:
                arms[arm_id] = (alpha, beta)
        return arms
    except Exception as e:
        logger.warning("[arm_loader] failed to load arms for %s: %s", niche_id, e)
        return {}


def load_all_arms_extended(
    proxy,
    niche_id: str,
) -> dict[str, dict[str, Any]]:
    """Load bandit arms for ``niche_id`` with optional LinUCB state.

    Returns {arm_id: {"alpha": float, "beta": float, "linucb_state": dict|None}}.

    Same niche-filtering semantics as ``load_all_arms``: rows with a
    different ``niche_id`` are dropped; rows lacking the field (legacy
    backends) are kept.

    The ``LinUCB_State`` column stores a JSON string produced by
    ``LinUCBArm.to_dict()``. When the field is absent or empty the
    ``linucb_state`` value is None, signalling that the arm should
    use Thompson Sampling.
    """
    try:
        items = proxy.all()
        arms: dict[str, dict[str, Any]] = {}
        for item in items:
            fields = item.get("fields", item)
            row_niche = fields.get("niche_id")
            if row_niche and row_niche != niche_id:
                continue
            arm_id = fields.get("arm_id") or fields.get("Title") or ""
            if not arm_id:
                continue

            alpha = float(fields.get("alpha") if "alpha" in fields else fields.get("Alpha", 1.0))
            beta = float(fields.get("beta") if "beta" in fields else fields.get("Beta", 1.0))

            linucb_state: dict[str, Any] | None = None
            raw_state = fields.get("linucb_state") or fields.get("LinUCB_State") or ""
            if raw_state:
                try:
                    linucb_state = json.loads(raw_state)
                except (json.JSONDecodeError, TypeError):
                    logger.debug(
                        "[arm_loader] invalid LinUCB_State for arm %s, ignoring",
                        arm_id,
                    )

            arms[arm_id] = {
                "alpha": alpha,
                "beta": beta,
                "linucb_state": linucb_state,
            }
        return arms
    except Exception as e:
        logger.warning(
            "[arm_loader] failed to load extended arms for %s: %s",
            niche_id,
            e,
        )
        return {}


def save_arm(
    proxy,
    arm_id: str,
    alpha: float,
    beta: float,
    content_type: str = "",
    platform: str = "",
    linucb_state: dict[str, Any] | None = None,
    n_plays: int | None = None,
    niche_id: str = "",
) -> None:
    """Save a single bandit arm — upsert by ``(niche_id, arm_id)``.

    Args:
        n_plays: If provided, written as-is. If None (default), the
            existing row's n_plays is preserved on update; new rows
            get 0. Previously hardcoded to 0 on every write, which
            silently zeroed the column on every update.
        linucb_state: Optional LinUCB state dict from ``LinUCBArm.to_dict()``.
            Serialized as JSON into the ``LinUCB_State`` column.
        niche_id: When provided, the lookup is scoped to this niche
            (matches the ``UNIQUE(niche_id, arm_id)`` constraint added
            in migration ``i9d0e1f2g3h4``) and the value is written
            to the row's ``niche_id`` column on create. Without it the
            lookup falls back to ``arm_id``-only, preserving prior
            behavior for any caller that hasn't been updated yet.

    Perf (2026-06-21): the lookup was previously ``proxy.all() + Python
    next()`` — a full-table scan + linear search per call. Inside the
    reward loop at ``metric_collector.py:813`` this fired N+1 times per
    reward window (once for the outer scan, once per arm in save_arm).
    The switch to ``proxy.all(formula=...)`` uses the composite UNIQUE
    index (``bandit_arms_niche_id_arm_id_key``) for a single index probe
    per call. See ``[[feedback-defer-bandit-index-lookup]]`` for the
    pre-design verification of the constraint + formula→SQL translation.
    """
    fields: dict[str, Any] = {
        "arm_id": arm_id,
        "alpha": alpha,
        "beta": beta,
        "ContentType": content_type,
        "Platform": platform,
        "LastUpdated": datetime.now(UTC).isoformat(),
    }
    if niche_id:
        # Only set niche_id on the row when the caller passed it. Legacy
        # callers without niche_id continue to write NULL, matching prior
        # behavior — they won't suddenly start populating a column the
        # row was created without.
        fields["niche_id"] = niche_id
    if linucb_state is not None:
        fields["linucb_state"] = json.dumps(linucb_state)
    try:
        # Indexed lookup via the composite UNIQUE constraint. When the
        # caller passed ``niche_id`` we use the (niche_id, arm_id) pair
        # so cross-niche collisions are impossible. Without it we fall
        # back to arm_id-only (idx_ba_arm_id index), preserving the
        # legacy ambiguity for any caller that hasn't been migrated.
        if niche_id:
            existing = proxy.all(
                formula=f"AND({{arm_id}}='{arm_id}',{{niche_id}}='{niche_id}')",
                max_records=1,
            )
        else:
            existing = proxy.all(formula=f"{{arm_id}}='{arm_id}'", max_records=1)
        match = existing[0] if existing else None
        # Legacy SharePoint-backend rows used Title instead of arm_id —
        # the prior code matched either. The formula filter above won't
        # catch Title-only rows. We retry with a broader scan in the
        # narrow fallback case where formula returned nothing AND the
        # caller indicated this is a legacy row by passing no niche_id.
        # Keep the Python-side legacy fallback to avoid breaking any
        # niche still on the single-column UNIQUE(arm_id) layout.
        if match is None and not niche_id:
            legacy = proxy.all()
            match = next(
                (item for item in legacy if (item.get("fields", item)).get("Title", "") == arm_id),
                None,
            )
        if match:
            if n_plays is not None:
                fields["n_plays"] = n_plays
            # else: omit from update payload → preserve existing value
            proxy.update(match["id"], fields)
        else:
            fields["n_plays"] = n_plays if n_plays is not None else 0
            proxy.create(fields)
    except Exception as e:
        logger.warning("[arm_loader] save failed for %s: %s", arm_id, e)
