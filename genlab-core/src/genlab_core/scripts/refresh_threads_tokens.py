"""Refresh Threads Long-Lived Tokens for all niches before they expire.

2026-06-14 engagement-loop audit found all 5 niche Threads tokens
expired around 2026-05-21 (60-day LLUTs never refreshed). This produced
a 24-day silent outage: every 10 minutes for 24 days, all 5 niches'
Threads polls returned HTTP 400, generating ~720 warning logs/day with
zero replies posted and zero new pending_engagement rows.

This script (run daily via genlab-threads-token-refresh.timer) calls
the Threads refresh endpoint for each niche's current token. Successful
refreshes are written to /opt/genlab/.threads_tokens.json which
``niche_credentials.resolve_threads_credentials`` reads BEFORE the .env
fallback. So a successful refresh takes effect on the next poller
cycle — no operator action required.

Failures emit a CRITICAL alert (via the same emit_token_expiry_alert
path the poller uses), surfaced on Mission Control's
CriticalAlertsBanner so the operator gets a single visible signal.

Usage:
    python -m genlab_core.scripts.refresh_threads_tokens                # all niches
    python -m genlab_core.scripts.refresh_threads_tokens --dry-run      # no writes
    python -m genlab_core.scripts.refresh_threads_tokens --niche gaming # single
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Niches that have Threads accounts. Keep in sync with the niches that
# actually have THREADS_ACCESS_TOKEN provisioned. Adding a new niche
# here is harmless if the token isn't set — the script logs + skips.
_NICHES = ("ai_creators", "gaming", "sports", "movies", "anime")

_CACHE_PATH = Path("/opt/genlab/.threads_tokens.json")


def _load_cache() -> dict:
    """Read the existing cache file. Returns {} if missing/malformed."""
    try:
        if _CACHE_PATH.is_file():
            return json.loads(_CACHE_PATH.read_text())
    except Exception as exc:
        logger.warning("[refresh-threads] could not read cache: %s", exc)
    return {}


def _write_cache(cache: dict, *, dry_run: bool) -> None:
    """Atomically write the cache file. No-op in dry-run mode.

    fcntl.flock on a sidecar lock file serialises concurrent invocations
    (systemd timer + manual operator invocation) so two writers never
    race on the same .tmp path.
    """
    if dry_run:
        logger.info("[refresh-threads] DRY-RUN: would write %d entries", len(cache))
        return

    import fcntl

    lock_path = _CACHE_PATH.with_suffix(".lock")
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w", encoding="utf-8") as lock_fp:
            fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX)
            tmp_path = _CACHE_PATH.with_suffix(".json.tmp")
            tmp_path.write_text(json.dumps(cache, indent=2, sort_keys=True))
            tmp_path.chmod(0o600)  # contains secrets — operator-readable only
            tmp_path.replace(_CACHE_PATH)
    except Exception as exc:
        logger.error("[refresh-threads] cache write failed: %s", exc)


def _refresh_one(niche_id: str, *, dry_run: bool, force: bool, cache: dict) -> bool:
    """Refresh one niche's Threads token. Returns True on success."""
    from genlab_core.engagement.token_health import (
        emit_token_expiry_alert,
        refresh_threads_token,
    )
    from genlab_core.publishing.niche_credentials import resolve_threads_credentials

    current_token, _user_id = resolve_threads_credentials(niche_id)
    if not current_token:
        logger.info("[refresh-threads] %s: no token configured; skipping", niche_id)
        return False

    # Refresh-skip heuristic: only refresh tokens within 10 days of
    # expiry (or unknown age). Threads requires the token be at least
    # 24h old to be refreshable, and rate-limits frequent refreshes —
    # daily checks against the cached `refreshed_at` keep us comfortably
    # inside the API's expected cadence.
    if not force:
        entry = cache.get(niche_id, {})
        refreshed_at_iso = entry.get("refreshed_at", "")
        if refreshed_at_iso:
            try:
                refreshed_at = datetime.fromisoformat(refreshed_at_iso.replace("Z", "+00:00"))
                age = datetime.now(UTC) - refreshed_at
                # Token was refreshed recently (within 50 days) AND we
                # have its expires_in — don't refresh again.
                if age < timedelta(days=50):
                    logger.info(
                        "[refresh-threads] %s: cached token only %dd old; skipping",
                        niche_id,
                        age.days,
                    )
                    return True
            except (ValueError, TypeError):
                pass

    logger.info("[refresh-threads] %s: calling refresh API...", niche_id)
    result = refresh_threads_token(current_token)
    if not result.ok:
        logger.error("[refresh-threads] %s: refresh FAILED: %s", niche_id, result.error)
        try:
            emit_token_expiry_alert("threads", niche_id, result.error)
        except Exception as exc:
            logger.warning("[refresh-threads] %s: alert emit failed: %s", niche_id, exc)
        return False

    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=result.expires_in)
    cache[niche_id] = {
        "access_token": result.access_token,
        "refreshed_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "expires_in": result.expires_in,
    }
    logger.info(
        "[refresh-threads] %s: refreshed; new expiry %s (%dd from now)",
        niche_id,
        expires_at.isoformat(timespec="seconds"),
        result.expires_in // 86400,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--niche",
        choices=sorted({*_NICHES, "all"}),
        default="all",
        help="Niche to refresh (default: all).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen; don't call the refresh API or write cache.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Refresh even if the cached token is recent (<50d). Use for "
        "emergency post-outage recovery.",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cache = _load_cache()
    niches = list(_NICHES) if args.niche == "all" else [args.niche]
    successes = 0
    failures = 0
    for niche_id in niches:
        if _refresh_one(niche_id, dry_run=args.dry_run, force=args.force, cache=cache):
            successes += 1
        else:
            failures += 1

    if not args.dry_run:
        _write_cache(cache, dry_run=False)

    logger.info(
        "[refresh-threads] done: %d refreshed, %d failed / skipped out of %d",
        successes,
        failures,
        len(niches),
    )
    # Non-zero exit on partial failure so the systemd unit reports
    # "result=exit-code" and the dashboard alert fires.
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
