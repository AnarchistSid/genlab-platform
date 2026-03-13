#!/usr/bin/env python3
"""Print disk quota status for all configured agents.

Usage:
    python scripts/disk_quota_status.py [--config path/to/storage.yaml]
    python scripts/disk_quota_status.py --agent content_scraper
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from genlab_core.storage.disk_quota import DiskQuotaManager


def _fmt_bytes(b: int) -> str:
    if b >= 1024**3:
        return f"{b / 1024**3:.1f} GB"
    if b >= 1024**2:
        return f"{b / 1024**2:.1f} MB"
    return f"{b / 1024:.0f} KB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Disk quota status monitor")
    parser.add_argument(
        "--config",
        default=str(Path(__file__).resolve().parent.parent / "config" / "storage.yaml"),
        help="Path to storage.yaml config",
    )
    parser.add_argument("--agent", help="Show status for a specific agent only")
    args = parser.parse_args()

    manager = DiskQuotaManager.from_yaml(args.config)

    agents = manager._config.get("agents", {})
    if args.agent:
        if args.agent not in agents:
            print(f"Unknown agent: {args.agent}", file=sys.stderr)
            return 1
        agents = {args.agent: agents[args.agent]}

    exit_code = 0
    for agent_name, agent_cfg in agents.items():
        if not agent_cfg.get("runs_dir"):
            print(f"  {agent_name}: runs_dir not configured, skipping")
            continue

        status = manager.get_status(agent_name)
        marker = ""
        if status.over_quota:
            marker = " [OVER QUOTA]"
            exit_code = 1
        elif status.over_warn:
            marker = " [WARNING]"

        print(f"  {agent_name}:{marker}")
        print(f"    Used:      {_fmt_bytes(status.used_bytes)} / {_fmt_bytes(status.quota_bytes)}")
        print(f"    Usage:     {status.pct_used}%")
        print(f"    Runs:      {status.run_count} total, {status.evictable_count} evictable")
        print(f"    Evictable: {_fmt_bytes(status.evictable_bytes)}")
        print()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
