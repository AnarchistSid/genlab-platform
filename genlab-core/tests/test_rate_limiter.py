"""Tests for genlab_core.ratelimit — TokenBucket and RateLimiterRegistry."""

import asyncio
import threading
import time

import pytest

from genlab_core.ratelimit.token_bucket import TokenBucket, RateLimiterRegistry


class TestTokenBucketBurst:
    def test_burst_capacity(self):
        """Can acquire up to burst tokens without blocking."""
        bucket = TokenBucket(rate=10.0, burst=5.0)
        for _ in range(5):
            assert bucket.try_acquire()
        # Burst exhausted — should fail
        assert not bucket.try_acquire()

    def test_default_burst_is_2x_rate(self):
        bucket = TokenBucket(rate=10.0)
        assert bucket._burst == 20.0

    def test_rejects_zero_rate(self):
        with pytest.raises(ValueError):
            TokenBucket(rate=0)

    def test_rejects_negative_rate(self):
        with pytest.raises(ValueError):
            TokenBucket(rate=-1)


class TestTokenBucketRateLimiting:
    def test_refill_after_wait(self):
        """After burst exhausted, waiting allows new tokens."""
        bucket = TokenBucket(rate=100.0, burst=1.0)
        assert bucket.try_acquire()
        assert not bucket.try_acquire()
        time.sleep(0.02)  # ~2 tokens at 100/s
        assert bucket.try_acquire()

    def test_acquire_blocks_until_available(self):
        bucket = TokenBucket(rate=20.0, burst=1.0)
        bucket.acquire()  # Use the 1 burst token
        start = time.monotonic()
        bucket.acquire()  # Must wait ~0.05s for 1 token at 20/s
        elapsed = time.monotonic() - start
        assert elapsed >= 0.03  # Some tolerance

    def test_try_acquire_never_blocks(self):
        bucket = TokenBucket(rate=0.1, burst=1.0)
        bucket.try_acquire()  # Drain
        start = time.monotonic()
        result = bucket.try_acquire()
        elapsed = time.monotonic() - start
        assert result is False
        assert elapsed < 0.01  # Should be instant


class TestTokenBucketAsync:
    def test_async_acquire(self):
        """async_acquire works correctly in an event loop."""
        bucket = TokenBucket(rate=100.0, burst=2.0)

        async def drain():
            await bucket.async_acquire()
            await bucket.async_acquire()
            # Third should wait for refill
            start = time.monotonic()
            await bucket.async_acquire()
            return time.monotonic() - start

        elapsed = asyncio.run(drain())
        assert elapsed >= 0.005  # Waited for at least 1 token


class TestTokenBucketThreadSafety:
    def test_concurrent_threads(self):
        """10 concurrent threads acquiring from same limiter without crashes."""
        bucket = TokenBucket(rate=1000.0, burst=100.0)
        errors = []

        def worker():
            try:
                for _ in range(10):
                    bucket.acquire()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(errors) == 0

    def test_tokens_property_thread_safe(self):
        bucket = TokenBucket(rate=10.0, burst=10.0)
        # Should not raise even when called from multiple threads
        results = []

        def read_tokens():
            results.append(bucket.tokens)

        threads = [threading.Thread(target=read_tokens) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(results) == 20


class TestRateLimiterRegistry:
    def test_register_and_get(self):
        registry = RateLimiterRegistry()
        limiter = TokenBucket(rate=10.0, burst=5.0)
        registry.register("test_api", limiter)
        assert registry.get("test_api") is limiter

    def test_get_missing_raises(self):
        registry = RateLimiterRegistry()
        with pytest.raises(KeyError, match="no_such_limiter"):
            registry.get("no_such_limiter")

    def test_from_yaml(self, tmp_path):
        config = tmp_path / "rate_limits.yaml"
        config.write_text(
            "twitch_helix:\n"
            "  rate: 800\n"
            "  unit: minute\n"
            "  burst: 50\n"
            "pexels:\n"
            "  rate: 200\n"
            "  unit: hour\n"
            "  burst: 10\n"
        )
        registry = RateLimiterRegistry.from_yaml(str(config))
        twitch = registry.get("twitch_helix")
        assert abs(twitch.rate - 800 / 60) < 0.01
        pexels = registry.get("pexels")
        assert abs(pexels.rate - 200 / 3600) < 0.001

    def test_from_yaml_missing_file(self, tmp_path):
        """Missing config file returns empty registry without crashing."""
        registry = RateLimiterRegistry.from_yaml(str(tmp_path / "nonexistent.yaml"))
        with pytest.raises(KeyError):
            registry.get("anything")

    def test_from_yaml_default_unit(self, tmp_path):
        """If unit is omitted, default to per-second."""
        config = tmp_path / "rate_limits.yaml"
        config.write_text("fast_api:\n  rate: 100\n")
        registry = RateLimiterRegistry.from_yaml(str(config))
        fast = registry.get("fast_api")
        assert fast.rate == 100.0


# --- Domain limiter (migrated from Content Scraper) -------------------------

from genlab_core.ratelimit.domain_limiter import RateLimiter


class TestDomainLimiterBasic:
    def test_first_call_no_wait(self):
        limiter = RateLimiter(default_delay=0.1)
        start = time.monotonic()
        limiter.wait("example.com")
        elapsed = time.monotonic() - start
        assert elapsed < 0.15

    def test_second_call_waits(self):
        limiter = RateLimiter(default_delay=0.1)
        limiter.wait("example.com")
        start = time.monotonic()
        limiter.wait("example.com")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05

    def test_different_domains_independent(self):
        limiter = RateLimiter(default_delay=0.5)
        limiter.wait("a.com")
        start = time.monotonic()
        limiter.wait("b.com")
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    def test_url_extraction(self):
        limiter = RateLimiter(default_delay=0.1)
        limiter.wait("https://venturebeat.com/category/ai/feed/")
        start = time.monotonic()
        limiter.wait("https://venturebeat.com/article/some-article")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.05

    def test_custom_delays(self):
        limiter = RateLimiter(default_delay=0.05)
        delay = limiter.get_delay("venturebeat.com")
        assert delay >= 2.0


class TestDomainLimiterConcurrency:
    def test_concurrent_domains_not_serialized(self):
        """Different domains should wait concurrently, not serially."""
        limiter = RateLimiter(default_delay=0.3)
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

        assert wall_time < 0.5, f"Wall time {wall_time:.3f}s suggests domains were serialized"
