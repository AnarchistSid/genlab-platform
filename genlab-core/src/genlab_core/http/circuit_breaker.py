"""Shared circuit breaker + resilient decorator for external API calls.

Generalised from the TTS-specific CircuitBreaker in ``tts/cascade.py``.

States:
    CLOSED    — normal operation, requests flow through.
    OPEN      — too many recent failures; requests are rejected immediately
                with ``CircuitOpenError``.
    HALF_OPEN — after ``recovery_timeout`` elapses the circuit allows ONE
                probe request through.  Success → CLOSED, failure → OPEN.

Failure counting uses a sliding window: only failures within the last
``window_seconds`` are counted.  This prevents a slow trickle of errors
over hours from tripping the breaker.

Pre-configured per-service instances are exported at module level so
callers can import them directly::

    from genlab_core.http.circuit_breaker import SHAREPOINT_CB
    SHAREPOINT_CB.call(do_graph_request, *args)

The ``@resilient`` decorator combines retry-with-backoff and a circuit
breaker in one annotation::

    from genlab_core.http.circuit_breaker import resilient, YOUTUBE_CB

    @resilient(YOUTUBE_CB, max_attempts=3, backoff=2.0)
    def fetch_trending(api_key):
        ...
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


# ── Exceptions ───────────────────────────────────────────────────────


class CircuitOpenError(Exception):
    """Raised when a call is attempted on an open circuit breaker."""

    def __init__(self, name: str, retry_after: float = 0.0):
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit breaker '{name}' is OPEN — retry after {retry_after:.1f}s")


# ── CircuitBreaker ───────────────────────────────────────────────────


class CircuitBreaker:
    """Thread-safe circuit breaker with sliding-window failure detection.

    Args:
        name:              Human-readable service label (for logging).
        failure_threshold:  Number of failures inside ``window_seconds``
                           before the circuit trips open.
        window_seconds:    Sliding window for failure counting.
        recovery_timeout:  Seconds to wait in OPEN before probing HALF_OPEN.

    Typical usage::

        cb = CircuitBreaker("sharepoint", failure_threshold=5)
        result = cb.call(requests.get, url, timeout=15)
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        window_seconds: float = 60.0,
        recovery_timeout: float = 30.0,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.recovery_timeout = recovery_timeout

        self._lock = threading.Lock()
        self._state: str = self.CLOSED
        self._failure_timestamps: list[float] = []
        self._last_failure_time: float = 0.0
        self._success_count: int = 0
        self._total_calls: int = 0
        self._total_failures: int = 0

    # ── Public interface ─────────────────────────────────────────────

    @property
    def state(self) -> str:
        """Current state — may transition OPEN → HALF_OPEN if timeout elapsed."""
        with self._lock:
            return self._effective_state()

    def _effective_state(self) -> str:
        """Compute effective state (caller holds the lock)."""
        if self._state == self.OPEN:
            elapsed = time.monotonic() - self._last_failure_time
            if elapsed >= self.recovery_timeout:
                self._state = self.HALF_OPEN
                logger.info(
                    "Circuit '%s' → HALF_OPEN (%.1fs since last failure)",
                    self.name,
                    elapsed,
                )
        return self._state

    def call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute *fn* through the circuit breaker.

        Raises ``CircuitOpenError`` if the circuit is OPEN.
        """
        with self._lock:
            state = self._effective_state()
            if state == self.OPEN:
                retry_after = self.recovery_timeout - (time.monotonic() - self._last_failure_time)
                raise CircuitOpenError(self.name, max(0.0, retry_after))
            self._total_calls += 1

        # Allow the call through (CLOSED or HALF_OPEN probe)
        try:
            result = fn(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise

    def record_success(self) -> None:
        """Record a successful call — resets failure window, closes circuit."""
        with self._lock:
            self._success_count += 1
            self._failure_timestamps.clear()
            if self._state == self.HALF_OPEN:
                logger.info("Circuit '%s' → CLOSED (probe succeeded)", self.name)
            self._state = self.CLOSED

    def record_failure(self) -> None:
        """Record a failed call — may trip the circuit open."""
        now = time.monotonic()
        with self._lock:
            self._total_failures += 1
            self._last_failure_time = now
            self._failure_timestamps.append(now)

            # Prune failures outside the sliding window
            cutoff = now - self.window_seconds
            self._failure_timestamps = [t for t in self._failure_timestamps if t > cutoff]

            if len(self._failure_timestamps) >= self.failure_threshold:
                if self._state != self.OPEN:
                    logger.warning(
                        "Circuit '%s' → OPEN (%d failures in %.0fs window)",
                        self.name,
                        len(self._failure_timestamps),
                        self.window_seconds,
                    )
                self._state = self.OPEN
            elif self._state == self.HALF_OPEN:
                logger.info(
                    "Circuit '%s' → OPEN (half-open probe failed)",
                    self.name,
                )
                self._state = self.OPEN

    def reset(self) -> None:
        """Manually reset to CLOSED — use for testing or admin overrides."""
        with self._lock:
            self._failure_timestamps.clear()
            self._state = self.CLOSED
            logger.info("Circuit '%s' manually reset → CLOSED", self.name)

    @property
    def stats(self) -> dict[str, Any]:
        """Diagnostic snapshot."""
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            recent_failures = len([t for t in self._failure_timestamps if t > cutoff])
            return {
                "name": self.name,
                "state": self._effective_state(),
                "recent_failures": recent_failures,
                "failure_threshold": self.failure_threshold,
                "total_calls": self._total_calls,
                "total_failures": self._total_failures,
                "success_count": self._success_count,
            }

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self.state!r}, "
            f"threshold={self.failure_threshold})"
        )


# ── @resilient decorator ─────────────────────────────────────────────


def resilient(
    circuit_breaker: CircuitBreaker,
    max_attempts: int = 3,
    backoff: float = 2.0,
    initial_delay: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
):
    """Decorator combining retry-with-backoff and circuit breaker.

    On each attempt the call goes through ``circuit_breaker``.  If the
    circuit is open, ``CircuitOpenError`` is raised immediately (no retry).
    Transient failures (matching *exceptions*) are retried up to
    *max_attempts* with exponential backoff.

    Args:
        circuit_breaker: The ``CircuitBreaker`` instance to use.
        max_attempts:    Total attempts including the first try.
        backoff:         Multiplier applied to delay after each failure.
        initial_delay:   Seconds before the first retry.
        exceptions:      Exception types eligible for retry.
    """

    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = initial_delay
            last_exc: BaseException | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return circuit_breaker.call(fn, *args, **kwargs)
                except CircuitOpenError:
                    # Don't retry when the circuit is open — fail fast
                    raise
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    logger.warning(
                        "%s attempt %d/%d failed (%s): %s — retrying in %.1fs",
                        fn.__qualname__,
                        attempt,
                        max_attempts,
                        circuit_breaker.name,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= backoff

            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


# ── Pre-configured per-service instances ─────────────────────────────

SHAREPOINT_CB = CircuitBreaker(
    "sharepoint",
    failure_threshold=5,
    window_seconds=120.0,
    recovery_timeout=60.0,
)

META_API_CB = CircuitBreaker(
    "meta_api",
    failure_threshold=3,
    window_seconds=60.0,
    recovery_timeout=30.0,
)

YOUTUBE_CB = CircuitBreaker(
    "youtube_api",
    failure_threshold=3,
    window_seconds=60.0,
    recovery_timeout=30.0,
)

ANTHROPIC_CB = CircuitBreaker(
    "anthropic",
    failure_threshold=2,
    window_seconds=60.0,
    recovery_timeout=60.0,
)

TWITTER_CB = CircuitBreaker(
    "twitter_api",
    failure_threshold=3,
    window_seconds=60.0,
    recovery_timeout=30.0,
)

# Convenience lookup for platform-keyed dispatch
PLATFORM_CIRCUIT_BREAKERS: dict[str, CircuitBreaker] = {
    "instagram": META_API_CB,
    "facebook": META_API_CB,
    "youtube": YOUTUBE_CB,
    "x": TWITTER_CB,
    "twitter": TWITTER_CB,
}


def get_circuit_breaker(platform: str) -> CircuitBreaker | None:
    """Return the circuit breaker for a platform, or None if unknown."""
    return PLATFORM_CIRCUIT_BREAKERS.get(platform)
