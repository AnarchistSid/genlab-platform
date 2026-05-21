"""FrameDrift pipeline runner — thin wrapper around GenericPipelineRunner.

Usage:
    python run_pipeline.py --dry-run
    python run_pipeline.py --verbose
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from genlab_core.pipeline import GenericPipelineRunner
from genlab_core.settings import settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
GENLAB_ROOT = PROJECT_ROOT.parent
NICHE_ID = "anime"


def _pre_run_check(niche_id: str, config: dict) -> None:
    missing = settings.validate_for_niche(niche_id)
    if missing:
        logger.warning("Missing credentials for '%s': %s", niche_id, missing)


def main() -> int:
    import os

    parser = argparse.ArgumentParser(description="FrameDrift pipeline runner")
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

    runner = GenericPipelineRunner(
        niche_roots={NICHE_ID: PROJECT_ROOT},
        genlab_root=GENLAB_ROOT,
        pre_run_hook=_pre_run_check,
    )
    ctx = runner.run(NICHE_ID, dry_run=args.dry_run, verbose=args.verbose)
    print(f"\nRun complete: {ctx.run_id}")
    print(f"Stories: {len(ctx.stories)}, Errors: {len(ctx.errors)}")
    return 1 if ctx.is_aborted else 0


if __name__ == "__main__":
    sys.exit(main())
