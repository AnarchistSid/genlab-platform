#!/usr/bin/env python3
"""Register bandit arms for the affiliate-product selector (L3 PR 3).

Reads ``genlab-core/config/affiliate_catalog.yaml`` +
``affiliate_seasonal.yaml`` and upserts one bandit arm per
``(niche, product)`` pair. Idempotent by design — safe to re-run;
existing arms preserve their posteriors.

Arm ID format:
    ``product__<slug>``

The slug is produced by :func:`slugify_product_name` (shipped in
L3 PR 2, ``genlab_core.monetization.affiliate_matcher``) — the SAME
helper that populates ``blueprints.product_slug``. This alignment is
load-bearing:

* ``bandit_arms.arm_id = 'product__nvidia-rtx-4090'``
* ``blueprints.product_slug = 'nvidia-rtx-4090'``
* ``affiliate_clicks.product_id = 'nvidia-rtx-4090'``

The reward wire (L3 PR 8) will JOIN these three via slug — so the
same slug function MUST be used at all three write-sites. Using a
different slug (or hand-coded slugs) here would break attribution
silently for every product that mismatches.

Examples:
    ``product__ps5-console``
    ``product__nvidia-rtx-4090``
    ``product__xbox-game-pass-ultimate``
    ``product__crunchyroll-premium``
    ``product__prime-day-ps5-console``     (from seasonal)
    ``product__black-friday-rtx-4090``     (from seasonal)

Per-arm metadata written to ``bandit_arms``:
    * ``alpha=1.0, beta=1.0`` — Beta(1,1) cold-start prior. Cross-niche
      hierarchical transfer (Intervention 2, extended for products in
      L3 PR 6) may warm-start on arm creation.
    * ``arm_type='product'`` — new arm classification for this sprint.
      Enables analytical queries (``SELECT * FROM bandit_arms WHERE
      arm_type='product' AND niche_id=?``) without parsing the
      composite arm_id string.
    * ``dimension='affiliate_product'`` — matches the multi-arm
      ``pending_feedback.arm_ids_by_dimension`` JSONB key that will
      route reward attribution in L3 PR 8.
    * ``dimension_value=<slug>`` — the specific product slug this arm
      represents. Enables Mission Control cards to render per-product
      posteriors without SQL LIKE matching on arm_id.

Usage:
    # Dry run — enumerate arms per niche without writing
    python scripts/register_product_arms.py --dry-run

    # Register arms for a single niche
    python scripts/register_product_arms.py --niche gaming

    # Register all 5 niches (production usage)
    python scripts/register_product_arms.py --niche all

Exit codes:
    0 — success (all niches processed OR dry-run completed)
    1 — argument error / config parse error / file missing
    2 — persister error (BacklogClient construction or write failure)

Sprint context: this is PR 3 of the 14-PR monetization Layer 3 sprint.
See ``docs/MONETIZATION-LAYER-3-DESIGN.md`` § Phase A.

Prerequisites:
    - Migration ``a8w9x0y1z2a3_monetization_l3_product_bandit_schema``
      (L3 PR 1) applied — adds ``blueprints.product_slug`` +
      ``affiliate_clicks.blueprint_id`` + ``commission_pct``.
    - Migration ``a7v8w9x0y1z2_intelligent_transformation_schema``
      applied — provides ``bandit_arms.arm_type`` + ``dimension`` +
      ``dimension_value`` columns (shipped by an earlier sprint).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

# Repo-relative default paths. Overridable via CLI flags for tests.
DEFAULT_CATALOG_PATH = "genlab-core/config/affiliate_catalog.yaml"
DEFAULT_SEASONAL_PATH = "genlab-core/config/affiliate_seasonal.yaml"

logger = logging.getLogger("register_product_arms")

# The 5 niches Gen Lab operates. Guardrail against catalog rows for
# unknown niches accidentally spawning arms — the catalog is
# hand-edited so a typo (``"movie"`` vs ``"movies"``) would silently
# create an orphan arm. Keep this list synchronized with
# ``arm_loader.BANDIT_LIST_NAMES``.
KNOWN_NICHES: frozenset[str] = frozenset(["gaming", "sports", "movies", "anime", "ai_creators"])


def _arm_id(slug: str) -> str:
    """Compose the arm_id string.

    Uses ``__`` (double underscore) as separator — matches the
    convention established by ``register_transformation_arms`` and
    ``bandit_platform_split.platform_arm_id()``.

    Slugs that already contain ``__`` would silently corrupt the
    composite parse — this raises rather than accepting them. The
    canonical ``slugify_product_name`` never produces ``__`` (its
    output only contains ``[a-z0-9-]``), so this guard only triggers
    if a caller hands us a pre-slugified string with the wrong
    format.
    """
    if not slug:
        raise ValueError("empty slug — did slugify_product_name receive empty input?")
    if "__" in slug:
        raise ValueError(
            f"product slug {slug!r} contains '__' separator — would corrupt composite arm_id parse."
        )
    return f"product__{slug}"


def enumerate_arms_for_niche(
    niche_id: str,
    catalog: dict,
    seasonal: dict,
) -> Iterable[tuple[str, str]]:
    """Yield ``(arm_id, slug)`` tuples for a niche.

    Sources:
        1. ``catalog["niches"][niche_id]["products"]``
        2. ``seasonal["events"][*]["products"]`` — because seasonal
           events aren't niche-tagged in the current YAML shape, we
           include seasonal products IN EVERY niche's arm set. This
           mirrors ``affiliate_matcher._match_products``'s
           merge behavior — the matcher considers seasonal products
           for every niche, so the bandit must have arms for them
           in every niche too. If seasonal ever gains a per-niche
           filter, tighten this loop accordingly.

    Deduplication: a product with the same slug listed in both
    catalog and seasonal (or under two seasonal events) yields ONE
    arm — the bandit posterior aggregates all occurrences of a
    product regardless of surface path.
    """
    from genlab_core.monetization.affiliate_matcher import slugify_product_name

    slugs_seen: set[str] = set()

    # ── catalog products (niche-scoped) ────────────────────────────
    niche_data = (catalog.get("niches") or {}).get(niche_id) or {}
    for product in niche_data.get("products") or []:
        name = product.get("name") or ""
        slug = slugify_product_name(name)
        if not slug or slug in slugs_seen:
            continue
        slugs_seen.add(slug)
        yield _arm_id(slug), slug

    # ── seasonal products (shared across niches) ───────────────────
    for event in seasonal.get("events") or []:
        for product in event.get("products") or []:
            name = product.get("name") or ""
            slug = slugify_product_name(name)
            if not slug or slug in slugs_seen:
                continue
            slugs_seen.add(slug)
            yield _arm_id(slug), slug


def _load_yaml(path: Path) -> dict:
    """Load a YAML file into a dict. Missing file → empty dict.

    Missing catalog is a hard error (nothing to register); missing
    seasonal is soft (no seasonal events currently running is a
    legitimate steady state). Callers pass ``required=True`` for
    catalog.
    """
    import yaml

    if not path.is_file():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def register_arms_for_niche(
    niche_id: str,
    catalog: dict,
    seasonal: dict,
    *,
    dry_run: bool = False,
) -> dict:
    """Register all product arms for a niche.

    Returns stats dict:
        {
            "niche_id": str,
            "total": int — total arms enumerated,
            "registered": int — arms actually written (0 if dry_run),
            "skipped": int — arms that failed to write,
            "dry_run": bool,
        }
    """
    if niche_id not in KNOWN_NICHES:
        raise ValueError(
            f"unknown niche {niche_id!r} — not in KNOWN_NICHES "
            f"({sorted(KNOWN_NICHES)}). If a new niche was added, "
            f"update KNOWN_NICHES + BANDIT_LIST_NAMES in arm_loader."
        )

    arms = list(enumerate_arms_for_niche(niche_id, catalog, seasonal))

    stats = {
        "niche_id": niche_id,
        "total": len(arms),
        "registered": 0,
        "skipped": 0,
        "dry_run": dry_run,
    }

    if not arms:
        logger.warning(
            "[register_product_arms] niche %r: no products in catalog + no seasonal — "
            "nothing to register",
            niche_id,
        )
        return stats

    if dry_run:
        logger.info(
            "[register_product_arms] DRY RUN %r: %d arms would be registered",
            niche_id,
            stats["total"],
        )
        for arm_id, slug in arms:
            logger.debug("  %s  (slug=%s)", arm_id, slug)
        return stats

    # Live registration
    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import save_arm
    except ImportError as exc:
        logger.error(
            "[register_product_arms] cannot import genlab_core: %s. "
            "Ensure package is installed (uv sync).",
            exc,
        )
        raise

    client = BacklogClient()
    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        raise RuntimeError(
            "BacklogClient has no bandit_arms proxy — check DATABASE_URL + "
            "GENLAB_USE_POSTGRES env vars."
        )

    for arm_id, slug in arms:
        try:
            save_arm(
                proxy=proxy,
                arm_id=arm_id,
                alpha=1.0,
                beta=1.0,
                niche_id=niche_id,
                arm_type="product",
                dimension="affiliate_product",
                dimension_value=slug,
            )
            stats["registered"] += 1
        except Exception as exc:
            logger.warning(
                "[register_product_arms] failed to write %s/%s: %s",
                niche_id,
                arm_id,
                exc,
            )
            stats["skipped"] += 1

    logger.info(
        "[register_product_arms] %s: %d/%d registered (%d skipped)",
        niche_id,
        stats["registered"],
        stats["total"],
        stats["skipped"],
    )
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--niche",
        default="all",
        choices=["all", *sorted(KNOWN_NICHES)],
        help="Niche to register arms for. 'all' processes all 5 niches.",
    )
    parser.add_argument(
        "--catalog-path",
        default=DEFAULT_CATALOG_PATH,
        help=f"Path to affiliate_catalog.yaml (default: {DEFAULT_CATALOG_PATH}).",
    )
    parser.add_argument(
        "--seasonal-path",
        default=DEFAULT_SEASONAL_PATH,
        help=f"Path to affiliate_seasonal.yaml (default: {DEFAULT_SEASONAL_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate arms without writing to bandit_arms.",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable DEBUG logging.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    workspace_root = Path(__file__).resolve().parents[1]
    catalog_path = workspace_root / args.catalog_path
    seasonal_path = workspace_root / args.seasonal_path

    if not catalog_path.is_file():
        logger.error("catalog not found at %s", catalog_path)
        return 1

    catalog = _load_yaml(catalog_path)
    seasonal = _load_yaml(seasonal_path)  # empty dict OK if missing

    niches = sorted(KNOWN_NICHES) if args.niche == "all" else [args.niche]

    total_stats: dict[str, dict] = {}
    for niche in niches:
        try:
            stats = register_arms_for_niche(niche, catalog, seasonal, dry_run=args.dry_run)
        except Exception as exc:
            logger.error("[register_product_arms] %s failed: %s", niche, exc)
            return 2
        total_stats[niche] = stats

    # Summary
    grand_total = sum(s["total"] for s in total_stats.values())
    grand_registered = sum(s["registered"] for s in total_stats.values())
    grand_skipped = sum(s["skipped"] for s in total_stats.values())

    action = "would register" if args.dry_run else "registered"
    print(
        f"\n[register_product_arms] Summary: {action} "
        f"{grand_registered if not args.dry_run else grand_total}/{grand_total} "
        f"arms across {len(niches)} niches "
        f"({grand_skipped} skipped)"
    )
    for niche, stats in total_stats.items():
        n_shown = stats["registered"] if not args.dry_run else stats["total"]
        print(f"  {niche}: {n_shown}/{stats['total']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
