"""Tests for execution.utils.rate_limiter."""

import time


def test_rate_limiter_basic():
    """Rate limiter enforces minimum delay between requests."""
    from genlab_core.ratelimit.domain_limiter import RateLimiter

    limiter = RateLimiter(default_delay=0.1)

    # First call should not wait
    start = time.monotonic()
    limiter.wait("example.com")
    elapsed = time.monotonic() - start
    assert elapsed < 0.15  # Should be near-instant

    # Second call should wait ~0.1s
    start = time.monotonic()
    limiter.wait("example.com")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # Should have waited


def test_rate_limiter_different_domains():
    """Different domains should not affect each other."""
    from genlab_core.ratelimit.domain_limiter import RateLimiter

    limiter = RateLimiter(default_delay=0.5)
    limiter.wait("a.com")

    # Different domain should be instant
    start = time.monotonic()
    limiter.wait("b.com")
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_rate_limiter_url_extraction():
    """Rate limiter should extract domain from full URLs."""
    from genlab_core.ratelimit.domain_limiter import RateLimiter

    limiter = RateLimiter(default_delay=0.1)
    limiter.wait("https://venturebeat.com/category/ai/feed/")

    # Same domain, different path should still wait
    start = time.monotonic()
    limiter.wait("https://venturebeat.com/article/some-article")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05


def test_rate_limiter_custom_delays():
    """Per-domain delays should override default."""
    from genlab_core.ratelimit.domain_limiter import RateLimiter

    limiter = RateLimiter(default_delay=0.05)
    delay = limiter.get_delay("venturebeat.com")
    assert delay >= 2.0  # VentureBeat has a built-in high delay


def test_get_limiter_singleton():
    """get_limiter should return the same instance."""
    from genlab_core.ratelimit.domain_limiter import get_limiter

    a = get_limiter()
    b = get_limiter()
    assert a is b


def test_concurrent_domains_not_serialized():
    """Two different domains should be able to wait concurrently, not serially.

    If domain A sleeps 0.3s and domain B sleeps 0.3s, with per-domain locks
    the total wall time should be ~0.3s (parallel), not ~0.6s (serial).
    """
    import threading

    from genlab_core.ratelimit.domain_limiter import RateLimiter

    limiter = RateLimiter(default_delay=0.3)

    # Prime both domains so the second call triggers a sleep
    limiter.wait("domain-a.com")
    limiter.wait("domain-b.com")

    results = {}

    def wait_domain(name):
        start = time.monotonic()
        limiter.wait(name)
        results[name] = time.monotonic() - start

    t1 = threading.Thread(target=wait_domain, args=("domain-a.com",))
    t2 = threading.Thread(target=wait_domain, args=("domain-b.com",))

    wall_start = time.monotonic()
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    wall_time = time.monotonic() - wall_start

    # Both threads should have waited ~0.3s each, but concurrently.
    # Total wall time should be well under 2x the delay (< 0.5s).
    # With the old global lock, it would be ~0.6s (serial).
    assert wall_time < 0.5, f"Wall time {wall_time:.3f}s suggests domains were serialized"
