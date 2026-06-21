"""
Warm-start transfer for new niches.

Method (Kveton et al., ICML 2021):
  1. Load source niche's arm posteriors (alpha, beta per arm)
  2. Scale by confidence: alpha_new = 1 + confidence * (alpha_src - 1)
  3. Add exploration inflation (default 0.5) to both alpha and beta

The exploration inflation prevents transferred priors from causing
over-exploitation — the new niche needs to verify source patterns.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TRANSFER_MATRIX: dict[str, dict[str, float]] = {
    "sports": {"gaming": 0.45},
    "movies": {"ai_creators": 0.35},
    "anime": {"ai_creators": 0.30},
}

EXPLORATION_INFLATION = 0.5


def compute_warm_start_prior(
    source_alpha: float,
    source_beta: float,
    confidence: float,
    exploration_inflation: float = EXPLORATION_INFLATION,
) -> tuple[float, float]:
    """Scale source posterior toward uniform by (1 - confidence), add inflation."""
    alpha_warm = 1.0 + confidence * (source_alpha - 1.0)
    beta_warm = 1.0 + confidence * (source_beta - 1.0)
    alpha_warm += exploration_inflation
    beta_warm += exploration_inflation
    return max(1.0, alpha_warm), max(1.0, beta_warm)


def apply_warm_start(
    target_niche: str,
    source_arms: dict[str, tuple[float, float]],
    target_proxy,
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    """Apply warm-start priors to target niche's arms.

    Only updates arms at Beta(1,1) — never overwrites real observations.

    Args:
        target_niche: Niche ID to warm-start (e.g. "sports").
        source_arms: {arm_id: (alpha, beta)} from the source niche.
        target_proxy: GraphTableProxy (or mock) pointed at the target
                      niche's BanditArms list.
        dry_run: If True, compute priors but don't write to SharePoint.

    Returns:
        {arm_id: "updated" | "skipped_has_obs" | "dry_run"} for each
        source arm processed.
    """
    from genlab_core.learning.arm_loader import load_all_arms, save_arm

    transfers = TRANSFER_MATRIX.get(target_niche, {})
    if not transfers:
        logger.warning("[meta_prior] no transfer mapping for target niche '%s'", target_niche)
        return {}

    source_niche = next(iter(transfers))
    confidence = transfers[source_niche]

    # Load existing target arms (may be empty if list doesn't exist yet)
    target_arms = load_all_arms(target_proxy, target_niche)
    results: dict[str, str] = {}

    for arm_id, (src_a, src_b) in source_arms.items():
        # Check if target already has observations
        if arm_id in target_arms:
            t_a, t_b = target_arms[arm_id]
            total_obs = (t_a - 1.0) + (t_b - 1.0)  # Subtract priors
            if total_obs > 2.0:
                results[arm_id] = "skipped_has_obs"
                continue

        new_alpha, new_beta = compute_warm_start_prior(src_a, src_b, confidence)

        if dry_run:
            logger.info(
                "[meta_prior] DRY RUN %s → alpha=%.2f beta=%.2f (confidence=%.2f)",
                arm_id,
                new_alpha,
                new_beta,
                confidence,
            )
            results[arm_id] = "dry_run"
            continue

        # Extract content_type and platform from arm_id (format: content_type__platform)
        parts = arm_id.split("__", 1)
        ct = parts[0] if len(parts) > 0 else ""
        pl = parts[1] if len(parts) > 1 else ""

        # Pass niche_id so the upsert lookup uses the composite
        # UNIQUE(niche_id, arm_id) index (perf fix 2026-06-21).
        save_arm(target_proxy, arm_id, new_alpha, new_beta, ct, pl, niche_id=target_niche)
        results[arm_id] = "updated"

    updated_count = sum(1 for v in results.values() if v == "updated")
    skipped_count = sum(1 for v in results.values() if v == "skipped_has_obs")
    logger.info(
        "[meta_prior] %s: %d updated, %d skipped (source=%s, confidence=%.2f)",
        target_niche,
        updated_count,
        skipped_count,
        source_niche,
        confidence,
    )
    return results
