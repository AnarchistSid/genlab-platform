"""Thread-safe, async-compatible token bucket rate limiter.

Implements the classic token bucket algorithm:
  - Bucket refills at `rate` tokens per second
  - Each API call consumes `cost` tokens (default 1)
  - Calls block (sync) or suspend (async) when bucket is empty

Usage:
    pexels_limiter = TokenBucket(rate=200/3600, burst=10)  # 200/hr
    twitch_limiter = TokenBucket(rate=800/60, burst=50)    # 800/min

    # Synchronous
    pexels_limiter.acquire()
    response = requests.get(url)

    # Async
    await twitch_limiter.async_acquire()
    response = await client.get(url)
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import yaml

logger = logging.getLogger(__name__)


class TokenBucket:
    """Thread-safe, async-compatible token bucket rate limiter.

    Args:
        rate: Token refill rate in tokens-per-second.
        burst: Maximum burst capacity (bucket size). Defaults to rate * 2.
    """

    def __init__(self, rate: float, burst: float | None = None) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate}")
        self._rate = rate
        self._burst = burst if burst is not None else rate * 2.0
        self._tokens = self._burst
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time. Must be called with lock held."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def acquire(self, cost: float = 1.0) -> None:
        """Acquire tokens, blocking until available.

        Args:
            cost: Number of tokens to consume (default 1).
        """
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                # Calculate wait time for enough tokens
                deficit = cost - self._tokens
                wait_time = deficit / self._rate

            time.sleep(wait_time)

    async def async_acquire(self, cost: float = 1.0) -> None:
        """Acquire tokens, suspending the coroutine until available.

        Args:
            cost: Number of tokens to consume (default 1).
        """
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= cost:
                    self._tokens -= cost
                    return
                deficit = cost - self._tokens
                wait_time = deficit / self._rate

            await asyncio.sleep(wait_time)

    def try_acquire(self, cost: float = 1.0) -> bool:
        """Try to acquire tokens without blocking.

        Returns:
            True if tokens were acquired, False if insufficient tokens.
        """
        with self._lock:
            self._refill()
            if self._tokens >= cost:
                self._tokens -= cost
                return True
            return False

    @property
    def tokens(self) -> float:
        """Current token count (after refill)."""
        with self._lock:
            self._refill()
            return self._tokens

    @property
    def rate(self) -> float:
        """Refill rate in tokens/second."""
        return self._rate


class RateLimiterRegistry:
    """Registry of named rate limiters, loadable from YAML config.

    Allows pipeline stages to share rate limiter instances by name:

        registry = RateLimiterRegistry.from_yaml("config/rate_limits.yaml")
        limiter = registry.get("twitch_helix")
        limiter.acquire()

    YAML format:
        twitch_helix:
            rate: 800
            unit: minute
            burst: 50
        pexels:
            rate: 200
            unit: hour
            burst: 10
    """

    _UNIT_DIVISORS = {
        "second": 1.0,
        "minute": 60.0,
        "hour": 3600.0,
        "day": 86400.0,
    }

    def __init__(self) -> None:
        self._limiters: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_yaml(cls, path: str) -> RateLimiterRegistry:
        """Load rate limiter configs from a YAML file.

        Args:
            path: Path to YAML config file.

        Returns:
            Registry with all configured limiters.
        """
        registry = cls()
        try:
            with open(path) as f:
                config = yaml.safe_load(f) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("Failed to load rate limits from %s: %s", path, exc)
            return registry

        for name, cfg in config.items():
            if not isinstance(cfg, dict):
                continue
            raw_rate = cfg.get("rate", 1.0)
            unit = cfg.get("unit", "second")
            divisor = cls._UNIT_DIVISORS.get(unit, 1.0)
            rate_per_second = raw_rate / divisor
            burst = cfg.get("burst", None)
            registry.register(name, TokenBucket(rate=rate_per_second, burst=burst))

        logger.debug("Loaded %d rate limiters from %s", len(registry._limiters), path)
        return registry

    def get(self, name: str) -> TokenBucket:
        """Get a rate limiter by name.

        Args:
            name: The limiter name.

        Returns:
            The TokenBucket instance.

        Raises:
            KeyError: If no limiter is registered with that name.
        """
        with self._lock:
            if name not in self._limiters:
                raise KeyError(f"No rate limiter registered as '{name}'")
            return self._limiters[name]

    def register(self, name: str, limiter: TokenBucket) -> None:
        """Register a rate limiter under a name.

        Args:
            name: The name to register under.
            limiter: The TokenBucket instance.
        """
        with self._lock:
            self._limiters[name] = limiter
