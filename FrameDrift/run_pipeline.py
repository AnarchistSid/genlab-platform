"""FrameDrift pipeline runner — delegates to unified CLI.

Usage:
    python run_pipeline.py --dry-run
    python run_pipeline.py --verbose
    python run_pipeline.py --force-publish
"""

from __future__ import annotations

import sys

from genlab_core.pipeline.cli import _exit_code_for_ctx, build_parser, run_pipeline


def main() -> int:
    parser = build_parser()
    parser.prog = "python run_pipeline.py (FrameDrift / anime)"
    # Override --niche to default to anime and not be required
    for action in parser._actions:
        if hasattr(action, "dest") and action.dest == "niche":
            action.required = False
            action.default = "anime"
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
    # 2026-07-22 rule #26 sibling fix: use canonical exit-code
    # derivation from run_report.status. Prior form treated 0
    # blueprints + status=failed as exit 0, hiding data-side
    # failures (e.g. today's WARP outage) from systemd OnFailure.
    return _exit_code_for_ctx(ctx)


if __name__ == "__main__":
    sys.exit(main())
