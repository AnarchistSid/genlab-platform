"""Media provider health tracking + auto-rotation.

Motivating incident (2026-07-19, memo notes it fixed): tmpfiles.org
silently changed its URL scheme — the naive replace() produced a
wrapper page URL instead of the signed direct-download URL. Meta
preflight rejected the URL as text/html and 3 IG publishes failed.

This module provides a lightweight health tracker. When a provider
fails, it's marked unhealthy for a cool-down period. Subsequent
upload attempts skip the provider until the cool-down expires. On
the next attempt after cool-down, the provider is tried again — a
transient outage auto-recovers.

Discipline
==========

* **Module-level state.** In-memory only, per-process. Suits the
  Dramatiq worker + short-lived publisher CLI use cases equally.
  Losing state on restart is fine — worst case, one wasted attempt
  after startup before the failure re-marks the provider unhealthy.
* **Cool-down period.** Default 1 hour — long enough to bridge
  transient DNS / rate-limit / 5xx blips, short enough to auto-
  recover in the same publisher window.
* **Fail-open.** ``is_provider_healthy`` returns True unless the
  provider is explicitly marked unhealthy. Absence of evidence
  never blocks an upload.
* **No thread-safety guarantees.** ``dict`` writes are atomic in
  CPython; two concurrent writers race on last-writer-wins semantics
  which is fine — the health signal is coarse.

See:
* ``[[class-of-bug-third-party-silent-url-scheme-change]]`` — the
  motivating class of failure
* ``platforms/cdn_upload.py`` — the consumer
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Default cool-down duration in seconds. 1 hour matches the typical
# time between a publisher retry window and the next publish cycle.
DEFAULT_COOLDOWN_S: float = 3600.0

# provider_id → epoch_seconds_when_healthy_again
_unhealthy_until: dict[str, float] = {}

# provider_id → (last_failure_reason, unhealthy_since_epoch) for
# observability. Not queried by the health logic itself.
_last_failure: dict[str, tuple[str, float]] = {}


@dataclass(frozen=True)
class ProviderHealth:
    """Snapshot of a provider's health state.

    Fields
    ------
    provider : str
    is_healthy : bool
    cool_down_remaining_s : float
        Seconds until the provider becomes healthy again. 0.0 when
        already healthy.
    last_failure_reason : str
        Empty when no known failure has been recorded.
    """

    provider: str
    is_healthy: bool
    cool_down_remaining_s: float
    last_failure_reason: str


def is_provider_healthy(provider: str) -> bool:
    """Return True if the provider is not in a cool-down window.

    Absence of evidence = healthy. This is the fail-open discipline —
    a provider that has never been tested is assumed available.
    """
    if not provider:
        return True
    cool_down_end = _unhealthy_until.get(provider)
    if cool_down_end is None:
        return True
    if time.time() >= cool_down_end:
        # Cool-down expired — drop the record so subsequent checks
        # take the fast path.
        _unhealthy_until.pop(provider, None)
        _last_failure.pop(provider, None)
        return True
    return False


def mark_provider_unhealthy(
    provider: str,
    reason: str,
    *,
    cooldown_s: float = DEFAULT_COOLDOWN_S,
) -> None:
    """Mark a provider unhealthy for ``cooldown_s`` seconds.

    Callers pass ``reason`` for observability — the string surfaces
    in the health snapshot but doesn't affect the cool-down logic.
    """
    if not provider:
        return
    now = time.time()
    _unhealthy_until[provider] = now + cooldown_s
    _last_failure[provider] = (str(reason)[:200], now)
    logger.info(
        "[media_provider_health] marked %s unhealthy for %.0fs (reason=%s)",
        provider,
        cooldown_s,
        reason,
    )


def clear_provider(provider: str) -> None:
    """Manually mark a provider healthy again. Used by tests + the
    rare operator override case where a cool-down needs early exit."""
    _unhealthy_until.pop(provider, None)
    _last_failure.pop(provider, None)


def snapshot(provider: str) -> ProviderHealth:
    """Return a full health snapshot for the provider. Useful for
    dashboard cards + drain scripts that want to report which
    providers are cool-down-blocked."""
    if not provider:
        return ProviderHealth(
            provider="",
            is_healthy=True,
            cool_down_remaining_s=0.0,
            last_failure_reason="",
        )
    cool_down_end = _unhealthy_until.get(provider)
    now = time.time()
    if cool_down_end is None or now >= cool_down_end:
        return ProviderHealth(
            provider=provider,
            is_healthy=True,
            cool_down_remaining_s=0.0,
            last_failure_reason="",
        )
    reason, _ = _last_failure.get(provider, ("", 0.0))
    return ProviderHealth(
        provider=provider,
        is_healthy=False,
        cool_down_remaining_s=cool_down_end - now,
        last_failure_reason=reason,
    )


def reset_all() -> None:
    """Wipe all state. Tests only."""
    _unhealthy_until.clear()
    _last_failure.clear()


__all__ = [
    "DEFAULT_COOLDOWN_S",
    "ProviderHealth",
    "clear_provider",
    "is_provider_healthy",
    "mark_provider_unhealthy",
    "reset_all",
    "snapshot",
]
