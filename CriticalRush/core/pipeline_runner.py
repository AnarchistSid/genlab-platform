"""CriticalRush pipeline runner — gaming niche only.

Each channel has its own run_pipeline.py. This file is CriticalRush's
entry point for the gaming niche — it does NOT run other channels.
Use ClutchWire/run_pipeline.py, SpliceReel/run_pipeline.py, etc. for
their respective niches.

Usage:
    runner = PipelineRunner()
    ctx = runner.run("gaming")

CLI:
    python -m core.pipeline_runner --niche gaming --dry-run
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from genlab_core.context import PipelineContext
from genlab_core.pipeline import GenericPipelineRunner
from genlab_core.settings import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GENLAB_ROOT = PROJECT_ROOT.parent  # /Users/anarchistsid/GenLab

# CriticalRush only handles gaming. Other channels have their own run_pipeline.py.
NICHE_ROOTS = {
    "gaming": PROJECT_ROOT,
}


def _pre_run_check(niche_id: str, config: dict[str, Any]) -> None:
    """Validate credentials before pipeline execution."""
    missing = settings.validate_for_niche(niche_id)
    if missing:
        logger.warning("Missing credentials for '%s': %s", niche_id, missing)


def _post_run_summary(ctx: PipelineContext) -> None:
    """Print gaming-centric dry-run summary."""
    _print_dry_run_summary(ctx)


class PipelineRunner:
    """Run the CriticalRush content pipeline for a given niche.

    Delegates to ``GenericPipelineRunner`` for all execution logic.
    """

    SUPPORTED_NICHES = list(NICHE_ROOTS.keys())

    def __init__(self) -> None:
        self._inner = GenericPipelineRunner(
            niche_roots=NICHE_ROOTS,
            genlab_root=GENLAB_ROOT,
            pre_run_hook=_pre_run_check,
            post_run_hook=_post_run_summary,
        )

    def run(
        self,
        niche_id: str,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> PipelineContext:
        return self._inner.run(niche_id, dry_run=dry_run, verbose=verbose)

    # Expose internal methods for tests that call them directly
    @staticmethod
    def _load_stages(niche_id: str, config: dict[str, Any]):
        return GenericPipelineRunner._load_stages(niche_id, config)

    @staticmethod
    def _group_stages(stages, declarations):
        return GenericPipelineRunner._group_stages(stages, declarations)


def _print_dry_run_summary(ctx: PipelineContext) -> None:
    """Print a human-readable summary after a dry run."""
    print(f"\n{'=' * 60}")
    print(f"  DRY RUN SUMMARY — {ctx.run_id}")
    print(f"{'=' * 60}")

    stats = ctx.run_stats
    fetch = stats.get("fetch", {})
    print(
        f"\n  Sources: Steam={fetch.get('steam_count', 0)}, "
        f"Twitch={fetch.get('twitch_count', 0)}, "
        f"RSS={fetch.get('rss_count', 0)}"
    )
    print(
        f"  After dedup: {fetch.get('after_dedup', 0)} → "
        f"final {fetch.get('final_count', 0)} stories"
    )

    filt = stats.get("filter", {})
    if filt:
        print(
            f"\n  Filter: {filt.get('input_count', 0)} → "
            f"{filt.get('selected', 0)} stories "
            f"(rejected: {filt.get('rejected', 0)})"
        )
        rejected_titles = filt.get("rejected_titles", [])
        if rejected_titles:
            print(f"  Rejected samples: {rejected_titles[:3]}")

    igdb = stats.get("igdb_enrichment", {})
    print(
        f"\n  IGDB enrichment: {igdb.get('enriched_count', 0)} enriched, "
        f"{igdb.get('skipped_count', 0)} already had IDs, "
        f"{igdb.get('failed_count', 0)} failed "
        f"({igdb.get('status', 'ran')})"
    )

    clip = stats.get("clip_sourcing", {})
    if clip:
        print(
            f"\n  Clip sourcing: {clip.get('clips_sourced', 0)}/{clip.get('total_stories', 0)} sourced"
        )
    else:
        print("\n  Clip sourcing: skipped (use_gaming_clip_sourcer=False)")

    cw = stats.get("content_writing", {})
    if cw:
        status = cw.get("status", "")
        if status == "skipped_no_api_key":
            print("\n  Content writing: skipped (no API key)")
        else:
            print(
                f"\n  Content writing: {cw.get('written_count', 0)} written, "
                f"{cw.get('failed_count', 0)} failed"
            )

    render = stats.get("render", {})
    if render:
        print(
            f"\n  Render: {render.get('rendered', 0)} rendered, "
            f"{render.get('failed', 0)} failed, "
            f"{render.get('skipped', 0)} skipped"
        )
        breakdown = render.get("method_breakdown", {})
        if breakdown:
            print(f"  Methods: {breakdown}")
    else:
        print("\n  Render: no stats recorded")

    pub = stats.get("publish", {})
    if pub:
        mode = "DRY RUN" if pub.get("dry_run") else "LIVE"
        print(
            f"\n  Publish ({mode}): {pub.get('published', 0)} published, "
            f"{pub.get('failed', 0)} failed, "
            f"{pub.get('skipped', 0)} skipped"
        )

    report = stats.get("report", {})
    if report:
        print(f"\n  Report: {report.get('status', '?')} → {report.get('path', '?')}")

    if ctx.stories:
        print("\n  Top stories:")
        for i, s in enumerate(ctx.stories[:10], 1):
            igdb_id = s.get("igdb_game_id") or "-"
            steam_id = s.get("steam_app_id") or "-"
            content = s.get("content", {})
            hook = content.get("hook", "-")
            rendered = (s.get("media") or {}).get("rendered_path")
            pub_status = (s.get("publishing") or {}).get("status", "-")
            print(f"    {i:2}. [{s['score']:.2f}] {s['title']}")
            print(f"        src={s['source']}, steam={steam_id}, igdb={igdb_id}")
            if hook != "-":
                print(f'        hook: "{hook}"')
            if rendered:
                print(f"        video: {rendered}")
            if pub_status != "-":
                print(f"        publish: {pub_status}")
    else:
        print("\n  No stories fetched.")

    if ctx.errors:
        print(f"\n  Errors ({len(ctx.errors)}):")
        for err in ctx.errors:
            print(f"    - {err['stage']}: {err['message']}")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(description="CriticalRush pipeline runner")
    parser.add_argument("--niche", default="gaming", choices=["gaming"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--force-publish",
        action="store_true",
        help="Bypass schedule gate and daily cap — publish immediately",
    )
    args = parser.parse_args()

    if args.force_publish:
        os.environ["FORCE_PUBLISH"] = "true"

    runner = PipelineRunner()
    ctx = runner.run(args.niche, dry_run=args.dry_run, verbose=args.verbose)
    print(f"\nRun complete: {ctx.run_id}")
    print(f"Stories: {len(ctx.stories)}, Errors: {len(ctx.errors)}")
    sys.exit(1 if ctx.is_aborted else 0)
