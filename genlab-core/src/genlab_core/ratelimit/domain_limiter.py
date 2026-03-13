"""Per-domain rate limiter for HTTP requests.

Prevents 429 (Too Many Requests) errors by enforcing minimum delays between
requests to the same domain. Thread-safe via per-domain locks.

Usage:
    from genlab_core.ratelimit.domain_limiter import RateLimiter, get_limiter

    limiter = get_limiter()
    limiter.wait("venturebeat.com")
    response = requests.get(url)

Config-driven via YAML (optional). Falls back to sensible defaults.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

# Default minimum seconds between requests to the same domain
DEFAULT_DELAY_SECONDS = 1.0

# Max domains to track (prevents unbounded memory growth in long-running daemons)
MAX_TRACKED_DOMAINS = 200

# Built-in overrides for known rate-limited domains
DEFAULT_DOMAIN_DELAYS = {
    "venturebeat.com": 3.0,
    "www.venturebeat.com": 3.0,
    "techcrunch.com": 1.5,
    "www.techcrunch.com": 1.5,
    "arstechnica.com": 1.5,
    "www.arstechnica.com": 1.5,
    "theverge.com": 1.5,
    "www.theverge.com": 1.5,
    "wired.com": 1.5,
    "www.wired.com": 1.5,
    "marktechpost.com": 2.0,
    "www.marktechpost.com": 2.0,
    "the-decoder.com": 2.0,
    "news.ycombinator.com": 1.0,
    "blog.google": 1.0,
}


def _load_config_delays(config_path: Optional[Path] = None) -> Dict[str, float]:
    """Load per-domain delays from config file, if it exists."""
    if config_path is None or not config_path.exists():
        return {}
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("domain_delays", {})
    except Exception as exc:
        logger.warning("Failed to load rate limits config: %s", exc)
        return {}


class RateLimiter:
    """Per-domain rate limiter with configurable delays.

    Args:
        default_delay: Default seconds between requests to the same domain.
        config_path: Optional path to YAML config with domain_delays overrides.
    """

    def __init__(
        self,
        default_delay: float = DEFAULT_DELAY_SECONDS,
        config_path: Optional[Path] = None,
    ):
        self._lock = threading.Lock()
        self._last_request: Dict[str, float] = {}
        self._default_delay = default_delay
        self._domain_locks: Dict[str, threading.Lock] = {}
        self._domain_locks_lock = threading.Lock()

        self._domain_delays = dict(DEFAULT_DOMAIN_DELAYS)
        config_delays = _load_config_delays(config_path)
        self._domain_delays.update(config_delays)

        if config_delays:
            logger.debug("Loaded %d domain delay overrides from config", len(config_delays))

    def _get_domain(self, url_or_domain: str) -> str:
        """Extract domain from a URL or pass through if already a domain."""
        if "://" in url_or_domain:
            return urlparse(url_or_domain).netloc.lower()
        return url_or_domain.lower()

    def get_delay(self, url_or_domain: str) -> float:
        """Get the configured delay for a domain."""
        domain = self._get_domain(url_or_domain)
        return self._domain_delays.get(domain, self._default_delay)

    def _get_domain_lock(self, domain: str) -> threading.Lock:
        """Return a per-domain lock, creating one if needed (thread-safe)."""
        with self._domain_locks_lock:
            if domain not in self._domain_locks:
                self._domain_locks[domain] = threading.Lock()
            return self._domain_locks[domain]

    def _evict_stale_domains(self) -> None:
        """Evict oldest 25% of tracked domains when limit exceeded.

        Must be called while self._lock is held.
        """
        if len(self._last_request) <= MAX_TRACKED_DOMAINS:
            return
        evict_count = len(self._last_request) // 4
        sorted_domains = sorted(self._last_request.items(), key=lambda x: x[1])
        for domain, _ in sorted_domains[:evict_count]:
            del self._last_request[domain]
        logger.debug("Rate limiter: evicted %d stale domains", evict_count)

    def wait(self, url_or_domain: str) -> float:
        """Wait if needed before making a request to this domain.

        Returns the actual wait time in seconds (0 if no wait needed).

        Uses per-domain locks so sleeping for domain A does not block domain B.
        """
        domain = self._get_domain(url_or_domain)
        delay = self.get_delay(domain)
        domain_lock = self._get_domain_lock(domain)

        with domain_lock:
            with self._lock:
                self._evict_stale_domains()
            now = time.monotonic()
            with self._lock:
                last = self._last_request.get(domain, 0.0)
            elapsed = now - last
            wait_time = max(0.0, delay - elapsed)

            if wait_time > 0:
                logger.debug("Rate limiting %s: waiting %.1fs", domain, wait_time)
                time.sleep(wait_time)

            with self._lock:
                self._last_request[domain] = time.monotonic()
        return wait_time

    def record(self, url_or_domain: str) -> None:
        """Record that a request was just made (call after successful request)."""
        domain = self._get_domain(url_or_domain)
        with self._lock:
            self._last_request[domain] = time.monotonic()


# Module-level singleton
_global_limiter: Optional[RateLimiter] = None
_global_limiter_lock = threading.Lock()


def get_limiter(config_path: Optional[Path] = None) -> RateLimiter:
    """Get or create the global rate limiter singleton (thread-safe).

    Args:
        config_path: Optional path to YAML config (only used on first call).
    """
    global _global_limiter
    if _global_limiter is None:
        with _global_limiter_lock:
            if _global_limiter is None:
                _global_limiter = RateLimiter(config_path=config_path)
    return _global_limiter
