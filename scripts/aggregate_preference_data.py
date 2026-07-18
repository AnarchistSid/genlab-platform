#!/usr/bin/env python3
"""Aggregate all weekly preference_data JSONL exports into one training-ready file.

Intelligence stack #4b (2026-07-18). Consumer-side prep for eventual
AWS Bedrock fine-tune of Claude Haiku. The weekly ``dpo_export.py``
timer produces per-week JSONL files at
``$GENLAB_PROJECT_ROOT/.tmp/dpo/preference_data_YYYY-MM-DD.jsonl``.
This script merges them, deduplicates by ``metadata.preference_id``,
and writes a single ``preference_data_aggregated.jsonl`` that a
fine-tune orchestrator can consume.

## Usage

    # From prod (idempotent — safe to re-run):
    ./scripts/aggregate_preference_data.py

    # Custom paths:
    ./scripts/aggregate_preference_data.py \\
        --input-dir /custom/dir \\
        --output /custom/output.jsonl

    # Skip dedup (rarely needed — for debugging):
    ./scripts/aggregate_preference_data.py --no-dedup

## Output

Prints one summary line at the end:
    [aggregate] N pairs across M weekly files → OUTPUT (P duplicates dropped)

Exit codes:
    0 — success
    1 — no input files found (empty producer state)
    2 — output write failed

## When to run

Manually before a fine-tune experiment. There is intentionally NO
systemd timer for this script — the fine-tune orchestrator
(scripts/finetune_on_bedrock.py stub, once operator provisions AWS
access) should invoke aggregation as its first step.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def _find_project_root() -> Path:
    """Match dpo_export.py's project root resolution."""
    env = os.environ.get("GENLAB_PROJECT_ROOT")
    if env:
        return Path(env)
    # Fall back to script's parent (assumes scripts/ is at project root)
    return Path(__file__).resolve().parent.parent


def aggregate(
    input_dir: Path,
    output_path: Path,
    dedup: bool = True,
) -> tuple[int, int, int]:
    """Merge JSONL files under ``input_dir`` into ``output_path``.

    Returns (pair_count, file_count, duplicate_count).

    Dedup is by ``metadata.preference_id`` — same pair exported in
    two consecutive weekly runs would appear twice without dedup.
    """
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input dir not found: {input_dir}")

    files = sorted(input_dir.glob("preference_data_*.jsonl"))
    # Exclude the aggregated file itself if it's in the same dir
    files = [f for f in files if f.name != output_path.name]

    if not files:
        return (0, 0, 0)

    seen_ids: set[str] = set()
    all_pairs: list[dict] = []
    duplicate_count = 0

    for f in files:
        with f.open() as fp:
            for line_no, line in enumerate(fp, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    pair = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "[aggregate] skipping malformed line %s:%d: %s",
                        f.name,
                        line_no,
                        exc,
                    )
                    continue

                if dedup:
                    pref_id = (pair.get("metadata") or {}).get("preference_id")
                    if pref_id and pref_id in seen_ids:
                        duplicate_count += 1
                        continue
                    if pref_id:
                        seen_ids.add(pref_id)

                all_pairs.append(pair)

    # Write output atomically — .tmp then rename, so a partial write
    # doesn't leave a corrupt aggregated file that the fine-tune
    # orchestrator would then consume.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    with tmp.open("w") as fp:
        for pair in all_pairs:
            fp.write(json.dumps(pair) + "\n")
    tmp.replace(output_path)

    return (len(all_pairs), len(files), duplicate_count)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate preference_data JSONL files for fine-tune",
    )
    project_root = _find_project_root()
    default_input = project_root / ".tmp" / "dpo"
    default_output = default_input / "preference_data_aggregated.jsonl"

    parser.add_argument(
        "--input-dir",
        type=Path,
        default=default_input,
        help=f"Directory with weekly JSONL exports (default: {default_input})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output,
        help=f"Output aggregated JSONL path (default: {default_output})",
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Skip preference_id dedup (rare — debug only)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    try:
        pair_count, file_count, dup_count = aggregate(
            args.input_dir,
            args.output,
            dedup=not args.no_dedup,
        )
    except FileNotFoundError as exc:
        print(f"[aggregate] {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"[aggregate] write failed: {exc}", file=sys.stderr)
        return 2

    if pair_count == 0:
        print(
            f"[aggregate] 0 pairs found under {args.input_dir} — producer may not have run yet",
            file=sys.stderr,
        )
        return 1

    print(
        f"[aggregate] {pair_count} pairs across {file_count} weekly files "
        f"→ {args.output} ({dup_count} duplicates dropped)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
