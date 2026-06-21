"""System-level health aggregator across all circuit breakers (Lever P).

Pre-2026-06-21 each of the 5 pre-configured circuit breakers
(SHAREPOINT_CB, META_API_CB, YOUTUBE_CB, ANTHROPIC_CB, TWITTER_CB)
tracked its own state independently. When a cascade failure hit
multiple platforms simultaneously (e.g. Hetzner network blip,
prod outage, DNS misconfig), operators got 3+ separate WARN logs
about "circuit X opened" but no single signal saying "the system
is degraded across multiple platforms — investigate the common cause".

Lever P closes that gap. The aggregator:

1. Inventories all 5 named circuit breakers + queries each state
2. Counts how many are OPEN (or HALF_OPEN) right now
3. Emits a structured ``SystemHealth`` snapshot operators can read
4. Marks ``is_degraded=True`` when ≥3 breakers are non-closed

This is a read-only diagnostic module. It does NOT change circuit
breaker behavior — operators (or downstream automation like
adaptive cost routing) consume the snapshot via:

    from genlab_core.monitoring.system_health import get_system_health
    health = get_system_health()
    if health.is_degraded:
        # take adaptive action — defer non-urgent fetches, reduce
        # LLM judge spend, surface alert to dashboard, etc.
        ...

Run via:
    python -m genlab_core.monitoring.system_health

Designed to be cheap (no I/O — just reads in-process breaker state)
so callers can poll it on a tight loop without cost concerns.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

logger = logging.getLogger(__name__)


# All pre-configured circuit breakers exposed by ``http/circuit_breaker.py``.
# Keeping the list explicit (vs. dynamic import-time discovery) so adding
# a new platform breaker is a 1-line change here PLUS the new constant.
# That visibility is the point — aggregator coverage is documented.
_TRACKED_BREAKERS: Final[list[str]] = [
    "sharepoint",
    "meta_api",
    "youtube",
    "anthropic",
    "twitter",
]

# Degradation threshold — when ≥3 of 5 breakers are non-CLOSED,
# the system as a whole is "degraded" and operators should investigate
# the common cause. Tunable per deployment via the kwarg on
# ``get_system_health``.
_DEFAULT_DEGRADED_THRESHOLD: Final[int] = 3


@dataclass(frozen=True)
class CircuitState:
    """Snapshot of one circuit breaker's state at a point in time."""

    name: str
    state: str  # "closed" | "open" | "half_open"
    total_calls: int
    total_failures: int

    @property
    def is_open(self) -> bool:
        """True when the circuit is OPEN or HALF_OPEN (in-recovery)."""
        return self.state in ("open", "half_open")


@dataclass(frozen=True)
class SystemHealth:
    """Aggregate health snapshot across all tracked circuit breakers."""

    breakers: list[CircuitState]
    open_count: int  # number of breakers OPEN or HALF_OPEN
    degraded_threshold: int
    is_degraded: bool  # open_count >= degraded_threshold
    computed_at: str  # ISO timestamp

    @property
    def open_breakers(self) -> list[str]:
        """Names of breakers that are OPEN or HALF_OPEN right now."""
        return [b.name for b in self.breakers if b.is_open]

    @property
    def closed_count(self) -> int:
        """Number of breakers in CLOSED state (healthy)."""
        return len(self.breakers) - self.open_count


def _load_breaker(name: str):
    """Import + return the named circuit breaker, or None on failure.

    Each breaker is a module-level constant in ``http/circuit_breaker.py``,
    e.g. ``SHAREPOINT_CB``. The convention is ``<NAME_UPPER>_CB``.
    """
    try:
        from genlab_core.http import circuit_breaker as cb_module
    except ImportError:
        return None
    return getattr(cb_module, f"{name.upper()}_CB", None)


def snapshot_breaker(name: str) -> CircuitState | None:
    """Capture the current state of one named circuit breaker.

    Returns None when the breaker can't be loaded — preserves the
    fail-quiet contract so a single misnamed breaker doesn't crash
    the whole aggregator.
    """
    breaker = _load_breaker(name)
    if breaker is None:
        return None
    try:
        return CircuitState(
            name=breaker.name,
            state=breaker.state,
            total_calls=getattr(breaker, "_total_calls", 0),
            total_failures=getattr(breaker, "_total_failures", 0),
        )
    except Exception as exc:
        # Defensive — ``state`` acquires the breaker's internal lock,
        # so a hung breaker shouldn't block the aggregator's caller.
        logger.debug("[system_health] snapshot failed for %s: %s", name, exc)
        return None


def get_system_health(
    *,
    degraded_threshold: int = _DEFAULT_DEGRADED_THRESHOLD,
) -> SystemHealth:
    """Return the current aggregate health across all tracked breakers.

    ``degraded_threshold`` is the number of non-CLOSED breakers required
    to mark the system as degraded. Default 3 of 5 = 60% of platforms
    are misbehaving simultaneously.

    Cheap to call — no I/O, just in-process state reads. Safe to poll
    on any cadence the caller wants (every-second monitor loops OK).
    """
    snapshots: list[CircuitState] = []
    for name in _TRACKED_BREAKERS:
        snap = snapshot_breaker(name)
        if snap is not None:
            snapshots.append(snap)

    open_count = sum(1 for s in snapshots if s.is_open)
    return SystemHealth(
        breakers=snapshots,
        open_count=open_count,
        degraded_threshold=degraded_threshold,
        is_degraded=open_count >= degraded_threshold,
        computed_at=datetime.now(UTC).isoformat(),
    )


if __name__ == "__main__":
    # CLI entry: ``python -m genlab_core.monitoring.system_health``
    import argparse

    parser = argparse.ArgumentParser(description="System health aggregator (Lever P)")
    parser.add_argument(
        "--degraded-threshold",
        type=int,
        default=_DEFAULT_DEGRADED_THRESHOLD,
        help=f"Number of non-CLOSED breakers required to mark degraded (default {_DEFAULT_DEGRADED_THRESHOLD})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    health = get_system_health(degraded_threshold=args.degraded_threshold)
    print(f"\nSystem health snapshot at {health.computed_at}")
    print(f"  Tracked breakers: {len(health.breakers)}")
    print(f"  Open/half-open:   {health.open_count}")
    print(f"  Closed (healthy): {health.closed_count}")
    print(f"  Threshold:        {health.degraded_threshold}")
    print(f"  IS DEGRADED:      {health.is_degraded}")
    if health.open_breakers:
        print(f"\nNon-CLOSED breakers: {', '.join(health.open_breakers)}")
    print("\nPer-breaker:")
    for s in health.breakers:
        marker = "✗" if s.is_open else "✓"
        print(
            f"  {marker} {s.name:<12} {s.state:<10} "
            f"({s.total_calls} calls, {s.total_failures} failures)"
        )
