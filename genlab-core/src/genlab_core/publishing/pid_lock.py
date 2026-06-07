"""Process-level lock with PID-file backing and stale-detection.

Lives in its own module so the multi-platform publisher orchestrator
(:mod:`genlab_core.publishing.publish_all_platforms`) stays focused on
publishing, and so future callers (single-platform debug scripts, the
engagement worker if it ever needs the same exclusion) can reuse the
primitive without dragging the whole orchestrator import surface.

Originally lived inline in ``publish_all_platforms.py``; extracted in the
refactor-#9 decomposition (PR 1/N). Re-exported from there for
backwards-compatible imports.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class PidLock:
    """Process-level lock using a PID file with stale-holder detection.

    A successful :meth:`acquire` creates ``{lock_dir}/publisher-{niche_id}.lock``
    atomically with ``O_EXCL`` and writes the current PID into it. A subsequent
    :meth:`acquire` from another process sees the file already exists; it then
    reads the PID and checks whether that process is still alive via
    ``os.kill(pid, 0)``. If the holder is alive, the lock is held — return
    False. If the holder is gone (or the file is unreadable, or the PID is
    bogus), the lock is treated as stale, removed, and ``acquire`` retries
    the atomic create exactly once.

    Single-host only — relies on PID space, so it does NOT serialize across
    multiple machines or containers.
    """

    def __init__(self, niche_id: str, lock_dir: Path | None = None):
        base = lock_dir or Path("/tmp")
        self.path = base / f"publisher-{niche_id}.lock"

    def acquire(self) -> bool:
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            # Lock file exists — check if holder is still alive
            try:
                pid = int(self.path.read_text().strip())
                os.kill(pid, 0)  # signal 0 = check if alive
                return False  # process is alive — lock held
            except (ValueError, ProcessLookupError, PermissionError):
                # Stale lock or unreadable — remove and retry once
                logger.info("Removing stale lock file: %s", self.path)
                self.path.unlink(missing_ok=True)
            except OSError:
                # Other OS errors — treat as stale
                self.path.unlink(missing_ok=True)
            # Retry atomic create after removing stale lock
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return True
            except FileExistsError:
                return False  # Another process won the race

    def release(self) -> None:
        self.path.unlink(missing_ok=True)
