"""Pre-publish crash recovery: reset blueprints stuck in transient
states before the main publish loop runs.

Two narrowly-scoped recovery passes run at the top of every publisher
invocation (including ``--retry-only`` mode):

* :func:`recover_stuck_publishing` — a blueprint in ``PUBLISHING``
  state that hasn't been touched for >30 min is almost certainly the
  victim of a crash mid-publish (the orchestrator died between
  ``status=PUBLISHING`` and the per-platform PUBLISHED/FAILED writes).
  Reset it to ``VISUAL_READY`` so the next run retries — unless the
  blueprint has already burned through its attempt budget, in which
  case promote it to ``PUBLISH_FAILED`` for human review.

* :func:`recover_publish_failed` — a blueprint in ``PUBLISH_FAILED``
  state for >24h gets auto-recovered to ``VISUAL_READY`` with
  ``publish_attempts`` zeroed. Most failure modes (token expiry,
  transient platform issues, rate limits) clear up within a day, so
  this gives the orchestrator one more shot before the human has to
  intervene.

Lives in its own module so the multi-platform publisher orchestrator
stays focused on the actual publish flow. Extracted in the
refactor-#9 decomposition (PR 6a/N).

Design choices
--------------
* Recovery is **strictly best-effort**: any exception during a recovery
  pass is logged at WARN and swallowed. The pass MUST NOT block the
  main publish — a SharePoint hiccup during recovery would otherwise
  silently dark the channel for the day.
* Per-platform recovery (R-29): we reset to ``VISUAL_READY`` rather
  than mark ``PUBLISHED``, because the per-platform publish loop now
  skips platforms that already have a PUBLISHED marker in
  ``platform_publish_status``. The next run retries only the platforms
  that actually failed.
* The thresholds (stale_after, cooldown, attempt_cap) are caller-
  injectable so tests can use small values and a future ops change can
  tune without code edits.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

# Default thresholds — match the historical inline behaviour.
DEFAULT_PUBLISHING_STALE_AFTER: timedelta = timedelta(minutes=30)
DEFAULT_PUBLISH_FAILED_COOLDOWN: timedelta = timedelta(hours=24)
DEFAULT_ATTEMPT_CAP: int = 3


def _parse_updated_at(value: Any) -> datetime | None:
    """Parse a SharePoint ``updated_at`` value to a tz-aware datetime.

    Returns ``None`` if the value is missing, empty, or malformed —
    callers treat that as "no usable timestamp" and skip the blueprint
    (better than guessing an age and acting on a junk record)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def recover_stuck_publishing(
    backlog_client: Any,
    niche_id: str,
    *,
    stale_after: timedelta = DEFAULT_PUBLISHING_STALE_AFTER,
    attempt_cap: int = DEFAULT_ATTEMPT_CAP,
    now: datetime | None = None,
) -> None:
    """Reset blueprints stuck in ``PUBLISHING`` past ``stale_after``.

    For each stuck blueprint:
      * ``publish_attempts >= attempt_cap`` → promoted to ``PUBLISH_FAILED``
        (human review needed; permanent failures should stop retrying).
      * otherwise → reset to ``VISUAL_READY`` for retry on the next run.

    Per-platform recovery (R-29): the reset path is ``VISUAL_READY``
    (NOT ``PUBLISHED``) so the per-platform publish loop's "skip already
    PUBLISHED" check retries only the platforms that actually failed.
    """
    now = now or datetime.now(UTC)
    try:
        stuck = backlog_client.get_blueprints_by_status("PUBLISHING", niche_id=niche_id)
    except Exception as e:
        logger.warning("[publish] PUBLISHING recovery check failed: %s", e)
        return

    for bp in stuck:
        fields = bp.get("fields", bp)
        updated_at = _parse_updated_at(fields.get("updated_at", ""))
        if updated_at is None:
            continue  # No usable timestamp — skip rather than act on junk.
        if now - updated_at <= stale_after:
            continue  # Not yet stale; the publish might still be in flight.

        attempt_count = int(fields.get("publish_attempts", 0) or 0)
        bp_id = bp["id"]
        try:
            if attempt_count >= attempt_cap:
                backlog_client.blueprints.update(bp_id, {"status": "PUBLISH_FAILED"})
                logger.error(
                    "[publish] Blueprint %s stuck after %d attempts — marking PUBLISH_FAILED",
                    bp_id[:8],
                    attempt_count,
                )
            else:
                backlog_client.blueprints.update(bp_id, {"status": "VISUAL_READY"})
                logger.warning(
                    "[publish] Recovered stuck PUBLISHING blueprint %s -> VISUAL_READY "
                    "(per-platform retry skips already-published)",
                    bp_id[:8],
                )
        except Exception as e:
            # Update failed for one blueprint — log and continue to the next.
            # Recovery is best-effort; one transient SharePoint write error
            # MUST NOT block recovery of sibling blueprints.
            logger.warning("[publish] Recovery update for blueprint %s failed: %s", bp_id[:8], e)


def recover_publish_failed(
    backlog_client: Any,
    niche_id: str,
    *,
    cooldown: timedelta = DEFAULT_PUBLISH_FAILED_COOLDOWN,
    now: datetime | None = None,
) -> None:
    """Auto-recover ``PUBLISH_FAILED`` blueprints past ``cooldown`` back to
    ``VISUAL_READY``, zeroing ``publish_attempts`` so the retry counter
    starts fresh.

    Most failure modes (token expiry, transient platform issues, rate
    limits) clear up within a day; this gives the publisher one more
    shot before a human has to intervene.
    """
    now = now or datetime.now(UTC)
    try:
        failed = backlog_client.get_blueprints_by_status("PUBLISH_FAILED", niche_id=niche_id)
    except Exception as e:
        logger.warning("[publish] PUBLISH_FAILED recovery check failed: %s", e)
        return

    for bp in failed:
        fields = bp.get("fields", bp)
        updated_at = _parse_updated_at(fields.get("updated_at", ""))
        if updated_at is None:
            continue
        if now - updated_at <= cooldown:
            continue

        bp_id = bp["id"]
        try:
            backlog_client.blueprints.update(
                bp_id,
                {"status": "VISUAL_READY", "publish_attempts": 0},
            )
            logger.info(
                "[publish] Auto-recovered PUBLISH_FAILED blueprint %s after %s cooldown",
                bp_id[:8],
                cooldown,
            )
        except Exception as e:
            logger.warning("[publish] Cooldown recovery for blueprint %s failed: %s", bp_id[:8], e)
