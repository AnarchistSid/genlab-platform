"""YouTube Data API v3 quota tracker.

The YouTube Data API enforces a 10,000 unit daily budget that resets at
midnight Pacific Time.  Video uploads alone cost 1,600 units, so a single
day supports at most six uploads before the budget is exhausted.

This module provides a thread-safe, persistent tracker that records every
API call's cost and exposes guards the publishing pipeline can check before
attempting expensive operations.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

        self._load()

    # ── public API ─────────────────────────────────────────────────────

    def record(self, operation: str, count: int = 1) -> int:
        """Record *count* invocations of *operation* and return total used today.

        Raises ``ValueError`` for unknown operations.  Logs a warning when
        cumulative usage crosses the 90 % threshold.
        """
        cost_per = OPERATION_COSTS.get(operation)
        if cost_per is None:
            raise ValueError(
                f"Unknown operation {operation!r}. "
                f"Known: {', '.join(sorted(OPERATION_COSTS))}"
            )

        total_cost = cost_per * count

        with self._lock:
            self._maybe_reset()
            self._used += total_cost
            if operation == "upload":
                self._upload_count += count

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
        with self._lock:
            self._maybe_reset()
            return (self._used + UPLOAD_COST) <= self._hard_stop

    def daily_uploads_used(self) -> int:
        """Return the number of uploads recorded today."""
        with self._lock:
            self._maybe_reset()
            return self._upload_count

    def status(self) -> dict[str, object]:
        """Return a snapshot of today's quota state."""
        with self._lock:
            self._maybe_reset()
            remaining = max(self._daily_quota - self._used, 0)
            return {
                "used": self._used,
                "remaining": remaining,
                "pct_used": round(self._used / self._daily_quota * 100, 1),
                "upload_count": self._upload_count,
                "reset_date": self._reset_date,
            }

    # ── internals ──────────────────────────────────────────────────────

    def _maybe_reset(self) -> None:
        """Clear counters when the Pacific date rolls over.

        Must be called under ``self._lock``.
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
            self._reset_date = today
            self._save()

    def _load(self) -> None:
        """Load persisted state from disk (if present).

        Called once during ``__init__`` — no lock needed at that point.
        """
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._used = int(data.get("used", 0))
            self._upload_count = int(data.get("upload_count", 0))
            self._reset_date = str(data.get("reset_date", self._pacific_today()))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load quota state from %s: %s", self._state_path, exc)

    def _save(self) -> None:
        """Persist current state to disk.

        Must be called under ``self._lock``.
        """
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "used": self._used,
            "upload_count": self._upload_count,
            "reset_date": self._reset_date,
            "updated_at": datetime.now(tz=PACIFIC).isoformat(),
        }
        try:
            self._state_path.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            logger.error("Failed to save quota state to %s: %s", self._state_path, exc)

    @staticmethod
    def _pacific_today() -> str:
        """Return today's date in Pacific Time as an ISO string."""
        return datetime.now(tz=PACIFIC).strftime("%Y-%m-%d")
