"""Pin: system_health aggregator catches cascade failures (Lever P).

Pre-2026-06-21 each of the 5 circuit breakers tracked state
independently. When 3+ platforms degraded simultaneously (cascade
failure), operators got 3 separate WARN logs but no single signal
saying "the system as a whole is degraded — investigate the common
cause".

Lever P aggregates all 5 breakers + flags ``is_degraded=True`` when
≥3 are non-CLOSED. These pins lock in:

  1. ``CircuitState.is_open`` is True for both OPEN and HALF_OPEN
     (in-recovery counts as "not healthy yet")
  2. ``SystemHealth.open_breakers`` lists non-CLOSED breaker names
  3. ``SystemHealth.is_degraded`` flips at the configured threshold
  4. Default threshold is 3 (≥60% of platforms misbehaving)
  5. Custom threshold via kwarg overrides default
  6. Missing-breaker handling — single bad import doesn't crash aggregator
  7. snapshot_breaker returns None for unknown breaker names
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestCircuitState:
    def test_open_state_is_open(self):
        from genlab_core.monitoring.system_health import CircuitState

        s = CircuitState(name="x", state="open", total_calls=10, total_failures=5)
        assert s.is_open is True

    def test_half_open_state_is_open(self):
        """In-recovery still counts as 'not healthy yet' — operators
        should treat HALF_OPEN the same as OPEN for degradation purposes."""
        from genlab_core.monitoring.system_health import CircuitState

        s = CircuitState(name="x", state="half_open", total_calls=10, total_failures=5)
        assert s.is_open is True

    def test_closed_state_is_not_open(self):
        from genlab_core.monitoring.system_health import CircuitState

        s = CircuitState(name="x", state="closed", total_calls=10, total_failures=0)
        assert s.is_open is False


class TestSystemHealthAggregation:
    def _fake_breaker(self, name: str, state: str, calls: int = 100, failures: int = 5):
        """Build a fake CircuitBreaker that satisfies the aggregator's
        attribute reads."""
        b = MagicMock()
        b.name = name
        b.state = state
        b._total_calls = calls
        b._total_failures = failures
        return b

    def test_all_closed_not_degraded(self):
        """5/5 healthy → is_degraded=False."""
        from genlab_core.monitoring import system_health

        fakes = {
            "SHAREPOINT_CB": self._fake_breaker("sharepoint", "closed"),
            "META_API_CB": self._fake_breaker("meta_api", "closed"),
            "YOUTUBE_CB": self._fake_breaker("youtube", "closed"),
            "ANTHROPIC_CB": self._fake_breaker("anthropic", "closed"),
            "TWITTER_CB": self._fake_breaker("twitter", "closed"),
        }
        with patch.object(
            system_health, "_load_breaker", side_effect=lambda n: fakes.get(f"{n.upper()}_CB")
        ):
            health = system_health.get_system_health()

        assert health.open_count == 0
        assert health.closed_count == 5
        assert health.is_degraded is False
        assert health.open_breakers == []

    def test_two_open_not_degraded(self):
        """2 of 5 OPEN — below default threshold of 3 → not degraded.
        This is the "one bad platform isn't a cascade" check."""
        from genlab_core.monitoring import system_health

        fakes = {
            "SHAREPOINT_CB": self._fake_breaker("sharepoint", "open"),
            "META_API_CB": self._fake_breaker("meta_api", "open"),
            "YOUTUBE_CB": self._fake_breaker("youtube", "closed"),
            "ANTHROPIC_CB": self._fake_breaker("anthropic", "closed"),
            "TWITTER_CB": self._fake_breaker("twitter", "closed"),
        }
        with patch.object(
            system_health, "_load_breaker", side_effect=lambda n: fakes.get(f"{n.upper()}_CB")
        ):
            health = system_health.get_system_health()

        assert health.open_count == 2
        assert health.is_degraded is False
        # Open breakers listed correctly
        assert sorted(health.open_breakers) == ["meta_api", "sharepoint"]

    def test_three_open_is_degraded(self):
        """3 of 5 OPEN → meets default threshold → degraded.
        THE pin: cascade failure becomes visible as a single signal."""
        from genlab_core.monitoring import system_health

        fakes = {
            "SHAREPOINT_CB": self._fake_breaker("sharepoint", "open"),
            "META_API_CB": self._fake_breaker("meta_api", "open"),
            "YOUTUBE_CB": self._fake_breaker("youtube", "open"),
            "ANTHROPIC_CB": self._fake_breaker("anthropic", "closed"),
            "TWITTER_CB": self._fake_breaker("twitter", "closed"),
        }
        with patch.object(
            system_health, "_load_breaker", side_effect=lambda n: fakes.get(f"{n.upper()}_CB")
        ):
            health = system_health.get_system_health()

        assert health.open_count == 3
        assert health.is_degraded is True
        assert sorted(health.open_breakers) == ["meta_api", "sharepoint", "youtube"]

    def test_half_open_counts_toward_degraded(self):
        """A HALF_OPEN breaker is in-recovery — still not healthy.
        Should count toward the degraded threshold."""
        from genlab_core.monitoring import system_health

        fakes = {
            "SHAREPOINT_CB": self._fake_breaker("sharepoint", "open"),
            "META_API_CB": self._fake_breaker("meta_api", "half_open"),
            "YOUTUBE_CB": self._fake_breaker("youtube", "half_open"),
            "ANTHROPIC_CB": self._fake_breaker("anthropic", "closed"),
            "TWITTER_CB": self._fake_breaker("twitter", "closed"),
        }
        with patch.object(
            system_health, "_load_breaker", side_effect=lambda n: fakes.get(f"{n.upper()}_CB")
        ):
            health = system_health.get_system_health()

        assert health.open_count == 3  # 1 OPEN + 2 HALF_OPEN
        assert health.is_degraded is True

    def test_custom_threshold_overrides_default(self):
        """Operators can tune the threshold per deployment via kwarg."""
        from genlab_core.monitoring import system_health

        fakes = {
            "SHAREPOINT_CB": self._fake_breaker("sharepoint", "open"),
            "META_API_CB": self._fake_breaker("meta_api", "closed"),
            "YOUTUBE_CB": self._fake_breaker("youtube", "closed"),
            "ANTHROPIC_CB": self._fake_breaker("anthropic", "closed"),
            "TWITTER_CB": self._fake_breaker("twitter", "closed"),
        }
        with patch.object(
            system_health, "_load_breaker", side_effect=lambda n: fakes.get(f"{n.upper()}_CB")
        ):
            # With threshold=1, even one OPEN breaker is degraded
            health = system_health.get_system_health(degraded_threshold=1)

        assert health.degraded_threshold == 1
        assert health.is_degraded is True


class TestRobustness:
    def test_missing_breaker_returns_partial_snapshot(self):
        """If one named breaker can't be loaded, the aggregator omits it
        from the snapshot rather than crashing. Open count is computed
        from what IS loaded."""
        from genlab_core.monitoring import system_health

        def _partial_loader(name: str):
            if name == "twitter":
                return None  # simulated missing breaker
            fake = MagicMock()
            fake.name = name
            fake.state = "closed"
            fake._total_calls = 0
            fake._total_failures = 0
            return fake

        with patch.object(system_health, "_load_breaker", side_effect=_partial_loader):
            health = system_health.get_system_health()

        # 4 breakers loaded, 1 missing
        assert len(health.breakers) == 4
        assert health.is_degraded is False

    def test_snapshot_breaker_returns_none_for_unknown_name(self):
        from genlab_core.monitoring.system_health import snapshot_breaker

        # No "made_up" breaker in http/circuit_breaker.py — should
        # return None, not raise.
        result = snapshot_breaker("made_up_service")
        assert result is None

    def test_snapshot_breaker_handles_breaker_exception(self):
        """If the breaker's .state property raises (hung lock, etc.),
        snapshot returns None rather than blocking the aggregator."""
        from genlab_core.monitoring import system_health

        broken = MagicMock()
        broken.name = "broken"
        # Make .state raise when accessed
        type(broken).state = property(lambda self: (_ for _ in ()).throw(RuntimeError("hung")))

        with patch.object(system_health, "_load_breaker", return_value=broken):
            result = system_health.snapshot_breaker("anything")

        assert result is None
