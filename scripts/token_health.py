#!/usr/bin/env python3
"""Token health checker CLI — per-niche credential verification.

Delegates to genlab_core.monitoring for actual health checks,
adds per-niche credential iteration.

Usage:
    uv run --package genlab-core python scripts/token_health.py
    uv run --package genlab-core python scripts/token_health.py --niche gaming
    uv run --package genlab-core python scripts/token_health.py --json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from genlab_core.monitoring.token_health import run_all_checks
from genlab_core.publishing.niche_credentials import (
    NICHE_CREDENTIAL_PREFIXES,
    resolve_meta_credentials,
    resolve_threads_credentials,
    resolve_youtube_credentials,
)

logger = logging.getLogger(__name__)

NICHES = ["ai_creators", "gaming", "sports", "movies", "anime"]


def _check_niche_credentials(niche_id: str) -> list[dict]:
    """Check all platform credentials for a specific niche."""
    results = []
    prefix = NICHE_CREDENTIAL_PREFIXES.get(niche_id, "")

    # Meta/Instagram
    meta = resolve_meta_credentials(niche_id)
    results.append({
        "platform": "instagram",
        "niche": niche_id,
        "status": "ok" if meta.get("ig_access_token") else "missing",
        "detail": "token set" if meta.get("ig_access_token") else f"no {prefix}_META_ACCESS_TOKEN",
    })

    # YouTube
    yt = resolve_youtube_credentials(niche_id)
    results.append({
        "platform": "youtube",
        "niche": niche_id,
        "status": "ok" if yt.get("refresh_token") else "missing",
        "detail": "refresh_token set" if yt.get("refresh_token") else f"no {prefix}_YOUTUBE_REFRESH_TOKEN",
    })

    # Threads
    th = resolve_threads_credentials(niche_id)
    results.append({
        "platform": "threads",
        "niche": niche_id,
        "status": "ok" if th[0] else "missing",
        "detail": "token set" if th[0] else f"no {prefix}_THREADS_ACCESS_TOKEN",
    })

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Token health checker")
    parser.add_argument("--niche", choices=NICHES, help="Check specific niche only")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Global checks
    global_results = run_all_checks()

    # Per-niche checks
    niches = [args.niche] if args.niche else NICHES
    niche_results = []
    for niche in niches:
        niche_results.extend(_check_niche_credentials(niche))

    if args.json:
        print(json.dumps({"global": global_results, "niches": niche_results}, indent=2))
    else:
        print("\n=== Global Token Health ===")
        for check in global_results.get("checks", []):
            status = "✓" if check.get("valid") or check.get("status") == "ok" else "✗"
            print(f"  {status} {check.get('platform', check.get('service', '?'))}: {check.get('message', check.get('detail', ''))[:80]}")

        print("\n=== Per-Niche Credentials ===")
        for r in niche_results:
            status = "✓" if r["status"] == "ok" else "✗"
            print(f"  {status} {r['niche']:12} | {r['platform']:10} | {r['detail']}")

    failed = sum(1 for r in niche_results if r["status"] != "ok")
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
