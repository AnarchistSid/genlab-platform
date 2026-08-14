#!/usr/bin/env python3
"""Phase 1.D of the Genius Program Roadmap — AUTO #2 enrollment readiness.

Reads calibration_logger.stats for each niche and reports (or applies)
enrollment decisions based on the rule-#22-safe `enrollment_readiness`
verdict (samples + agreement% + confusion-matrix balance + FN-rate).

## Usage

    # Report readiness for all 5 niches (dry-run by default):
    uv run python scripts/check_auto_approval_enrollment.py

    # Report for one niche:
    uv run python scripts/check_auto_approval_enrollment.py --niche gaming

    # Auto-enroll niches that show 'ready' verdict (flips
    # auto_publish.enabled=true in the niche's publishing.yaml):
    uv run python scripts/check_auto_approval_enrollment.py --apply

## What gets flipped

For each niche with verdict='ready':
  * Edits `niches/<niche_dir>/config/publishing.yaml`
  * Sets `auto_publish.enabled: true`
  * Backs up the original as `publishing.yaml.bak.<timestamp>`
  * Leaves `min_confidence` and `rollout_pct` at their existing values

Operator retains override: script only flips ONE flag; unwinding is
one `git checkout publishing.yaml` away.

## Kill switches (existing)

Enrollment activation doesn't override:
  * `GENLAB_AUTO_APPROVE_DISABLED=1` (global kill)
  * `/opt/genlab/.runtime/auto_approve_kill_switch` (file-based kill)
  * `systemctl stop genlab-auto-approver.timer`

Any of these preempt the flag.

## Exit codes

  * 0 — completed (0+ niches enrolled)
  * 1 — DATABASE_URL unset or fatal config error
  * 2 — CLI arg error
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger("check_auto_approval_enrollment")

ACTIVE_NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")

# Map niche_id → filesystem directory name for publishing.yaml lookup
NICHE_DIRS = {
    "ai_creators": "BlackboxBrief",
    "gaming": "CriticalRush",
    "sports": "ClutchWire",
    "movies": "SpliceReel",
    "anime": "FrameDrift",
}


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="Flip flags for real")
    ap.add_argument("--niche", default=None,
                    help="Limit to one niche (all 5 by default)")
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--project-root", default="/opt/genlab",
                    help="Prod default; override for local testing")
    return ap.parse_args(argv)


def _flip_publishing_yaml(niche_dir: Path, niche_id: str) -> tuple[bool, str]:
    """Idempotent flip of `auto_publish.enabled: true` in publishing.yaml.

    Returns (flipped_bool, message). Never raises — a broken config
    path results in a message + False rather than crashing the whole
    enrollment run.
    """
    yaml_path = niche_dir / "config" / "publishing.yaml"
    if not yaml_path.is_file():
        return False, f"publishing.yaml not found at {yaml_path}"

    try:
        content = yaml_path.read_text()
    except OSError as exc:
        return False, f"cannot read {yaml_path}: {exc}"

    # Very cautious edit — regex-based to preserve comments/formatting.
    # Handles both existing enabled: false → true AND missing block.
    import re
    already_enabled = re.search(
        r"^auto_publish:\s*$\n(?:\s+.+\n)*?\s+enabled:\s*true",
        content, re.MULTILINE,
    )
    if already_enabled:
        return False, "already enabled — no change needed (idempotent)"

    disabled_match = re.search(
        r"(^auto_publish:\s*$\n(?:\s+.+\n)*?\s+enabled:\s*)(false)",
        content, re.MULTILINE,
    )
    if disabled_match:
        # Backup then replace `enabled: false` → `enabled: true`
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        backup = yaml_path.with_suffix(f".yaml.bak.{ts}")
        try:
            shutil.copy2(yaml_path, backup)
        except OSError as exc:
            return False, f"backup failed: {exc}"
        new_content = re.sub(
            r"(^auto_publish:\s*$\n(?:\s+.+\n)*?\s+enabled:\s*)false",
            r"\1true",
            content, count=1, flags=re.MULTILINE,
        )
        try:
            yaml_path.write_text(new_content)
        except OSError as exc:
            return False, f"write failed: {exc}"
        return True, f"flipped enabled false→true (backup: {backup.name})"

    # No auto_publish block at all — need to append one. Conservative:
    # don't modify unknown-shape configs; report + skip.
    return False, (
        "no `auto_publish.enabled` key found — needs manual add "
        "(script only flips existing false→true)"
    )


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    if os.environ.get("DATABASE_URL", "").strip() == "":
        logger.error("DATABASE_URL unset")
        return 1

    from genlab_core.scheduling.calibration_logger import stats_all_niches

    niches = [args.niche] if args.niche else list(ACTIVE_NICHES)
    try:
        per_niche = stats_all_niches(window_days=args.window_days)
    except Exception as exc:
        logger.error("stats query failed: %s", exc)
        return 1

    project_root = Path(args.project_root).resolve()
    print()
    print(f"{'niche':12} {'verdict':16} {'samples':>8} {'agree%':>7} "
          f"{'confusion':>16} {'reason'}")
    print("-" * 100)

    would_flip = []
    for niche_id in niches:
        s = per_niche.get(niche_id)
        if s is None:
            print(f"{niche_id:12} no-calibration-data")
            continue
        verdict = s.enrollment_readiness
        cm = f"TP{s.true_positives}/TN{s.true_negatives}/FP{s.false_positives}/FN{s.false_negatives}"
        print(f"{niche_id:12} {verdict:16} {s.sample_count:>8} "
              f"{s.agreement_rate * 100:>6.1f}% {cm:>16} "
              f"{s.readiness_reason}")
        if verdict == "ready":
            would_flip.append(niche_id)

    print()
    if not would_flip:
        print("Nothing ready for enrollment. Card operator to check readiness "
              "in Mission Control → Auto-approval calibration.")
        return 0

    if not args.apply:
        print(f"DRY RUN — would flip: {', '.join(would_flip)}")
        print("Re-run with --apply.")
        return 0

    print(f"Applying flips for: {', '.join(would_flip)}")
    for niche_id in would_flip:
        dir_name = NICHE_DIRS.get(niche_id)
        if not dir_name:
            print(f"  {niche_id}: unknown niche dir mapping — skipping")
            continue
        niche_dir = project_root / dir_name
        flipped, msg = _flip_publishing_yaml(niche_dir, niche_id)
        status = "ENROLLED" if flipped else "SKIPPED"
        print(f"  {niche_id:12} {status}  {msg}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
