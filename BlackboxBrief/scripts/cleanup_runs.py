"""Daily cleanup of old pipeline runs using the DiskQuotaManager.

Keeps the 5 most recent runs protected, evicts lowest-scoring runs first.
Designed to run via launchd at 00:30 UTC (06:00 IST) daily — before the
daily pipeline fires so it never competes for disk space mid-render.

Retention windows (optimised for 1-post/channel/day regime, ~1 GB/day):
  - runs/: 5 recent protected, 20 GB quota cap
  - media/: 7 days (allows retry of failed publishes without re-download)
  - renders/: 7 days (keeps rendered videos for platform retry)
  - audio/: 14 days (small footprint, useful for re-renders)
  - logs/: 14 days, 200 MB cap

Usage:
    python scripts/cleanup_runs.py [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

try:
    from genlab_core.storage.disk_quota import DiskQuotaManager
except ImportError as e:
    print(f"ERROR: genlab_core not on path. Run with: uv run python scripts/cleanup_runs.py\n{e}")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("cleanup_runs")

AGENT_NAME = "content_scraper"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PROJECT_ROOT / ".tmp"
RUNS_DIR = str(TMP_DIR / "runs")
MEDIA_DIR = TMP_DIR / "media"
_MEDIA_RETENTION_DAYS = 7  # was 1 — safe at 1 post/channel/day (~1 GB/day)
_MEDIA_RETENTION_SECONDS = _MEDIA_RETENTION_DAYS * 86400
_ONE_DAY_SECONDS = 86400
_PUBLISHING_LOCK_NAME = ".publishing_lock"


def is_publish_locked(directory: Path) -> bool:
    """Check if a directory has a publishing lock (active publish in progress)."""
    return (directory / _PUBLISHING_LOCK_NAME).exists()


def acquire_publish_lock(directory: Path) -> Path:
    """Create a lock file to prevent cleanup during active publish."""
    lock_path = directory / _PUBLISHING_LOCK_NAME
    lock_path.touch()
    return lock_path


def release_publish_lock(lock_path: Path) -> None:
    """Remove publish lock after publish completes (success or failure)."""
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


# Retention policies for unmanaged .tmp subdirectories.
# (runs/ managed by DiskQuotaManager, media/ by _cleanup_media — do NOT add here)
_EXTRA_RETENTION = [
    {"dir": "audio", "max_age_days": 14, "max_mb": 500},  # was 7d — small footprint, useful for re-renders
    {"dir": "renders", "max_age_days": 7, "max_mb": 2048},  # was 3d/1GB — keep for platform retry window
    {"dir": "logs", "max_age_days": 14, "max_mb": 200, "truncate_lines": 10_000},
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily run cleanup")
    parser.add_argument("--dry-run", action="store_true", help="Log what would be deleted without deleting")
    args = parser.parse_args()

    config = {
        "agents": {
            AGENT_NAME: {
                "runs_dir": RUNS_DIR,
                "quota_gb": 20,  # 20 GB cap (at ~1 GB/day, 7-day retention fits easily)
                "warn_pct": 70,
                "evict_pct": 85,
                "protect_recent": 5,  # was 3 — keep a week's worth at 1 run/day
            }
        }
    }

    manager = DiskQuotaManager(config)

    status = manager.get_status(AGENT_NAME)
    logger.info(
        "Disk status: used=%.1f GB (%.1f%%), quota=%.1f GB, runs=%d, evictable=%d (%.1f GB)",
        status.used_bytes / (1024**3),
        status.pct_used,
        status.quota_bytes / (1024**3),
        status.run_count,
        status.evictable_count,
        status.evictable_bytes / (1024**3),
    )

    if args.dry_run:
        logger.info("DRY RUN — showing eviction candidates without deleting")
        records = manager.scan_runs(AGENT_NAME)
        evictable = sorted(
            [r for r in records[3:] if not r.is_published],
            key=lambda r: r.score,
        )
        for r in evictable:
            logger.info(
                "  Would evict: %s (%.1f MB, score=%.3f, published=%s)",
                r.run_id,
                r.size_bytes / (1024**2),
                r.score,
                r.is_published,
            )
        _cleanup_media(dry_run=True)
        _cleanup_extras(dry_run=True)
        return

    # Always clean .tmp/media/ and extras regardless of quota status
    _cleanup_media(dry_run=False)
    _cleanup_extras(dry_run=False)

    if not status.over_warn:
        logger.info("Usage %.1f%% is below warn threshold — no eviction needed", status.pct_used)
        return

    evicted = manager.evict(AGENT_NAME)
    logger.info("Evicted %d paths", len(evicted))

    status_after = manager.get_status(AGENT_NAME)
    logger.info(
        "After cleanup: used=%.1f GB (%.1f%%)",
        status_after.used_bytes / (1024**3),
        status_after.pct_used,
    )


def _cleanup_media(dry_run: bool = False) -> None:
    """Delete media files in .tmp/media/ older than retention window.

    At 1 post/channel/day the 7-day window keeps ~3.5-7 GB — well within
    the 20 GB quota while allowing retry of failed publishes (e.g. YouTube
    quota resets) without re-downloading source clips.
    """
    if not MEDIA_DIR.is_dir():
        logger.info(".tmp/media/ does not exist — skipping media cleanup")
        return

    cutoff = time.time() - _MEDIA_RETENTION_SECONDS
    deleted = 0
    freed_bytes = 0
    suffixes = {".mp4", ".mp3", ".wav", ".mov", ".webm", ".aac"}

    for f in MEDIA_DIR.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in suffixes:
            continue
        # Skip files in publish-locked directories
        if is_publish_locked(f.parent):
            logger.info("  Skipping %s — directory has publishing lock", f.name)
            continue
        if f.stat().st_mtime < cutoff:
            size = f.stat().st_size
            if dry_run:
                logger.info("  Would delete: %s (%.1f MB)", f.name, size / (1024**2))
            else:
                f.unlink()
                deleted += 1
                freed_bytes += size

    # Remove empty directories
    if not dry_run:
        for d in sorted(MEDIA_DIR.rglob("*"), reverse=True):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    logger.info(
        ".tmp/media cleanup: %s%d files, %.1f MB freed",
        "[DRY RUN] " if dry_run else "",
        deleted,
        freed_bytes / (1024**2),
    )


def _cleanup_extras(dry_run: bool = False) -> None:
    """Apply retention policies to audio/, logs/, renders/ in .tmp/.

    Each policy specifies max_age_days and max_mb.  Files older than max_age
    are deleted first; if the directory still exceeds max_mb, largest files
    are deleted until under the cap.  Logs with a truncate_lines setting get
    truncated (keep last N lines) before age/size checks.
    """
    for policy in _EXTRA_RETENTION:
        target = TMP_DIR / policy["dir"]
        if not target.is_dir():
            continue

        label = f".tmp/{policy['dir']}"
        truncate_lines = policy.get("truncate_lines")
        max_age_s = policy["max_age_days"] * _ONE_DAY_SECONDS
        max_bytes = policy["max_mb"] * 1024 * 1024
        cutoff = time.time() - max_age_s

        # Phase 1: truncate large log files (logs dir only)
        if truncate_lines:
            for f in target.rglob("*"):
                if not f.is_file() or f.suffix != ".log":
                    continue
                try:
                    size = f.stat().st_size
                except OSError:
                    continue
                if size < 1_000_000:  # skip files under 1 MB
                    continue
                with open(f, "rb") as fh:
                    lines = fh.readlines()
                if len(lines) <= truncate_lines:
                    continue
                kept = lines[-truncate_lines:]
                saved = size - sum(len(ln) for ln in kept)
                if dry_run:
                    logger.info("  [%s] Would truncate %s (save %.1f MB)", label, f.name, saved / (1024**2))
                else:
                    with open(f, "wb") as fh:
                        fh.writelines(kept)
                    logger.info("[%s] Truncated %s (saved %.1f MB)", label, f.name, saved / (1024**2))

        # Phase 2: delete files older than max_age_days
        deleted = 0
        freed = 0
        for f in list(target.rglob("*")):
            if not f.is_file():
                continue
            # Skip files in publish-locked directories
            if is_publish_locked(f.parent):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                if dry_run:
                    logger.info("  [%s] Would delete (age): %s (%.1f MB)", label, f.name, st.st_size / (1024**2))
                else:
                    f.unlink()
                    deleted += 1
                    freed += st.st_size

        # Phase 3: if still over max_mb, delete largest files first
        remaining: list[tuple[Path, int]] = []
        total_remaining = 0
        for f in target.rglob("*"):
            if not f.is_file():
                continue
            try:
                sz = f.stat().st_size
            except OSError:
                continue
            remaining.append((f, sz))
            total_remaining += sz

        if total_remaining > max_bytes:
            remaining.sort(key=lambda x: x[1], reverse=True)
            for f, sz in remaining:
                if total_remaining <= max_bytes:
                    break
                if dry_run:
                    logger.info("  [%s] Would delete (cap): %s (%.1f MB)", label, f.name, sz / (1024**2))
                else:
                    f.unlink()
                    deleted += 1
                    freed += sz
                total_remaining -= sz

        # Remove empty directories
        if not dry_run:
            for d in sorted(target.rglob("*"), reverse=True):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass

        logger.info(
            "%s cleanup: %s%d files, %.1f MB freed",
            label,
            "[DRY RUN] " if dry_run else "",
            deleted,
            freed / (1024**2),
        )


if __name__ == "__main__":
    main()
