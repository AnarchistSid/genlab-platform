"""Tests for genlab_core.http.circuit_breaker.

Covers:
  - CircuitBreaker state transitions (CLOSED → OPEN → HALF_OPEN → CLOSED)
  - Sliding window failure counting
  - Recovery timeout behaviour
  - Thread safety (basic)
  - @resilient decorator: retry + circuit breaker integration
  - Pre-configured instances and get_circuit_breaker lookup
  - CircuitOpenError attributes
  - Stats / diagnostics
"""
from __future__ import annotations

import threading
import time

import pytest
from genlab_core.http.circuit_breaker import (
    ANTHROPIC_CB,
    META_API_CB,
    PLATFORM_CIRCUIT_BREAKERS,
    SHAREPOINT_CB,
    TWITTER_CB,
    YOUTUBE_CB,
    CircuitBreaker,
    CircuitOpenError,
    get_circuit_breaker,
    resilient,
)

# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def cb():
    """Fresh circuit breaker with low thresholds for fast testing."""
    return CircuitBreaker(
        name="test_service",
        failure_threshold=3,
        window_seconds=10.0,
        recovery_timeout=0.2,
    )


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset module-level circuit breakers between tests."""
    for instance in [SHAREPOINT_CB, META_API_CB, YOUTUBE_CB, ANTHROPIC_CB, TWITTER_CB]:
        instance.reset()
    yield


# ── CircuitBreaker: state transitions ────────────────────────────────

class TestCircuitBreakerStates:
    """State machine transitions."""

    def test_starts_closed(self, cb: CircuitBreaker):
        assert cb.state == CircuitBreaker.CLOSED

    def test_stays_closed_below_threshold(self, cb: CircuitBreaker):
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED

    def test_opens_at_threshold(self, cb: CircuitBreaker):
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

    def test_open_to_half_open_after_timeout(self, cb: CircuitBreaker):
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        time.sleep(0.25)  # recovery_timeout = 0.2
        assert cb.state == CircuitBreaker.HALF_OPEN

    def test_half_open_to_closed_on_success(self, cb: CircuitBreaker):
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.25)
        assert cb.state == CircuitBreaker.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitBreaker.CLOSED

    def test_half_open_to_open_on_failure(self, cb: CircuitBreaker):
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.25)
        assert cb.state == CircuitBreaker.HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

    def test_success_resets_failure_window(self, cb: CircuitBreaker):
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        # Only 1 failure since last success — should stay closed
        assert cb.state == CircuitBreaker.CLOSED


# ── CircuitBreaker: sliding window ───────────────────────────────────

class TestSlidingWindow:
    """Failures outside the window do not count."""

    def test_old_failures_pruned(self):
        cb = CircuitBreaker(
            "short_window",
            failure_threshold=3,
            window_seconds=0.1,
            recovery_timeout=0.05,
        )
        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)  # failures expire
        cb.record_failure()
        # Only 1 failure inside window — should stay closed
        assert cb.state == CircuitBreaker.CLOSED

    def test_all_failures_in_window_trips(self):
        cb = CircuitBreaker(
            "burst",
            failure_threshold=3,
            window_seconds=5.0,
            recovery_timeout=0.1,
        )
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN


# ── CircuitBreaker.call() ────────────────────────────────────────────

class TestCircuitBreakerCall:
    """Calling functions through the circuit breaker."""

    def test_call_success(self, cb: CircuitBreaker):
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.state == CircuitBreaker.CLOSED

    def test_call_exception_records_failure(self, cb: CircuitBreaker):
        with pytest.raises(ValueError, match="boom"):
            cb.call(lambda: (_ for _ in ()).throw(ValueError("boom")))
        assert cb.stats["total_failures"] == 1

    def test_call_raises_circuit_open_error(self, cb: CircuitBreaker):
        for _ in range(3):
            cb.record_failure()
        with pytest.raises(CircuitOpenError) as exc_info:
            cb.call(lambda: None)
        assert exc_info.value.name == "test_service"
        assert exc_info.value.retry_after >= 0

    def test_call_half_open_probe_success(self, cb: CircuitBreaker):
        for _ in range(3):
            cb.record_failure()
        time.sleep(0.25)
        result = cb.call(lambda: "recovered")
        assert result == "recovered"
        assert cb.state == CircuitBreaker.CLOSED

    def test_call_half_open_probe_failure(self, cb: CircuitBreaker):
        def failing():
            raise ConnectionError("still down")

        for _ in range(3):
            cb.record_failure()
        time.sleep(0.25)
        with pytest.raises(ConnectionError):
            cb.call(failing)
        assert cb.state == CircuitBreaker.OPEN

    def test_call_passes_args_kwargs(self, cb: CircuitBreaker):
        def adder(a, b, *, extra=0):
            return a + b + extra

        result = cb.call(adder, 1, 2, extra=10)
        assert result == 13


# ── CircuitBreaker.reset() ───────────────────────────────────────────

class TestCircuitBreakerReset:
    def test_reset_from_open(self, cb: CircuitBreaker):
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN
        cb.reset()
        assert cb.state == CircuitBreaker.CLOSED

    def test_reset_clears_failure_window(self, cb: CircuitBreaker):
        cb.record_failure()
        cb.record_failure()
        cb.reset()
        cb.record_failure()
        assert cb.state == CircuitBreaker.CLOSED


# ── CircuitBreaker.stats ─────────────────────────────────────────────

class TestCircuitBreakerStats:
    def test_stats_initial(self, cb: CircuitBreaker):
        s = cb.stats
        assert s["name"] == "test_service"
        assert s["state"] == "closed"
        assert s["recent_failures"] == 0
        assert s["total_calls"] == 0
        assert s["total_failures"] == 0

    def test_stats_after_calls(self, cb: CircuitBreaker):
        cb.call(lambda: None)
        cb.record_failure()
        s = cb.stats
        assert s["total_calls"] == 1
        assert s["total_failures"] == 1
        assert s["success_count"] == 1
        assert s["recent_failures"] == 1


# ── CircuitBreaker: thread safety ────────────────────────────────────

class TestCircuitBreakerThreadSafety:
    def test_concurrent_failures(self):
        cb = CircuitBreaker("concurrent", failure_threshold=50, window_seconds=5.0)
        errors = []

        def hammer():
            try:
                for _ in range(20):
                    cb.record_failure()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert cb.stats["total_failures"] == 100


# ── CircuitOpenError ─────────────────────────────────────────────────

class TestCircuitOpenError:
    def test_attributes(self):
        err = CircuitOpenError("my_service", retry_after=12.5)
        assert err.name == "my_service"
        assert err.retry_after == 12.5
        assert "my_service" in str(err)
        assert "OPEN" in str(err)

    def test_inherits_exception(self):
        assert issubclass(CircuitOpenError, Exception)


# ── @resilient decorator ─────────────────────────────────────────────

class TestResilientDecorator:
    """Integration of retry + circuit breaker."""

    def test_resilient_success_first_try(self, cb: CircuitBreaker):
        @resilient(cb, max_attempts=3)
        def succeed():
            return "ok"

        assert succeed() == "ok"
        assert cb.stats["total_calls"] == 1

    def test_resilient_retries_then_succeeds(self, cb: CircuitBreaker):
        call_count = 0

        @resilient(cb, max_attempts=3, initial_delay=0.01, backoff=1.0)
        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("flaky")
            return "ok"

        result = flaky()
        assert result == "ok"
        assert call_count == 3

    def test_resilient_exhausts_retries(self, cb: CircuitBreaker):
        @resilient(cb, max_attempts=2, initial_delay=0.01, backoff=1.0)
        def always_fail():
            raise TimeoutError("timeout")

        with pytest.raises(TimeoutError, match="timeout"):
            always_fail()

    def test_resilient_circuit_open_no_retry(self, cb: CircuitBreaker):
        """When the circuit is open, fail fast — do not retry."""
        for _ in range(3):
            cb.record_failure()

        call_count = 0

        @resilient(cb, max_attempts=5, initial_delay=0.01)
        def should_not_run():
            nonlocal call_count
            call_count += 1
            return "unreachable"

        with pytest.raises(CircuitOpenError):
            should_not_run()
        assert call_count == 0

    def test_resilient_selective_exceptions(self, cb: CircuitBreaker):
        """Only retry specified exception types."""
        @resilient(
            cb,
            max_attempts=3,
            initial_delay=0.01,
            exceptions=(ConnectionError,),
        )
        def wrong_type():
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            wrong_type()
        # Should have tried only once — ValueError is not in exceptions
        assert cb.stats["total_calls"] == 1

    def test_resilient_preserves_function_metadata(self, cb: CircuitBreaker):
        @resilient(cb)
        def my_function():
            """My docstring."""
            pass

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."

    def test_resilient_records_failures_on_circuit_breaker(self, cb: CircuitBreaker):
        @resilient(cb, max_attempts=3, initial_delay=0.01, backoff=1.0)
        def failing():
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            failing()

        # All 3 attempts should have recorded failures
        assert cb.stats["total_failures"] == 3


# ── Pre-configured instances ─────────────────────────────────────────

class TestPreConfiguredInstances:
    def test_sharepoint_cb_config(self):
        assert SHAREPOINT_CB.name == "sharepoint"
        assert SHAREPOINT_CB.failure_threshold == 5
        assert SHAREPOINT_CB.recovery_timeout == 60.0

    def test_meta_api_cb_config(self):
        assert META_API_CB.name == "meta_api"
        assert META_API_CB.failure_threshold == 3

    def test_youtube_cb_config(self):
        assert YOUTUBE_CB.name == "youtube_api"
        assert YOUTUBE_CB.failure_threshold == 3

    def test_anthropic_cb_config(self):
        assert ANTHROPIC_CB.name == "anthropic"
        assert ANTHROPIC_CB.failure_threshold == 2
        assert ANTHROPIC_CB.recovery_timeout == 60.0

    def test_twitter_cb_config(self):
        assert TWITTER_CB.name == "twitter_api"
        assert TWITTER_CB.failure_threshold == 3


# ── get_circuit_breaker() ────────────────────────────────────────────

class TestGetCircuitBreaker:
    def test_known_platforms(self):
        assert get_circuit_breaker("instagram") is META_API_CB
        assert get_circuit_breaker("facebook") is META_API_CB
        assert get_circuit_breaker("youtube") is YOUTUBE_CB
        assert get_circuit_breaker("x") is TWITTER_CB
        assert get_circuit_breaker("twitter") is TWITTER_CB

    def test_unknown_platform(self):
        assert get_circuit_breaker("tiktok") is None
        assert get_circuit_breaker("threads") is None

    def test_platform_lookup_matches_dict(self):
        for platform, expected_cb in PLATFORM_CIRCUIT_BREAKERS.items():
            assert get_circuit_breaker(platform) is expected_cb


# ── repr ─────────────────────────────────────────────────────────────

class TestCircuitBreakerRepr:
    def test_repr(self, cb: CircuitBreaker):
        r = repr(cb)
        assert "test_service" in r
        assert "closed" in r
        assert "threshold=3" in r
