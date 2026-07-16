#!/usr/bin/env python3
"""Bootstrap bandit arms for the intelligent transformation stack.

Reads each niche's ``config/visuals.yaml``, parses the
``intelligent_transform.dimensions`` block via
``IntelligentTransformConfig``, and upserts one bandit arm per
(dimension × value) combination. Idempotent by design — safe to
re-run; existing arms preserve their posteriors.

Arm ID format:
    ``transform__<dimension>__<value>``

Examples:
    ``transform__music_mood__cinematic``
    ``transform__caption_style__word_by_word``
    ``transform__hook_framing__question_open``
    ``transform__audio_ducking__-12``  (int → str)
    ``transform__caption_pacing__750``  (int → str)

Per-arm metadata written to bandit_arms:
    * ``alpha=1.0, beta=1.0`` — Beta(1,1) cold-start prior. Cross-niche
      hierarchical transfer (Intervention 2, PR #487) may warm-start on
      arm creation if the source niche has data for the same style
      suffix. See ``get_transferred_prior()``.
    * ``arm_type='transformation'`` — classifies the arm class.
    * ``dimension`` — dimension name ('music_mood', etc.).
    * ``dimension_value`` — the specific value ('cinematic', etc.).

Usage:
    # Dry run — enumerate arms per niche without writing
    python scripts/register_transformation_arms.py --dry-run

    # Register arms for a single niche
    python scripts/register_transformation_arms.py --niche gaming

    # Register all 5 niches (production usage)
    python scripts/register_transformation_arms.py --niche all

Exit codes:
    0 — success (all niches processed OR dry-run completed)
    1 — argument error / config parse error
    2 — persister error (BacklogClient construction or write failure)

Sprint context: this is PR 3 of the 16-PR intelligent transformation
sprint. See memory ``sprint-intelligent-transformation-commit-2026-07-05``
for the full sprint plan + ``intelligent-transformation-architecture-
2026-07-05`` for the multi-dimensional bandit design.

Prerequisites:
    - Migration ``a7v8w9x0y1z2_intelligent_transformation_schema`` (PR 1)
      applied — provides bandit_arms.arm_type + dimension +
      dimension_value columns.
    - Config schema (PR 2) applied — provides ``intelligent_transform``
      block in each niche's visuals.yaml.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

import yaml

logger = logging.getLogger("register_transformation_arms")


# Map niche_id → path to visuals.yaml relative to workspace root.
NICHE_VISUALS_YAML: dict[str, str] = {
    "ai_creators": "BlackboxBrief/config/visuals.yaml",
    "gaming": "CriticalRush/niches/gaming/config/visuals.yaml",
    "sports": "ClutchWire/config/visuals.yaml",
    "movies": "SpliceReel/config/visuals.yaml",
    "anime": "FrameDrift/config/visuals.yaml",
}


def _arm_id(dimension: str, value: str) -> str:
    """Compose the arm_id string.

    Uses ``__`` (double underscore) as separator — matches the
    convention established by ``bandit_platform_split.platform_arm_id()``
    and ``source_performance._arm_id_for()``.

    Values that already contain ``__`` would silently corrupt the
    composite parse — this raises rather than accepting them.
    """
    if "__" in value:
        raise ValueError(
            f"transformation arm value {value!r} contains '__' separator — "
            f"would corrupt composite arm_id parse. Rename the value."
        )
    return f"transform__{dimension}__{value}"


def enumerate_arms_for_niche(
    parsed_yaml: dict,
) -> Iterable[tuple[str, str, str]]:
    """Yield (arm_id, dimension, dimension_value) tuples for a niche.

    Parses the ``intelligent_transform.dimensions`` block via the
    Pydantic model. Returns empty iterator when the block is absent
    (backward-compat with pre-PR-2 visuals.yaml files).
    """
    # Lazy import so this module works without genlab-core installed
    # (useful for pin tests that don't need the full learning stack).
    from genlab_core.media.intelligent_transform import (
        IntelligentTransformConfig,
    )

    cfg = IntelligentTransformConfig.from_visuals_dict(parsed_yaml)
    dims = cfg.dimensions

    # Iterate every dimension × value pair. Integer values (audio_ducking
    # levels_db, caption_pacing pacing_ms_options) get str()'d — arm_id
    # is a string type in DB.
    for value in dims.music_mood.moods:
        yield _arm_id("music_mood", value), "music_mood", value
    for value in dims.caption_style.styles:
        yield _arm_id("caption_style", value), "caption_style", value
    for value in dims.hook_framing.framings:
        yield _arm_id("hook_framing", value), "hook_framing", value
    for value in dims.intro_animation.templates:
        yield _arm_id("intro_animation", value), "intro_animation", value
    for value in dims.outro_cta.styles:
        yield _arm_id("outro_cta", value), "outro_cta", value
    for value in dims.pan_zoom.patterns:
        yield _arm_id("pan_zoom", value), "pan_zoom", value
    for value in dims.highlight_moment.methods:
        yield _arm_id("highlight_moment", value), "highlight_moment", value
    for int_value in dims.audio_ducking.levels_db:
        str_value = str(int_value)
        yield _arm_id("audio_ducking", str_value), "audio_ducking", str_value
    for value in dims.music_visual_sync.modes:
        yield _arm_id("music_visual_sync", value), "music_visual_sync", value
    for value in dims.caption_emphasis_color.colors:
        yield _arm_id("caption_emphasis_color", value), "caption_emphasis_color", value
    for int_value in dims.caption_pacing.pacing_ms_options:
        str_value = str(int_value)
        yield _arm_id("caption_pacing", str_value), "caption_pacing", str_value


def _load_niche_yaml(workspace_root: Path, niche_id: str) -> dict:
    yaml_path = workspace_root / NICHE_VISUALS_YAML[niche_id]
    if not yaml_path.is_file():
        raise FileNotFoundError(
            f"visuals.yaml for niche {niche_id!r} not found at {yaml_path}. "
            f"Check NICHE_VISUALS_YAML mapping matches repo layout."
        )
    return yaml.safe_load(yaml_path.read_text())


def register_arms_for_niche(
    niche_id: str,
    workspace_root: Path,
    *,
    dry_run: bool = False,
) -> dict:
    """Register all transformation arms for a niche.

    Returns stats dict:
        {
            "niche_id": str,
            "total": int — total arms enumerated,
            "registered": int — arms actually written (0 if dry_run),
            "skipped": int — arms that failed to write,
            "dry_run": bool,
        }
    """
    parsed = _load_niche_yaml(workspace_root, niche_id)
    arms = list(enumerate_arms_for_niche(parsed))

    stats = {
        "niche_id": niche_id,
        "total": len(arms),
        "registered": 0,
        "skipped": 0,
        "dry_run": dry_run,
    }

    if not arms:
        logger.warning(
            "[register_transformation_arms] niche %r: no arms enumerated "
            "(intelligent_transform block missing or empty)",
            niche_id,
        )
        return stats

    if dry_run:
        logger.info(
            "[register_transformation_arms] DRY RUN %r: %d arms would be registered",
            niche_id,
            stats["total"],
        )
        return stats

    # Live registration — construct BacklogClient + save_arm each arm.
    try:
        from genlab_core.http.backlog_client import BacklogClient
        from genlab_core.learning.arm_loader import save_arm
    except ImportError as exc:
        logger.error(
            "[register_transformation_arms] cannot import genlab_core: %s. "
            "Ensure package is installed (uv sync).",
            exc,
        )
        raise

    client = BacklogClient()
    proxy = getattr(client, "bandit_arms", None)
    if proxy is None:
        raise RuntimeError(
            "BacklogClient has no bandit_arms proxy — check DATABASE_URL + "
            "GENLAB_USE_POSTGRES env vars, or SharePoint fallback config."
        )

    for arm_id, dimension, dimension_value in arms:
        try:
            save_arm(
                proxy=proxy,
                arm_id=arm_id,
                alpha=1.0,
                beta=1.0,
                niche_id=niche_id,
                arm_type="transformation",
                dimension=dimension,
                dimension_value=dimension_value,
            )
            stats["registered"] += 1
        except Exception as exc:
            logger.warning(
                "[register_transformation_arms] failed to write %s/%s: %s",
                niche_id,
                arm_id,
                exc,
            )
            stats["skipped"] += 1

    logger.info(
        "[register_transformation_arms] %s: %d/%d registered (%d skipped)",
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
        choices=["all", *NICHE_VISUALS_YAML.keys()],
        help="Niche to register arms for. 'all' processes all 5 niches.",
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

    niches = list(NICHE_VISUALS_YAML.keys()) if args.niche == "all" else [args.niche]

    total_stats: dict[str, dict] = {}
    for niche in niches:
        try:
            stats = register_arms_for_niche(niche, workspace_root, dry_run=args.dry_run)
        except FileNotFoundError as exc:
            logger.error("%s", exc)
            return 1
        except Exception as exc:
            logger.error("[register_transformation_arms] %s failed: %s", niche, exc)
            return 2
        total_stats[niche] = stats

    # Summary
    grand_total = sum(s["total"] for s in total_stats.values())
    grand_registered = sum(s["registered"] for s in total_stats.values())
    grand_skipped = sum(s["skipped"] for s in total_stats.values())

    action = "would register" if args.dry_run else "registered"
    print(
        f"\n[register_transformation_arms] Summary: {action} "
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
