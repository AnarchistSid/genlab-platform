#!/usr/bin/env python3
"""AWS Bedrock fine-tune orchestrator STUB for Claude Haiku preference data.

Intelligence stack #4b (2026-07-18). Consumer-side stub. Blocked on
operator provisioning:

  1. AWS credentials on prod (~/.aws/credentials or env vars)
  2. AWS Bedrock model access for Claude 3 Haiku (Claude 4 fine-tune
     is not yet available on Bedrock as of 2026-07-18)
  3. `boto3>=1.35` added to pyproject.toml main deps

When those are in place, this script:

  1. Runs aggregate_preference_data.py to produce merged JSONL
  2. Guards on minimum-pair count (default 100) — refuses to fine-tune
     with insufficient signal
  3. Uploads training data to S3
  4. Submits Bedrock fine-tune job for claude-3-haiku
  5. Polls until completion + canary-checks (SEPARATE follow-up work)

## Current state — SAFE DEFAULT NO-OP

Running this script today prints a clear "not configured" message
and exits 0 without side effects. This is deliberate — we don't want
the systemd timer (also intentionally NOT installed yet) to page
operators on every fire while the API access is missing.

## To activate

1. Provision AWS Bedrock access (operator side, one-time setup)
2. Set env vars on prod .env:
     AWS_ACCESS_KEY_ID=...
     AWS_SECRET_ACCESS_KEY=...
     AWS_REGION=us-west-2  # or region with Bedrock claude-3-haiku access
     GENLAB_BEDROCK_FINETUNE_ENABLED=1
3. Install boto3:
     uv add boto3 --package genlab-core
4. Install systemd unit + monthly timer (deploy/systemd-phase2/)
5. First fire will attempt real fine-tune once pair count crosses
   MIN_PAIRS_FOR_FINETUNE (100).

## Runtime cost

Bedrock fine-tune for Claude 3 Haiku (2026 pricing, may vary):
  - Training: ~$0.05 per 1M tokens; 100 pairs x 300 tokens = 30K tokens = $0.0015
  - Storage: negligible per-month
  - Inference: standard Haiku rate (fine-tuned model isn't cheaper)

## Canary + promote

INTENTIONALLY out of scope for this stub. When the base wire ships
and produces its first fine-tuned model, a separate PR adds:
  - Canary check: run 20 generation tasks through both baseline + fine-tune
  - Metric: compare hook engagement predictions on held-out set
  - Promote: only if canary shows improvement above threshold (say 5%)

Filed as follow-up when this stub becomes real.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger(__name__)

# Minimum pairs before fine-tune is meaningful. Below this, the trained
# model likely overfits + degrades vs baseline. Tuned to match typical
# DPO signal-to-noise research (100 pairs is the low end of "useful").
MIN_PAIRS_FOR_FINETUNE = 100


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    if os.environ.get("GENLAB_BEDROCK_FINETUNE_ENABLED", "0") != "1":
        print(
            "[bedrock-finetune] GENLAB_BEDROCK_FINETUNE_ENABLED not set - "
            "no-op. See script docstring for activation checklist.",
            file=sys.stderr,
        )
        return 0

    # Prerequisite checks - clear error messages that tell operator
    # what's missing, don't just crash with an ImportError.
    try:
        import boto3  # noqa: F401
    except ImportError:
        print(
            "[bedrock-finetune] boto3 not installed. Run: uv add boto3 --package genlab-core",
            file=sys.stderr,
        )
        return 2

    for env_var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
        if not os.environ.get(env_var):
            print(
                f"[bedrock-finetune] {env_var} not set. See script docstring for AWS setup.",
                file=sys.stderr,
            )
            return 2

    # Real implementation intentionally deferred. When operator activates
    # this stub, follow-up PR fills in:
    #   from genlab_core.learning.bedrock_finetune import submit_finetune_job
    #   pair_count = _run_aggregate()
    #   if pair_count < MIN_PAIRS_FOR_FINETUNE:
    #       print(f"[bedrock-finetune] {pair_count} pairs < {MIN_PAIRS} required")
    #       return 0
    #   submit_finetune_job(...)
    #   poll_until_complete(...)
    #   run_canary_check(...)
    #   if canary_shows_improvement:
    #       promote(...)
    print(
        "[bedrock-finetune] flag + AWS creds detected. Real orchestration "
        "logic not yet implemented - filed as follow-up. See script "
        "docstring for planned flow.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
