"""YouTube Data API v3 quota tracker.

The YouTube Data API enforces a 10,000 unit daily budget that resets at
midnight Pacific Time.  Video uploads alone cost 1,600 units, so a single
day supports at most six uploads before the budget is exhausted.

This module provides a thread-safe, persistent tracker that records every
API call's cost and exposes guards the publishing pipeline can check before
attempting expensive operations.

## Per-niche dimension (PR #536, 2026-06-24, SR-E foundation)

SYSTEM-RESEARCH.md §9 SR-E flagged the shared global counter as a
cross-tenant DoS risk: one runaway niche burning the 10K daily key
would starve the other 4. This module's foundational fix is to
TRACK usage per niche (in addition to global) so a follow-up PR can
add per-niche budget enforcement once callers are wired.

The migration is incremental, mirroring SR-C/SR-A:
  * PR #536 (this) — tracker accepts ``niche_id=`` on record/can_afford/
    status. Backward compat: ``niche_id=None`` preserves bit-for-bit
    behavior for the ~6 unmigrated callers.
  * PR #537 (follow-up) — wire trending_video_fetcher + platforms/youtube
    callers to pass niche_id (each has tenant context available).
  * PR #538 (follow-up) — add per-niche budget config (default
    DAILY_QUOTA/5 = 2000 units per niche) + enforce in can_afford.

State file layout (backward-compatible):

  Pre-PR-536:
    {"used": N, "upload_count": N, "reset_date": "..."}

  Post-PR-536 with niche_id usage:
    {"used": N, "upload_count": N, "reset_date": "...",
     "per_niche": {
         "gaming": {"used": N, "upload_count": N},
         "sports": {"used": N, "upload_count": N},
         ...
     }}

Operators reading an existing JSON without ``per_niche`` see no
behavior change (top-level keys unchanged). A reload of an old
state file produces an empty per-niche dict that the new
record() calls populate naturally.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import fcntl  # POSIX only — present on the macOS/Linux deploy targets
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# ── constants ──────────────────────────────────────────────────────────
DAILY_QUOTA: int = 10_000
UPLOAD_COST: int = 1_600
# 5 channels × 1600 units/upload = 8000 minimum. 90% of 10000 = 9000 only
# fits 5 uploads. Raised to 98% to allow all 5 channels + some headroom
# for search/analytics queries. Apply for YouTube quota increase (100K/day)
# to remove this constraint entirely.
HARD_STOP_PCT: float = 0.98

PACIFIC = ZoneInfo("America/Los_Angeles")

OPERATION_COSTS: dict[str, int] = {
    "upload": 1_600,
    "thumbnail_set": 50,
    "comment_list": 1,
    "analytics_query": 1,
    "channel_list": 1,
    "playlist_insert": 50,
    # Fetch-side ops (R-28): search.list is the 100-unit blow-up vector — a
    # config flip or RSS outage forcing keyword search across the 5 channels
    # could silently exhaust the 10k/day budget. Routing it through this
    # cross-process tracker enforces a global ceiling.
    "search": 100,
    "video_list": 1,
}

_DEFAULT_STATE_PATH = Path.home() / ".genlab" / "youtube_quota.json"


class YouTubeQuotaTracker:
    """Thread-safe, file-backed YouTube API quota tracker.

    Parameters
    ----------
    state_path:
        Path to the JSON file used for persistence.  Defaults to
        ``~/.genlab/youtube_quota.json``.
    daily_quota:
        Override the daily budget (useful for tests).
    """

    def __init__(
        self,
        state_path: Path | None = None,
        daily_quota: int = DAILY_QUOTA,
    ) -> None:
        self._lock = threading.Lock()
        self._state_path = Path(state_path) if state_path else _DEFAULT_STATE_PATH
        self._daily_quota = daily_quota
        self._hard_stop = int(self._daily_quota * HARD_STOP_PCT)

        # internal state — always accessed under _lock
        self._used: int = 0
        self._upload_count: int = 0
        self._reset_date: str = self._pacific_today()
        # PR #536: per-niche dimension. Map of niche_id -> {used,
        # upload_count}. Empty dict for pre-migration callers; populated
        # naturally as wired callers invoke record(..., niche_id="X").
        self._per_niche: dict[str, dict[str, int]] = {}

        self._load()

    # ── public API ─────────────────────────────────────────────────────

    def record(
        self,
        operation: str,
        count: int = 1,
        *,
        niche_id: str | None = None,
    ) -> int:
        """Record *count* invocations of *operation* and return total used today.

        Raises ``ValueError`` for unknown operations.  Logs a warning when
        cumulative usage crosses the 90 % threshold.

        PR #536 (SR-E foundation): ``niche_id`` parameter optionally
        attributes the spend to a specific tenant. When provided, the
        cost is added to BOTH the global counter AND the per-niche
        sub-counter. When omitted (the existing behavior), only the
        global counter is updated — backward-compat for all unmigrated
        callers.
        """
        cost_per = OPERATION_COSTS.get(operation)
        if cost_per is None:
            raise ValueError(
                f"Unknown operation {operation!r}. Known: {', '.join(sorted(OPERATION_COSTS))}"
            )

        total_cost = cost_per * count

        # R-55: the state file is shared by all 5 niche processes, so the
        # in-process threading.Lock is not enough — wrap the whole
        # read-modify-write in a cross-process flock and RE-READ the latest
        # state from disk before applying this delta, else a stale in-memory
        # `_used` clobbers another process's increment (lost update → the
        # quota silently overruns → uploadLimitExceeded).
        with self._lock, self._file_lock():
            self._reload_locked()
            self._maybe_reset(persist=False)
            self._used += total_cost
            if operation == "upload":
                self._upload_count += count

            # PR #536: per-niche attribution. Populated only when caller
            # supplies niche_id — preserves the existing per-niche dict
            # untouched for unmigrated callers.
            if niche_id:
                entry = self._per_niche.setdefault(niche_id, {"used": 0, "upload_count": 0})
                entry["used"] += total_cost
                if operation == "upload":
                    entry["upload_count"] += count

            if self._used >= self._hard_stop:
                logger.warning(
                    "YouTube quota at %d / %d (%.0f%%) — hard-stop threshold reached",
                    self._used,
                    self._daily_quota,
                    self._used / self._daily_quota * 100,
                )

            self._save()
            return self._used

    def can_upload(self) -> bool:
        """Return True if one more upload would stay within the 90 % budget."""
        with self._lock, self._file_lock():
            self._reload_locked()
            self._maybe_reset(persist=True)
            return (self._used + UPLOAD_COST) <= self._hard_stop

    def can_afford(
        self,
        operation: str,
        count: int = 1,
        *,
        niche_id: str | None = None,
    ) -> bool:
        """Return True if recording *count* of *operation* stays within budget.

        Used by the fetch path (R-28) to gate ``search.list`` (100 units) against
        the shared cross-process daily budget before spending it. Unknown
        operations cost 0 (always affordable) rather than raising, so a gate
        check never breaks a fetch.

        PR #536 (SR-E foundation): ``niche_id`` parameter accepted but
        NOT YET enforced — the per-niche budget check lands in PR #538.
        Today, ``niche_id`` is just plumbing: callers can wire it
        through ahead of time and the answer remains identical to the
        no-kwarg call. This staged approach lets the wire-up PRs land
        without behavior change, then a single config-flip PR turns on
        the per-niche enforcement.
        """
        cost = OPERATION_COSTS.get(operation, 0) * count
        with self._lock, self._file_lock():
            self._reload_locked()
            self._maybe_reset(persist=True)
            # NOTE: niche_id intentionally unused today (foundation only).
            # Future PR #538 will add: per-niche budget check via
            # self._per_niche.get(niche_id, {}).get("used", 0) + cost
            # against a per-niche cap from config.
            _ = niche_id
            return (self._used + cost) <= self._hard_stop

    def daily_uploads_used(self) -> int:
        """Return the number of uploads recorded today."""
        with self._lock, self._file_lock():
            self._reload_locked()
            self._maybe_reset(persist=True)
            return self._upload_count

    def status(self, *, niche_id: str | None = None) -> dict[str, object]:
        """Return a snapshot of today's quota state.

        PR #536 (SR-E foundation): when ``niche_id`` is provided, the
        returned dict ALSO includes per-niche detail under a
        ``niche`` key (used, upload_count for that tenant). The top-level
        keys remain global-shape so existing dashboards / log scrapers
        keep working unchanged.

        When ``niche_id`` is omitted, output is bit-for-bit identical
        to the pre-PR shape — backward compat for ~10 status() callers
        that don't yet know about per-niche.
        """
        with self._lock, self._file_lock():
            self._reload_locked()
            self._maybe_reset(persist=True)
            remaining = max(self._daily_quota - self._used, 0)
            out: dict[str, object] = {
                "used": self._used,
                "remaining": remaining,
                "pct_used": round(self._used / self._daily_quota * 100, 1),
                # 2026-06-15 audit T#65: expose hard_stop so the publisher's
                # quota-gate error message can show the actual cap (9800 by
                # default), not the raw 10K Google ceiling. Previous message
                # "8600/10000 units" displayed 86% when the gate was actually
                # at 88% of the 9800u hard-stop — operator read it as
                # "headroom remaining" when there wasn't any.
                "hard_stop": self._hard_stop,
                "pct_of_hard_stop": round(self._used / self._hard_stop * 100, 1),
                "upload_count": self._upload_count,
                "reset_date": self._reset_date,
            }
            if niche_id:
                # Per-niche snapshot — defaults to zeros if this tenant
                # hasn't spent anything today. Surfaced under "niche"
                # so the top-level shape is preserved.
                niche_state = self._per_niche.get(niche_id, {"used": 0, "upload_count": 0})
                out["niche"] = {
                    "niche_id": niche_id,
                    "used": niche_state["used"],
                    "upload_count": niche_state["upload_count"],
                }
            return out

    # ── internals ──────────────────────────────────────────────────────

    def _maybe_reset(self, persist: bool = True) -> None:
        """Clear counters when the Pacific date rolls over.

        Must be called under ``self._lock`` (and, for cross-process safety,
        the file lock).  ``persist=False`` skips the write so a caller that is
        about to ``_save()`` anyway doesn't write twice.

        PR #536: per-niche counters reset alongside global. Without
        this, day-1 spend would leak into day-2's quota check.
        """
        today = self._pacific_today()
        if today != self._reset_date:
            logger.info(
                "YouTube quota reset: %s → %s (was %d used)",
                self._reset_date,
                today,
                self._used,
            )
            self._used = 0
            self._upload_count = 0
            self._per_niche = {}
            self._reset_date = today
            if persist:
                self._save()

    @contextlib.contextmanager
    def _file_lock(self) -> Iterator[None]:
        """Cross-process exclusive lock around the shared quota file (R-55).

        All 5 niche processes share one state file; ``flock`` serializes their
        read-modify-write cycles. Best-effort no-op on non-POSIX platforms.
        """
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        if fcntl is None:  # pragma: no cover - non-POSIX fallback
            yield
            return
        lock_path = self._state_path.with_name(self._state_path.name + ".lock")
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_f, fcntl.LOCK_UN)

    def _reload_locked(self) -> None:
        """Re-read the freshest state from disk; call under the file lock."""
        self._load()

    def _load(self) -> None:
        """Load persisted state from disk (if present).

        PR #536: also loads ``per_niche`` map if present in the JSON.
        Backward compat: pre-PR-536 state files have no ``per_niche``
        key — the get() defaults to {} and the dict stays empty until
        wired callers populate it.
        """
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._used = int(data.get("used", 0))
            self._upload_count = int(data.get("upload_count", 0))
            self._reset_date = str(data.get("reset_date", self._pacific_today()))
            # Defensive coercion: trust the file's shape but normalize
            # values to int so a corrupt entry doesn't poison the
            # in-memory state with strings/None.
            raw_per_niche = data.get("per_niche", {}) or {}
            if isinstance(raw_per_niche, dict):
                # Per-entry try/except: one corrupt niche entry shouldn't
                # drop the whole map — the goal is "best-effort restore",
                # not "all-or-nothing". A bad value coerces to 0 so the
                # next record() call can still update it without crashing.
                clean: dict[str, dict[str, int]] = {}
                for nid, entry in raw_per_niche.items():
                    if not isinstance(entry, dict):
                        continue
                    try:
                        used = int(entry.get("used", 0) or 0)
                    except (TypeError, ValueError):
                        used = 0
                    try:
                        upload_count = int(entry.get("upload_count", 0) or 0)
                    except (TypeError, ValueError):
                        upload_count = 0
                    clean[str(nid)] = {"used": used, "upload_count": upload_count}
                self._per_niche = clean
            else:
                self._per_niche = {}
        except (json.JSONDecodeError, OSError, ValueError) as exc:
            logger.warning("Failed to load quota state from %s: %s", self._state_path, exc)

    def _save(self) -> None:
        """Persist current state atomically (temp file + os.replace).

        Must be called under ``self._lock`` and the file lock. The atomic
        rename means a crash mid-write can never truncate the shared file
        (which previously made ``_load`` reset the counter to zero — R-55).
        """
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, object] = {
            "used": self._used,
            "upload_count": self._upload_count,
            "reset_date": self._reset_date,
            "updated_at": datetime.now(tz=PACIFIC).isoformat(),
        }
        # PR #536: per-niche map only persisted when populated. Keeps
        # the JSON minimal for the common case (pre-migration callers
        # leave it empty) so operators tailing the file see no noise.
        if self._per_niche:
            payload["per_niche"] = self._per_niche
        tmp_path = self._state_path.with_name(f"{self._state_path.name}.tmp.{os.getpid()}")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload, indent=2) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._state_path)
        except OSError as exc:
            logger.error("Failed to save quota state to %s: %s", self._state_path, exc)
            with contextlib.suppress(OSError):
                if tmp_path.exists():
                    tmp_path.unlink()

    @staticmethod
    def _pacific_today() -> str:
        """Return today's date in Pacific Time as an ISO string."""
        return datetime.now(tz=PACIFIC).strftime("%Y-%m-%d")
