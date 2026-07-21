"""Operator-facing CLI wrapper for policy_block RCA (L2 of the
policy-block learning loop shipped 2026-07-21).

Usage
-----

    # Single niche, default window, override min_samples
    python3 scripts/inspect_policy_block_rca.py --niche gaming --min-samples 1

    # All 5 niches
    python3 scripts/inspect_policy_block_rca.py --niche all

    # Widen the historical window
    python3 scripts/inspect_policy_block_rca.py --niche all --window-days 60

## Why the flag override

analyze_recent_policy_blocks() is flag-gated OFF by default via
GENLAB_POLICY_BLOCK_RCA_ENABLED — protects prod from burning LLM
tokens automatically until the operator opts in.

This CLI forces the flag ON *for the process's lifetime only* so
operator can inspect verdicts on demand without flipping the
prod-wide flag. Same process, no persistence: exit the CLI and the
flag reverts.

## Cost expectations

Each --niche invocation is one LLM call (Anthropic Sonnet by default,
OpenAI gpt-4o fallback). Typical cost: $0.005-$0.02 per call
depending on how many rows are in the window.

--niche all makes 5 calls (one per niche) — budget ~$0.05 for a
full sweep.

## Exit codes

  0 — ran successfully (regardless of whether verdicts were produced)
  1 — CLI misuse (bad args, empty niche)
  2 — LLM budget exhausted or DB unreachable (a real signal —
      Anthropic + OpenAI both returning insufficient_quota, or
      compliance_events unreadable)
"""

from __future__ import annotations

import argparse
import os
import sys

_ALL_NICHES: tuple[str, ...] = ("ai_creators", "gaming", "sports", "movies", "anime")


def _print_verdicts(niche: str, verdicts: list) -> None:
    if not verdicts:
        print(f"  (no verdicts — flag not enabled, no samples, or LLM unavailable)")
        return
    for i, v in enumerate(verdicts, 1):
        print(
            f"  verdict[{i}] category={v.violation_category} "
            f"confidence={v.confidence:.2f}"
        )
        for p in v.avoid_patterns:
            print(f"    - {p}")
        if v.sample_blueprint_ids:
            print(f"    samples: {', '.join(v.sample_blueprint_ids[:3])}"
                  + (f" (+{len(v.sample_blueprint_ids) - 3} more)"
                     if len(v.sample_blueprint_ids) > 3 else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--niche",
        required=True,
        help=f"Niche id ('all' for a full sweep). One of: all, {', '.join(_ALL_NICHES)}",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=30,
        help="Lookback window in days (default 30, max 90)",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=3,
        help="Minimum samples in window to trigger LLM call (default 3)",
    )
    args = parser.parse_args(argv)

    if args.niche != "all" and args.niche not in _ALL_NICHES:
        print(f"[error] --niche must be 'all' or one of {_ALL_NICHES}", file=sys.stderr)
        return 1

    # Force-enable flag for this process. Does NOT persist — only
    # this Python invocation sees the True value.
    os.environ["GENLAB_POLICY_BLOCK_RCA_ENABLED"] = "1"

    # Lazy import so --help works before genlab_core is importable
    try:
        from genlab_core.compliance.policy_block_rca import (
            analyze_recent_policy_blocks,
        )
    except ImportError as exc:
        print(f"[error] genlab_core import failed: {exc}", file=sys.stderr)
        return 2

    niches = list(_ALL_NICHES) if args.niche == "all" else [args.niche]

    print(
        f"=== L2 RCA — window={args.window_days}d "
        f"min_samples={args.min_samples} ==="
    )
    print()

    any_verdicts = False
    for niche in niches:
        print(f"---> niche={niche}")
        verdicts = analyze_recent_policy_blocks(
            niche,
            window_days=args.window_days,
            min_samples=args.min_samples,
        )
        _print_verdicts(niche, verdicts)
        if verdicts:
            any_verdicts = True
        print()

    if not any_verdicts:
        print(
            "NOTE: no verdicts across any niche. Likely causes:\n"
            "  - no platform_policy_block rows in the window\n"
            "  - fewer than --min-samples rows per niche\n"
            "  - LLM providers both out of credit (see stderr above)"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
