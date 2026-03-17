"""Unified pipeline CLI — run any niche from a single entry point.

Usage::

    python -m genlab_core.pipeline --niche gaming
    python -m genlab_core.pipeline --niche sports,movies
    python -m genlab_core.pipeline --niche all
    python -m genlab_core.pipeline --niche anime --stages fetch,score --dry-run

Programmatic usage::

    from genlab_core.pipeline.cli import run_pipeline
    run_pipeline(niche_id="sports")

This module discovers niche root directories from the GENLAB_ROOT, maps
niche_ids to their channel folders, and delegates to GenericPipelineRunner.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from genlab_core.context import PipelineContext
from genlab_core.pipeline.pipeline_runner import GenericPipelineRunner
from genlab_core.settings import settings

logger = logging.getLogger(__name__)

# ── Valid niche IDs ──────────────────────────────────────────────────────────

VALID_NICHE_IDS = frozenset({
    "ai_creators",
    "gaming",
    "sports",
    "movies",
    "anime",
})

# ── Niche → channel directory name mapping ───────────────────────────────────
# Each niche_id maps to the directory name under GENLAB_ROOT that contains
# its config/, strategies/, and pipeline stages.

NICHE_DIR_NAMES: Dict[str, str] = {
    "ai_creators": "BlackboxBrief",
    "gaming": "CriticalRush",
    "sports": "ClutchWire",
    "movies": "SpliceReel",
    "anime": "FrameDrift",
}


def _resolve_genlab_root() -> Path:
    """Resolve the GenLab workspace root directory.

    Checks (in order):
    1. GENLAB_ROOT environment variable
    2. Walk up from genlab-core's package location

    Raises:
        RuntimeError: If the root cannot be determined.
    """
    env_root = os.environ.get("GENLAB_ROOT")
    if env_root:
        root = Path(env_root).resolve()
        if root.is_dir():
            return root

    # genlab-core lives at {GENLAB_ROOT}/genlab-core/src/genlab_core/pipeline/cli.py
    # Walk up 5 levels: cli.py -> pipeline/ -> genlab_core/ -> src/ -> genlab-core/ -> GenLab/
    inferred = Path(__file__).resolve().parent.parent.parent.parent.parent
    if (inferred / "genlab-core").is_dir():
        return inferred

    raise RuntimeError(
        "Cannot determine GENLAB_ROOT. Set the GENLAB_ROOT environment "
        "variable or run from within the GenLab workspace."
    )


def _build_niche_roots(
    genlab_root: Path,
    niche_ids: Sequence[str],
) -> Dict[str, Path]:
    """Build the niche_roots mapping for the requested niches.

    Args:
        genlab_root: Path to the GenLab workspace root.
        niche_ids: Niche identifiers to include.

    Returns:
        Mapping of niche_id to the channel's root directory.

    Raises:
        FileNotFoundError: If a channel directory does not exist.
    """
    roots: Dict[str, Path] = {}
    for niche_id in niche_ids:
        dir_name = NICHE_DIR_NAMES.get(niche_id)
        if dir_name is None:
            raise ValueError(
                f"Unknown niche_id '{niche_id}'. "
                f"Valid niches: {sorted(VALID_NICHE_IDS)}"
            )
        niche_root = genlab_root / dir_name
        if not niche_root.is_dir():
            raise FileNotFoundError(
                f"Channel directory not found: {niche_root} "
                f"(niche_id='{niche_id}')"
            )
        roots[niche_id] = niche_root
    return roots


def _pre_run_check(niche_id: str, config: dict) -> None:
    """Validate credentials before pipeline execution."""
    missing = settings.validate_for_niche(niche_id)
    if missing:
        logger.warning("Missing credentials for '%s': %s", niche_id, missing)


def _parse_niche_arg(niche_arg: str) -> List[str]:
    """Parse the --niche argument into a list of niche IDs.

    Accepts:
        - "all" -> all 5 niches
        - "gaming" -> single niche
        - "sports,movies,anime" -> comma-separated list

    Returns:
        Sorted list of validated niche IDs.

    Raises:
        ValueError: If any niche_id is not in VALID_NICHE_IDS.
    """
    if niche_arg.strip().lower() == "all":
        return sorted(VALID_NICHE_IDS)

    niche_ids = [n.strip() for n in niche_arg.split(",") if n.strip()]
    invalid = [n for n in niche_ids if n not in VALID_NICHE_IDS]
    if invalid:
        raise ValueError(
            f"Invalid niche ID(s): {invalid}. "
            f"Valid niches: {sorted(VALID_NICHE_IDS)}"
        )
    if not niche_ids:
        raise ValueError("No niche IDs provided.")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: List[str] = []
    for nid in niche_ids:
        if nid not in seen:
            seen.add(nid)
            unique.append(nid)
    return unique


def _write_run_report(
    genlab_root: Path,
    run_id: str,
    results: Dict[str, Dict[str, Any]],
) -> Path:
    """Write a JSON run report summarizing all niche executions.

    Args:
        genlab_root: GenLab workspace root.
        run_id: The unified run identifier.
        results: Mapping of niche_id to result summary dicts.

    Returns:
        Path to the written report file.
    """
    run_dir = genlab_root / ".tmp" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "run_report.json"

    report = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "niches": results,
    }
    report_path.write_text(json.dumps(report, indent=2, default=str))
    logger.info("[CLI] Run report written to %s", report_path)
    return report_path


def run_pipeline(
    niche_id: str,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    force_publish: bool = False,
    stages: Optional[List[str]] = None,
) -> PipelineContext:
    """Run the pipeline for a single niche.

    This is the programmatic entry point used by thin wrappers in each
    channel directory.

    Args:
        niche_id: The niche to run (e.g. "sports", "gaming").
        dry_run: Validate config and load stages without executing.
        verbose: Enable DEBUG-level logging.
        force_publish: Set FORCE_PUBLISH=true in environment.
        stages: If provided, filter to only these stage class names.
            (Not yet implemented — reserved for future use.)

    Returns:
        The PipelineContext with accumulated state.

    Raises:
        ValueError: If niche_id is invalid.
        FileNotFoundError: If the channel directory is missing.
    """
    if force_publish:
        os.environ["FORCE_PUBLISH"] = "true"

    genlab_root = _resolve_genlab_root()
    niche_roots = _build_niche_roots(genlab_root, [niche_id])

    runner = GenericPipelineRunner(
        niche_roots=niche_roots,
        genlab_root=genlab_root,
        pre_run_hook=_pre_run_check,
    )
    return runner.run(niche_id, dry_run=dry_run, verbose=verbose)


def run_multi(
    niche_ids: List[str],
    *,
    dry_run: bool = False,
    verbose: bool = False,
    force_publish: bool = False,
    stages: Optional[List[str]] = None,
) -> Dict[str, PipelineContext]:
    """Run the pipeline for multiple niches sequentially.

    Args:
        niche_ids: List of niche IDs to execute.
        dry_run: Validate config and load stages without executing.
        verbose: Enable DEBUG-level logging.
        force_publish: Set FORCE_PUBLISH=true in environment.
        stages: If provided, filter to only these stage class names.
            (Not yet implemented — reserved for future use.)

    Returns:
        Mapping of niche_id to PipelineContext.
    """
    if force_publish:
        os.environ["FORCE_PUBLISH"] = "true"

    genlab_root = _resolve_genlab_root()
    niche_roots = _build_niche_roots(genlab_root, niche_ids)

    results: Dict[str, PipelineContext] = {}

    for niche_id in niche_ids:
        logger.info(
            "[CLI] ════════════════════════════════════════════════════"
        )
        logger.info("[CLI] Starting pipeline: %s", niche_id)
        logger.info(
            "[CLI] ════════════════════════════════════════════════════"
        )

        runner = GenericPipelineRunner(
            niche_roots={niche_id: niche_roots[niche_id]},
            genlab_root=genlab_root,
            pre_run_hook=_pre_run_check,
        )

        try:
            ctx = runner.run(niche_id, dry_run=dry_run, verbose=verbose)
            results[niche_id] = ctx
            status = "ABORTED" if ctx.is_aborted else "OK"
            logger.info(
                "[CLI] Finished %s: %s (stories=%d, errors=%d)",
                niche_id, status, len(ctx.stories), len(ctx.errors),
            )
        except Exception as exc:
            logger.error("[CLI] Pipeline failed for '%s': %s", niche_id, exc)
            # Create a minimal context to record the failure
            ctx = PipelineContext(
                niche_id=niche_id,
                run_id=f"{niche_id}_failed",
            )
            ctx.record_error("cli", exc, fatal=True)
            results[niche_id] = ctx

    return results


def _print_summary(results: Dict[str, PipelineContext]) -> None:
    """Print a human-readable summary of all niche executions."""
    print(f"\n{'=' * 60}")
    print("  PIPELINE RUN SUMMARY")
    print(f"{'=' * 60}")

    for niche_id, ctx in results.items():
        status = "ABORTED" if ctx.is_aborted else "OK"
        marker = "X" if ctx.is_aborted else "v"
        print(
            f"  [{marker}] {niche_id:<14} "
            f"stories={len(ctx.stories):>3}  "
            f"errors={len(ctx.errors):>2}  "
            f"status={status}"
        )

    total_errors = sum(len(ctx.errors) for ctx in results.values())
    aborted = sum(1 for ctx in results.values() if ctx.is_aborted)
    print(f"\n  Niches: {len(results)}  "
          f"Total errors: {total_errors}  "
          f"Aborted: {aborted}")
    print(f"{'=' * 60}")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the unified pipeline CLI."""
    parser = argparse.ArgumentParser(
        prog="python -m genlab_core.pipeline",
        description=(
            "GenLab unified pipeline CLI. Run content pipelines for any "
            "combination of niches from a single command."
        ),
    )
    parser.add_argument(
        "--niche",
        required=True,
        help=(
            "Niche(s) to run. Single ID (e.g. 'gaming'), comma-separated "
            "list (e.g. 'sports,movies'), or 'all' for all 5 niches."
        ),
    )
    parser.add_argument(
        "--stages",
        default=None,
        help=(
            "Comma-separated list of stage class names to run. "
            "If omitted, all stages from niche config are executed. "
            "(Reserved for future use.)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load config and stages but do not execute them.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    parser.add_argument(
        "--force-publish",
        action="store_true",
        help="Bypass schedule gate and daily cap — publish immediately.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point.

    Args:
        argv: Command-line arguments. If None, reads from sys.argv.

    Returns:
        Exit code: 0 on success, 1 if any niche aborted or failed.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        niche_ids = _parse_niche_arg(args.niche)
    except ValueError as e:
        parser.error(str(e))
        return 1  # unreachable, but keeps type checkers happy

    stages_filter: Optional[List[str]] = None
    if args.stages:
        stages_filter = [s.strip() for s in args.stages.split(",") if s.strip()]

    # Configure root logging early for multi-niche banner output
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    for noisy in ("httpx", "httpcore", "hpack", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logger.info(
        "[CLI] GenLab pipeline starting: niches=%s, dry_run=%s",
        niche_ids, args.dry_run,
    )

    if len(niche_ids) == 1:
        ctx = run_pipeline(
            niche_ids[0],
            dry_run=args.dry_run,
            verbose=args.verbose,
            force_publish=args.force_publish,
            stages=stages_filter,
        )
        print(f"\nRun complete: {ctx.run_id}")
        print(f"Stories: {len(ctx.stories)}, Errors: {len(ctx.errors)}")
        return 1 if ctx.is_aborted else 0

    results = run_multi(
        niche_ids,
        dry_run=args.dry_run,
        verbose=args.verbose,
        force_publish=args.force_publish,
        stages=stages_filter,
    )

    # Write unified run report
    genlab_root = _resolve_genlab_root()
    unified_run_id = f"multi_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    report_data: Dict[str, Dict[str, Any]] = {}
    for niche_id, ctx in results.items():
        report_data[niche_id] = {
            "run_id": ctx.run_id,
            "stories": len(ctx.stories),
            "errors": len(ctx.errors),
            "is_aborted": ctx.is_aborted,
        }
    _write_run_report(genlab_root, unified_run_id, report_data)

    _print_summary(results)

    any_failed = any(ctx.is_aborted for ctx in results.values())
    return 1 if any_failed else 0
