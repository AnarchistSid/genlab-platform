"""Score-weighted disk quota management for Gen Lab agents.

Enforces per-agent disk quotas with intelligent eviction:
- Two-pass eviction: heavy subdirs (clips/) first, then full runs
- Score-weighted: lowest-scoring runs evicted first
- Protection: published runs and N most recent runs never evicted

Usage:
    manager = DiskQuotaManager(config)
    status = manager.get_status("content_scraper")
    ok = manager.preflight_check("content_scraper", estimated_bytes=5_000_000_000)
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_QUOTA_GB = 50
_DEFAULT_WARN_PCT = 80
_DEFAULT_EVICT_PCT = 90
_DEFAULT_PROTECT_RECENT = 3
# Subdirectories to evict in pass 1 (before deleting full runs).
# These are the largest space consumers that can be regenerated.
_HEAVY_SUBDIRS = ("clips",)
# Default max-age for ``extra_dirs:`` entries when an entry omits it.
_DEFAULT_EXTRA_DIR_MAX_AGE_DAYS = 7


@dataclass
class RunRecord:
    """Metadata for a single pipeline run directory."""

    run_id: str
    path: Path
    size_bytes: int
    created_at: float  # Unix timestamp (mtime)
    score: float  # 0.0-1.0, higher = more valuable
    is_published: bool
    clips_bytes: int = 0
    rendered_bytes: int = 0


@dataclass
class QuotaStatus:
    """Current disk quota status for an agent."""

    agent: str
    quota_bytes: int
    used_bytes: int
    pct_used: float
    run_count: int
    evictable_count: int
    evictable_bytes: int
    over_quota: bool
    over_warn: bool


# ── Helpers ──────────────────────────────────────────────────


def _dir_size(path: Path) -> int:
    """Total size in bytes of all files under *path*."""
    if not path.exists():
        return 0
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                try:
                    total += f.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _extract_score(run_dir: Path) -> float:
    """Extract a representative quality score from run artifacts.

    Priority:
      1. run_report.json -> metrics.score_distribution.top5_avg
      2. blueprint_pack.json -> mean(priority_score)
      3. Default 0.0 (unscored runs evict first)
    """
    report_path = run_dir / "run_report.json"
    if report_path.is_file():
        try:
            with open(report_path) as f:
                report = json.load(f)
            top5 = report.get("metrics", {}).get("score_distribution", {}).get("top5_avg")
            if top5 is not None:
                return float(top5)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    bp_path = run_dir / "blueprint_pack.json"
    if bp_path.is_file():
        try:
            with open(bp_path) as f:
                data = json.load(f)
            items = (
                data
                if isinstance(data, list)
                else data.get("blueprints", data.get("candidates", []))
            )
            scores = [
                item.get("priority_score", item.get("composite_score", 0))
                for item in items
                if isinstance(item, dict)
            ]
            if scores:
                return sum(scores) / len(scores)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    return 0.0


def _is_published(run_dir: Path) -> bool:
    """Detect whether a run contains published content."""
    if (run_dir / "publish_all_summary.json").is_file():
        return True
    if run_dir.name.endswith("_pub"):
        return True
    return False


def _get_pending_publish_run_ids() -> set[str]:
    """Return run_ids referenced by scheduled-but-unpublished blueprints.

    These runs must be protected from eviction even though they don't yet
    have a `publish_all_summary.json` — losing their visuals turns the
    blueprint into a dead schedule. Mac/Hetzner split-brain on 2026-04-29
    surfaced this: 75 scheduled blueprints had Hetzner-resident visuals
    purged because the host run hadn't published yet.

    Returns an empty set on any DB failure — better to risk eviction of a
    pending run than to crash the cleanup loop and let the disk fill up.
    """
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if not db_url:
        return set()
    try:
        import psycopg
    except ImportError:
        return set()
    try:
        with psycopg.connect(db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT extra->>'visual_paths'
                    FROM blueprints
                    WHERE scheduled_for IS NOT NULL
                      AND status NOT IN ('PUBLISHED', 'ARCHIVED', 'PUBLISH_FAILED')
                      AND extra ? 'visual_paths'
                    """
                )
                rows = cur.fetchall()
    except Exception as exc:
        logger.warning(
            "[QUOTA] Could not query pending blueprints (%s) — pending runs unprotected this pass",
            exc,
        )
        return set()

    run_ids: set[str] = set()
    for (vp_raw,) in rows:
        if not vp_raw:
            continue
        try:
            paths = json.loads(vp_raw) if vp_raw.startswith("[") else [vp_raw]
        except (json.JSONDecodeError, ValueError):
            paths = [vp_raw]
        for p in paths:
            if not isinstance(p, str):
                continue
            # Extract <run_id> from any segment after a /runs/ or /rendered/
            # marker — covers both /opt/genlab/.tmp/runs/<id>/... and the
            # CriticalRush /<x>/.tmp/rendered/<id>/... layout.
            for marker in ("/runs/", "/rendered/"):
                idx = p.find(marker)
                if idx >= 0:
                    rest = p[idx + len(marker) :]
                    run_id = rest.split("/", 1)[0]
                    if run_id:
                        run_ids.add(run_id)
                    break
    if run_ids:
        logger.info(
            "[QUOTA] Protecting %d run(s) referenced by pending scheduled blueprints", len(run_ids)
        )
    return run_ids


def _filesystem_free_bytes(path: Path) -> int:
    """Free bytes on the filesystem containing *path*, or -1 if unknown."""
    try:
        stat = os.statvfs(path)
        return stat.f_bavail * stat.f_frsize
    except (OSError, AttributeError):
        return -1


# ── Manager ──────────────────────────────────────────────────


class DiskQuotaManager:
    """Score-weighted disk quota enforcement.

    Config format::

        agents:
          content_scraper:
            runs_dir: "/path/to/.tmp/runs"
            quota_gb: 50
            warn_pct: 80
            evict_pct: 90
            protect_recent: 3
    """

    def __init__(self, config: dict[str, Any]):
        self._config = config

    @classmethod
    def from_yaml(cls, path: Path | str) -> DiskQuotaManager:
        import yaml

        with open(path) as f:
            return cls(yaml.safe_load(f) or {})

    # ── Config accessors ─────────────────────────────────────

    def _agent_cfg(self, agent: str) -> dict[str, Any]:
        return self._config.get("agents", {}).get(agent, {})

    def _runs_dir(self, agent: str) -> Path:
        cfg = self._agent_cfg(agent)
        runs_dir = cfg.get("runs_dir")
        if not runs_dir:
            raise ValueError(f"No runs_dir configured for agent {agent!r}")
        return Path(runs_dir)

    def _quota_bytes(self, agent: str) -> int:
        gb = self._agent_cfg(agent).get("quota_gb", _DEFAULT_QUOTA_GB)
        return int(float(gb) * (1024**3))

    def _warn_pct(self, agent: str) -> float:
        return float(self._agent_cfg(agent).get("warn_pct", _DEFAULT_WARN_PCT))

    def _evict_pct(self, agent: str) -> float:
        return float(self._agent_cfg(agent).get("evict_pct", _DEFAULT_EVICT_PCT))

    def _protect_recent(self, agent: str) -> int:
        return int(self._agent_cfg(agent).get("protect_recent", _DEFAULT_PROTECT_RECENT))

    # ── Core API ─────────────────────────────────────────────

    def scan_runs(self, agent: str) -> list[RunRecord]:
        """Scan and return metadata for all run directories, newest first."""
        runs_dir = self._runs_dir(agent)
        if not runs_dir.is_dir():
            return []

        # Treat runs referenced by pending scheduled blueprints as published —
        # evicting them would orphan an active schedule and the publisher would
        # later auto-archive the blueprint with `auto_archived_missing_media`.
        pending_run_ids = _get_pending_publish_run_ids()

        records: list[RunRecord] = []
        for entry in runs_dir.iterdir():
            if not entry.is_dir():
                continue

            try:
                created = entry.stat().st_mtime
            except OSError:
                created = 0.0

            records.append(
                RunRecord(
                    run_id=entry.name,
                    path=entry,
                    size_bytes=_dir_size(entry),
                    created_at=created,
                    score=_extract_score(entry),
                    is_published=_is_published(entry) or entry.name in pending_run_ids,
                    clips_bytes=_dir_size(entry / "clips"),
                    rendered_bytes=_dir_size(entry / "rendered"),
                )
            )

        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def get_status(self, agent: str) -> QuotaStatus:
        """Current quota status for an agent."""
        records = self.scan_runs(agent)
        quota = self._quota_bytes(agent)
        used = sum(r.size_bytes for r in records)
        protect_n = self._protect_recent(agent)

        evictable = [r for r in records[protect_n:] if not r.is_published]
        evictable_bytes = sum(r.size_bytes for r in evictable)
        pct = (used / quota * 100) if quota > 0 else 0.0

        return QuotaStatus(
            agent=agent,
            quota_bytes=quota,
            used_bytes=used,
            pct_used=round(pct, 1),
            run_count=len(records),
            evictable_count=len(evictable),
            evictable_bytes=evictable_bytes,
            over_quota=pct >= self._evict_pct(agent),
            over_warn=pct >= self._warn_pct(agent),
        )

    def evict(
        self,
        agent: str,
        target_free_bytes: int | None = None,
    ) -> list[str]:
        """Two-pass score-weighted eviction.

        Pass 1: Delete clips/ from ANY non-published run (including recent).
                Clips are regenerable and should not be shielded by protect_recent.
        Pass 2: Delete full run directories (only unprotected, non-published).

        Returns list of paths removed.
        """
        records = self.scan_runs(agent)
        quota = self._quota_bytes(agent)
        used = sum(r.size_bytes for r in records)
        protect_n = self._protect_recent(agent)

        if target_free_bytes is not None:
            current_free = max(0, quota - used)
            need_to_free = target_free_bytes - current_free
        else:
            # Bring usage below warn_pct to create headroom
            target_used = int(quota * self._warn_pct(agent) / 100)
            need_to_free = used - target_used

        if need_to_free <= 0:
            return []

        evicted_paths: list[str] = []
        freed = 0

        # Pass 1: delete heavy subdirectories from ALL runs (including
        # published and recent).  Clips are regenerable source material —
        # the final rendered output lives elsewhere in the run dir.
        # Sorted by score ascending (lowest value evicted first).
        clips_candidates = sorted(records, key=lambda r: r.score)
        for record in clips_candidates:
            if freed >= need_to_free:
                break
            for subdir_name in _HEAVY_SUBDIRS:
                subdir = record.path / subdir_name
                if subdir.is_dir():
                    size = _dir_size(subdir)
                    if size == 0:
                        continue
                    try:
                        shutil.rmtree(subdir)
                        freed += size
                        evicted_paths.append(str(subdir))
                        logger.info(
                            "[QUOTA] Pass 1: removed %s (%.1f MB, score=%.3f)",
                            subdir,
                            size / (1024**2),
                            record.score,
                        )
                    except OSError as e:
                        logger.warning("[QUOTA] Failed to remove %s: %s", subdir, e)

        # Pass 2: delete full run directories (not published, not recent).
        if freed < need_to_free:
            evictable_full = sorted(
                [r for r in records[protect_n:] if not r.is_published],
                key=lambda r: r.score,
            )
            for record in evictable_full:
                if freed >= need_to_free:
                    break
                if not record.path.exists():
                    continue
                size = _dir_size(record.path)
                if size == 0:
                    continue
                try:
                    shutil.rmtree(record.path)
                    freed += size
                    evicted_paths.append(str(record.path))
                    logger.info(
                        "[QUOTA] Pass 2: removed %s (%.1f MB, score=%.3f)",
                        record.path,
                        size / (1024**2),
                        record.score,
                    )
                except OSError as e:
                    logger.warning("[QUOTA] Failed to remove %s: %s", record.path, e)

        logger.info(
            "[QUOTA] Eviction complete: freed %.1f MB across %d paths",
            freed / (1024**2),
            len(evicted_paths),
        )
        return evicted_paths

    def preflight_check(
        self,
        agent: str,
        estimated_bytes: int = 0,
    ) -> bool:
        """Pre-flight check before a pipeline run.

        Returns True if there is (or can be made) enough space.
        Triggers eviction automatically when projected usage exceeds evict_pct.
        """
        status = self.get_status(agent)
        quota = status.quota_bytes
        if quota <= 0:
            return True

        projected_pct = (status.used_bytes + estimated_bytes) / quota * 100

        if projected_pct < self._evict_pct(agent):
            if projected_pct >= self._warn_pct(agent):
                logger.warning(
                    "[QUOTA] %s at %.1f%% (projected %.1f%%) — approaching limit",
                    agent,
                    status.pct_used,
                    projected_pct,
                )
            return True

        # Over quota — try eviction
        logger.warning("[QUOTA] %s projected %.1f%% — triggering eviction", agent, projected_pct)
        self.evict(agent, target_free_bytes=estimated_bytes)

        # Re-check after eviction
        status_after = self.get_status(agent)
        projected_after = (
            (status_after.used_bytes + estimated_bytes) / quota * 100 if quota > 0 else 0
        )

        if projected_after >= self._evict_pct(agent):
            logger.error(
                "[QUOTA] %s still at %.1f%% after eviction — cannot proceed",
                agent,
                projected_after,
            )
            return False

        # Also check real filesystem space
        fs_free = _filesystem_free_bytes(self._runs_dir(agent))
        if fs_free >= 0 and estimated_bytes > 0 and fs_free < estimated_bytes:
            logger.error(
                "[QUOTA] Filesystem has only %.1f GB free, need %.1f GB",
                fs_free / (1024**3),
                estimated_bytes / (1024**3),
            )
            return False

        return True

    # ── Extra-dir sweeper ─────────────────────────────────────

    def sweep_extra_dirs(self, *, now: float | None = None) -> dict[str, int]:
        """Delete entries in ``extra_dirs:`` whose mtime is older than
        ``max_age_days``. Returns a mapping of path → bytes freed.

        Closes the leak audit-finding P-9: the per-agent ``scan_runs``
        only walks each agent's ``runs_dir``, so shared scratch trees
        (``.tmp/rerender``, ``.tmp/cache``, ``.tmp/logs``) accumulated
        until the disk filled. 408 MB of ``.tmp/rerender`` dating to
        2026-03-27 was found during the 2026-06-08 audit — those would
        have stayed forever.

        Strictly best-effort: per-entry errors are logged at WARN and
        skipped. ``now`` is injected for deterministic tests.
        """
        now = now or time.time()
        freed: dict[str, int] = {}
        for entry in self._config.get("extra_dirs", []) or []:
            raw_path = entry.get("path") if isinstance(entry, dict) else None
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            if not path.is_dir():
                # Not a leak — the directory simply doesn't exist on
                # this host yet. (Common when a niche hasn't run.)
                continue
            max_age_days = int(entry.get("max_age_days", _DEFAULT_EXTRA_DIR_MAX_AGE_DAYS) or 0)
            if max_age_days <= 0:
                logger.warning("[disk_quota] skipping %s: max_age_days must be > 0", path)
                continue
            cutoff = now - max_age_days * 86400

            bytes_freed = 0
            for child in path.iterdir():
                try:
                    mtime = child.stat().st_mtime
                except OSError:
                    continue
                if mtime > cutoff:
                    continue
                try:
                    if child.is_dir():
                        size = _dir_size(child)
                        shutil.rmtree(child, ignore_errors=True)
                        bytes_freed += size
                    else:
                        size = child.stat().st_size
                        child.unlink(missing_ok=True)
                        bytes_freed += size
                except OSError as exc:
                    logger.warning("[disk_quota] sweep failed for %s: %s", child, exc)

            if bytes_freed:
                logger.info(
                    "[disk_quota] swept %s (>%dd old) — freed %.1f MB",
                    path,
                    max_age_days,
                    bytes_freed / (1024 * 1024),
                )
            freed[str(path)] = bytes_freed
        return freed
