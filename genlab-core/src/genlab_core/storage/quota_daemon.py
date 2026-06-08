"""Disk quota enforcement daemon.

Runs as a long-lived process (via launchd KeepAlive). Polls disk usage
every 60 seconds and triggers two-pass eviction when thresholds are
exceeded.

Usage:
    python -m genlab_core.storage.quota_daemon --config /path/to/disk_quota.yaml

Or via launchd:
    ~/Library/LaunchAgents/com.genlab.quota-monitor.plist
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("quota_daemon")

_RUNNING = True


def _handle_signal(signum, frame):
    global _RUNNING
    logger.info("Received signal %d, shutting down", signum)
    _RUNNING = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Disk quota enforcement daemon")
    parser.add_argument(
        "--config",
        default=os.getenv(
            "DISK_QUOTA_CONFIG",
            str(Path.home() / "GenLab" / "genlab-core" / "config" / "disk_quota.yaml"),
        ),
        help="Path to disk_quota.yaml",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Poll interval in seconds (default: 60)",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Config not found: %s", config_path)
        sys.exit(1)

    from genlab_core.storage.disk_quota import DiskQuotaManager

    manager = DiskQuotaManager.from_yaml(config_path)
    agents = list(manager._config.get("agents", {}).keys())
    logger.info(
        "Quota daemon started: config=%s agents=%s interval=%ds", config_path, agents, args.interval
    )

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    while _RUNNING:
        for agent in agents:
            try:
                status = manager.get_status(agent)
                if status.over_quota:
                    logger.warning(
                        "[%s] OVER QUOTA %.1f%% — triggering eviction",
                        agent,
                        status.pct_used,
                    )
                    evicted = manager.evict(agent)
                    if evicted:
                        logger.info("[%s] Evicted %d paths", agent, len(evicted))
                elif status.over_warn:
                    logger.info(
                        "[%s] WARNING %.1f%% used (%d runs, %d evictable)",
                        agent,
                        status.pct_used,
                        status.run_count,
                        status.evictable_count,
                    )
                else:
                    logger.debug(
                        "[%s] OK %.1f%% used",
                        agent,
                        status.pct_used,
                    )
            except Exception:
                logger.exception("[%s] Quota check failed", agent)

        # Sweep the shared extra-dirs (audit P-9) — these are NOT per-agent
        # and only need one pass per cycle. Best-effort; per-entry errors
        # are logged inside ``sweep_extra_dirs`` and never raised here.
        try:
            manager.sweep_extra_dirs()
        except Exception:
            logger.exception("Extra-dir sweep failed")

        # Sleep in short intervals so SIGTERM is responsive
        for _ in range(args.interval):
            if not _RUNNING:
                break
            time.sleep(1)

    logger.info("Quota daemon stopped")


if __name__ == "__main__":
    main()
