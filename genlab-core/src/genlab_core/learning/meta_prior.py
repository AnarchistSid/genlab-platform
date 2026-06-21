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


def bootstrap_niche_warm_start(
    niche_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    """Apply Kveton 2021 warm-start priors to a newly-scaffolded niche.

    Auto-discovers the source niche from ``TRANSFER_MATRIX``, loads its
    arm posteriors via ``BacklogClient``, and calls ``apply_warm_start``.
    Designed for the niche-scaffolding flow (Lever D3): when a new
    niche spins up it gets free priors instead of starting cold from
    Beta(1, 1) on every arm.

    Returns the same per-arm result map as ``apply_warm_start``, or an
    empty dict when:
      - ``niche_id`` has no TRANSFER_MATRIX entry (e.g. a brand-new
        unrelated niche the operator scaffolded — they can extend the
        matrix manually if they want transfer)
      - BacklogClient fails to construct (PG unavailable, etc.)
      - Source niche has no arms yet (nothing to transfer)

    This is best-effort by design: a missing or failed warm-start
    MUST NOT fail niche scaffolding. The operator can re-run via
    ``python -m genlab_core.learning.meta_prior --target-niche X``
    once the source niche has accumulated data.

    Pre-Lever-D3 the ``apply_warm_start`` function existed but had
    zero production callers — Kveton 2021 transfer math sat dormant.
    """
    if niche_id not in TRANSFER_MATRIX:
        logger.info(
            "[meta_prior] no transfer mapping for niche %r — skipping warm-start "
            "(extend TRANSFER_MATRIX if you want this niche to inherit priors)",
            niche_id,
        )
        return {}

    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import load_all_arms

        client = BacklogClient()
        proxy = getattr(client, "bandit_arms", None)
        if proxy is None:
            logger.warning("[meta_prior] no bandit_arms proxy on client — skipping warm-start")
            return {}

        # The TRANSFER_MATRIX entry is {source_niche: confidence}; we
        # only support 1 source per target in this iteration (Kveton's
        # original method supports multi-source but our matrix is sparse).
        source_niche = next(iter(TRANSFER_MATRIX[niche_id]))
        source_arms = load_all_arms(proxy, source_niche)
        if not source_arms:
            logger.info(
                "[meta_prior] source niche %r has no arms yet — nothing to transfer to %r",
                source_niche,
                niche_id,
            )
            return {}

        return apply_warm_start(
            target_niche=niche_id,
            source_arms=source_arms,
            target_proxy=proxy,
            dry_run=dry_run,
        )
    except Exception as exc:
        # Best-effort: never fail the caller (scaffold_niche) on a
        # transfer-math failure. Log + return empty so the operator
        # sees what happened and can re-run manually.
        logger.warning(
            "[meta_prior] warm-start bootstrap failed for %r: %s (manual re-run available)",
            niche_id,
            exc,
        )
        return {}


if __name__ == "__main__":
    # Lever D3 (2026-06-21): ops CLI entrypoint. Lets operators re-run
    # warm-start after the source niche has accumulated more data, or
    # bootstrap a previously-scaffolded niche that pre-dated this wire.
    #
    # Usage:
    #   uv run python -m genlab_core.learning.meta_prior --target-niche sports
    #   uv run python -m genlab_core.learning.meta_prior --target-niche sports --dry-run
    import argparse

    parser = argparse.ArgumentParser(description="Apply Kveton 2021 warm-start priors to a niche.")
    parser.add_argument("--target-niche", required=True, help="Niche to warm-start (e.g. sports)")
    parser.add_argument("--dry-run", action="store_true", help="Compute priors without writing")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    results = bootstrap_niche_warm_start(args.target_niche, dry_run=args.dry_run)
    if not results:
        print(f"No warm-start applied for {args.target_niche!r} — see log for reason.")
    else:
        print(f"\nWarm-start results for {args.target_niche!r}:")
        for arm, outcome in results.items():
            print(f"  {arm}: {outcome}")
