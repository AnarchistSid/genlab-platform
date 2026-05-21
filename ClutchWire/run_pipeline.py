"""ClutchWire pipeline runner — delegates to unified CLI.

Usage:
    python run_pipeline.py --dry-run
    python run_pipeline.py --verbose
    python run_pipeline.py --force-publish
"""

from __future__ import annotations

import sys

from genlab_core.pipeline.cli import build_parser, run_pipeline


def main() -> int:
    parser = build_parser()
    parser.prog = "python run_pipeline.py (ClutchWire / sports)"
    # Override --niche to default to sports and not be required
    for action in parser._actions:
        if hasattr(action, "dest") and action.dest == "niche":
            action.required = False
            action.default = "sports"
            break
    args = parser.parse_args()

    ctx = run_pipeline(
        niche_id=args.niche,
        dry_run=args.dry_run,
        verbose=args.verbose,
        force_publish=args.force_publish,
    )
    print(f"\nRun complete: {ctx.run_id}")
    print(f"Stories: {len(ctx.stories)}, Errors: {len(ctx.errors)}")
    return 1 if ctx.is_aborted else 0


if __name__ == "__main__":
    sys.exit(main())
