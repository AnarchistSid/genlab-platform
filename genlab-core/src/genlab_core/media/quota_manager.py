"""Disk quota management for media workspaces with score-weighted eviction."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class QuotaStatus:
    """Snapshot of current disk usage against quota."""

    used_bytes: int
    max_bytes: int
    pct: float  # used/max ratio
    level: str  # "ok", "warning", "critical"


class QuotaManager:
    """Manages disk quota for media workspaces with score-weighted eviction."""

    def __init__(
        self,
        base_dir: Path,
        max_bytes: int = 50 * 1024**3,  # 50 GB default
        warning_pct: float = 0.80,
        critical_pct: float = 0.95,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.max_bytes = max_bytes
        self.warning_pct = warning_pct
        self.critical_pct = critical_pct
        self._workspaces: list[VideoWorkspace] = []

    def current_usage_bytes(self) -> int:
        """Calculate total bytes used under base_dir."""
        if not self.base_dir.exists():
            return 0
        total = 0
        for f in self.base_dir.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
        return total

    def check(self) -> QuotaStatus:
        """Return current quota status."""
        used = self.current_usage_bytes()
        pct = used / self.max_bytes if self.max_bytes > 0 else 0.0
        if pct >= self.critical_pct:
            level = "critical"
        elif pct >= self.warning_pct:
            level = "warning"
        else:
            level = "ok"
        return QuotaStatus(used_bytes=used, max_bytes=self.max_bytes, pct=pct, level=level)

    def evict_if_needed(self, protected_dirs: set[Path] | None = None) -> int:
        """Evict lowest-score, oldest dirs until usage drops below warning threshold.

        Returns the number of bytes freed.
        """
        protected = {Path(p).resolve() for p in protected_dirs} if protected_dirs else set()
        target_bytes = int(self.warning_pct * self.max_bytes)

        used = self.current_usage_bytes()
        if used <= target_bytes:
            return 0

        # Collect candidate subdirectories with their scores and mtimes
        candidates: list[tuple[float, float, Path]] = []
        for entry in self.base_dir.iterdir():
            if not entry.is_dir():
                continue
            if entry.resolve() in protected:
                continue
            # Read score from .score file, default to 0.0
            score = 0.0
            score_file = entry / ".score"
            if score_file.exists():
                try:
                    score = float(score_file.read_text().strip())
                except (ValueError, OSError):
                    pass
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((score, mtime, entry))

        # Sort by score ascending, then mtime ascending (lowest score + oldest first)
        candidates.sort(key=lambda c: (c[0], c[1]))

        freed = 0
        for _score, _mtime, dir_path in candidates:
            if used - freed <= target_bytes:
                break
            # Measure dir size before deleting
            dir_size = 0
            for f in dir_path.rglob("*"):
                if f.is_file():
                    try:
                        dir_size += f.stat().st_size
                    except OSError:
                        pass
            logger.info(
                "[QuotaManager] Evicting %s (score=%.2f, size=%d bytes)",
                dir_path.name,
                _score,
                dir_size,
            )
            shutil.rmtree(dir_path, ignore_errors=True)
            freed += dir_size

        return freed

    def register_workspace(self, workspace: VideoWorkspace) -> None:
        """Register a workspace for tracking."""
        self._workspaces.append(workspace)

    def pressure_check(self) -> None:
        """Run at pipeline startup. Evict if above warning threshold."""
        status = self.check()
        if status.level == "critical":
            logger.warning(
                "[QuotaManager] CRITICAL disk pressure: %.1f%% used, evicting...",
                status.pct * 100,
            )
            freed = self.evict_if_needed()
            logger.info("[QuotaManager] Freed %d bytes", freed)
        elif status.level == "warning":
            logger.info(
                "[QuotaManager] Disk pressure warning: %.1f%% used",
                status.pct * 100,
            )


class VideoWorkspace:
    """Context manager that creates a temp workspace dir and cleans up on exit."""

    def __init__(self, base_dir: Path, run_id: str, score: float = 0.0) -> None:
        self.base_dir = Path(base_dir)
        self.path = self.base_dir / run_id
        self.score = score

    def __enter__(self) -> VideoWorkspace:
        self.path.mkdir(parents=True, exist_ok=True)
        (self.path / ".score").write_text(str(self.score))
        return self

    def __exit__(self, *exc: object) -> None:
        # Don't auto-cleanup — let QuotaManager handle eviction
        pass

    def write_score(self, score: float) -> None:
        """Update score (e.g., after rendering shows this content performed well)."""
        self.score = score
        (self.path / ".score").write_text(str(score))
