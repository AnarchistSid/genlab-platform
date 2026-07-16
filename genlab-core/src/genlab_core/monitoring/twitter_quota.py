"""X/Twitter free-tier quota tracker (500 posts/month per app).

Free-tier X API v2 caps at 500 tweets/month per app-scoped credential.
When exceeded, the API returns 402 (Payment Required) and the app is
locked out for the remainder of the month. Live evidence 2026-07-14:
ai_creators X publish returned 402 unexpectedly.

Prior state: no proactive tracking → discover the 500-post limit only
when Twitter enforces it, causing niche outages that persist until
month rollover.

This module ships a file-backed counter that:
  * Increments on every successful publish
  * Resets automatically at month boundary
  * Exposes ``can_publish()`` for pre-publish check
  * Fail-open on all state errors (never blocks legitimate publishes
    due to state-file corruption or missing directory)

Design mirrors ``youtube_quota.YouTubeQuotaTracker`` but simpler:
monthly cadence (not daily), no per-endpoint cost accounting (each
publish = 1 unit), no soft-warning tiers (400/500 is enough
runway).

Follow-up: multi-niche X publishing (Phase 2 SaaS) will need
per-niche counters — refactor to use ``niche_id`` as state key when
that lands.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Free-tier limit is 500 tweets/month per app credential. Twitter
# doesn't distinguish "post" from "reply" — both count. We reserve
# 20 units of headroom for engagement replies (which we can't
# accurately count in advance) so publish stops at 480.
FREE_TIER_MONTHLY_CAP = 500
PUBLISH_LIMIT = 480  # 500 - 20 engagement headroom
WARNING_THRESHOLD = 0.80  # log WARNING when 80% of monthly cap used

_DEFAULT_STATE_PATH = Path(
    os.environ.get("GENLAB_PROJECT_ROOT", ".")
) / ".runtime" / "twitter_quota.json"


class TwitterQuotaTracker:
    """File-backed monthly counter for X/Twitter free-tier posts.

    Usage
    -----
    ::

        tracker = TwitterQuotaTracker()
        if not tracker.can_publish():
            log.warning("Twitter monthly cap hit; skipping publish")
            return
        # ... perform publish ...
        tracker.record_publish()
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self._state_path = state_path or _DEFAULT_STATE_PATH

    # ---- public API -------------------------------------------------

    def can_publish(self, cost: int = 1) -> tuple[bool, int]:
        """Check whether we have quota headroom for ``cost`` publishes.

        Returns ``(True, remaining)`` if publish is allowed. Returns
        ``(False, remaining)`` if cap would be exceeded.

        Fail-open: state-read failure returns ``(True, PUBLISH_LIMIT)``
        so a corrupt state file doesn't lock out publishes. The
        WARNING log makes the failure visible.
        """
        state = self._read_state()
        used = state.get("used", 0)
        remaining = max(0, PUBLISH_LIMIT - used)
        allowed = (used + cost) <= PUBLISH_LIMIT
        return allowed, remaining

    def record_publish(self, count: int = 1) -> int:
        """Increment counter by ``count`` publishes. Returns new total.

        Fail-open: state-write failure logs WARNING but doesn't raise.
        Next check may double-count until the state file heals.
        """
        state = self._read_state()
        state["used"] = state.get("used", 0) + count
        state["last_updated"] = datetime.now(UTC).isoformat()
        self._write_state(state)

        used = state["used"]
        pct = used / FREE_TIER_MONTHLY_CAP
        if pct >= WARNING_THRESHOLD:
            logger.warning(
                "[twitter_quota] monthly cap %.0f%% used (%d/%d) — "
                "publishes will halt at %d (headroom for engagement replies)",
                pct * 100,
                used,
                FREE_TIER_MONTHLY_CAP,
                PUBLISH_LIMIT,
            )
        else:
            logger.info(
                "[twitter_quota] recorded publish, monthly used=%d/%d",
                used,
                FREE_TIER_MONTHLY_CAP,
            )
        return used

    def reset_for_month(self, month_key: str | None = None) -> None:
        """Reset counter for a new month. Called automatically by
        ``_read_state`` when the persisted month_key doesn't match
        the current month. Exposed for tests + operator-forced reset.
        """
        if month_key is None:
            month_key = datetime.now(UTC).strftime("%Y-%m")
        self._write_state({"month": month_key, "used": 0})
        logger.info("[twitter_quota] reset for month=%s", month_key)

    def current_state(self) -> dict:
        """Return the currently-persisted state for observability."""
        return dict(self._read_state())

    # ---- state I/O --------------------------------------------------

    def _read_state(self) -> dict:
        """Read state file. Returns ``{"month": ..., "used": ...}``.

        Auto-resets counter if persisted month != current month. Fail-
        open with fresh state on any read/parse error.
        """
        current_month = datetime.now(UTC).strftime("%Y-%m")
        try:
            if not self._state_path.exists():
                return {"month": current_month, "used": 0}
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("state file is not a dict")
            persisted_month = data.get("month", "")
            if persisted_month != current_month:
                # Month rolled over — reset counter.
                logger.info(
                    "[twitter_quota] month rollover %s → %s, resetting counter",
                    persisted_month,
                    current_month,
                )
                fresh = {"month": current_month, "used": 0}
                self._write_state(fresh)
                return fresh
            return data
        except Exception as exc:
            logger.warning(
                "[twitter_quota] state read failed (%s) — treating as fresh; "
                "publishes will proceed. State path: %s",
                exc,
                self._state_path,
            )
            return {"month": current_month, "used": 0}

    def _write_state(self, state: dict) -> None:
        """Write state via temp-file + rename for atomicity.

        fcntl.flock on a sidecar lock file serialises concurrent
        publisher invocations. Without the lock, two parallel publishes
        can each read `used=N`, each increment to `N+1`, and each
        write `N+1` — one increment silently lost. On the 500/mo write
        cap this class-of-bug would appear as false-negative quota
        headroom.
        """
        import fcntl

        lock_path = self._state_path.with_suffix(".lock")
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_path, "w", encoding="utf-8") as lock_fp:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
                tmp = self._state_path.with_name(
                    f"{self._state_path.name}.tmp.{os.getpid()}"
                )
                tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
                os.replace(tmp, self._state_path)
        except Exception as exc:
            logger.warning(
                "[twitter_quota] state write failed (%s) — next check may "
                "misreport; state path: %s",
                exc,
                self._state_path,
            )


# Module-level singleton so all publish sites share the same counter.
_TRACKER: TwitterQuotaTracker | None = None


def get_tracker() -> TwitterQuotaTracker:
    """Return the process-wide singleton tracker."""
    global _TRACKER
    if _TRACKER is None:
        _TRACKER = TwitterQuotaTracker()
    return _TRACKER


def _reset_singleton_for_tests() -> None:
    """Testing helper — force re-instantiation of the singleton."""
    global _TRACKER
    _TRACKER = None
