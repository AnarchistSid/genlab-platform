"""Pin tests for the shared Anthropic → OpenAI fallback module (2026-07-21).

The 8 Anthropic-direct call sites share ONE circuit breaker via this
module so total exhaustion attempts is bounded regardless of how many
sites are firing. If these pins regress, the fallback becomes per-site
and total burn multiplies by the number of sites.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import genlab_core.llm.fallback as fb_module
from genlab_core.llm.fallback import (
    call_openai_fallback,
    cb_is_open,
    cb_record_exhaustion,
    cb_record_success,
    fallback_enabled,
    should_fallback,
    with_openai_fallback,
)


@pytest.fixture(autouse=True)
def _reset_state():
    fb_module._ANTHROPIC_EXHAUSTION_COUNT = 0
    fb_module._ANTHROPIC_CB_OPEN_UNTIL = 0.0
    yield
    fb_module._ANTHROPIC_EXHAUSTION_COUNT = 0
    fb_module._ANTHROPIC_CB_OPEN_UNTIL = 0.0


class TestShouldFallback:
    def test_credit_balance_too_low(self):
        assert should_fallback(Exception("credit balance is too low")) is True

    def test_ratelimit_class(self):
        exc = type("RateLimitError", (Exception,), {})("429")
        assert should_fallback(exc) is True

    def test_apistatus_class(self):
        exc = type("APIStatusError", (Exception,), {})("some server error")
        assert should_fallback(exc) is True

    def test_auth_error_rejected(self):
        assert should_fallback(Exception("401 unauthorized")) is False

    def test_network_error_rejected(self):
        assert should_fallback(ConnectionError("Connection refused")) is False


class TestFallbackEnabled:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("GENLAB_LLM_FALLBACK_ENABLED", raising=False)
        assert fallback_enabled() is True

    def test_explicit_off(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LLM_FALLBACK_ENABLED", "0")
        assert fallback_enabled() is False

    def test_explicit_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LLM_FALLBACK_ENABLED", "1")
        assert fallback_enabled() is True


class TestSharedCircuitBreaker:
    """The whole point of extracting this module: ONE breaker shared
    across all Anthropic call sites, not per-site."""

    def test_cb_starts_closed(self):
        assert cb_is_open() is False

    def test_cb_opens_after_threshold(self):
        for _ in range(3):
            cb_record_exhaustion()
        assert cb_is_open() is True

    def test_cb_stays_closed_below_threshold(self):
        for _ in range(2):
            cb_record_exhaustion()
        assert cb_is_open() is False

    def test_success_resets_counter(self):
        cb_record_exhaustion()
        cb_record_exhaustion()
        cb_record_success()
        assert fb_module._ANTHROPIC_EXHAUSTION_COUNT == 0
        assert cb_is_open() is False

    def test_success_closes_open_breaker(self):
        for _ in range(3):
            cb_record_exhaustion()
        assert cb_is_open() is True
        cb_record_success()
        assert cb_is_open() is False


class TestWithOpenaiFallback:
    """The convenience wrapper that other Anthropic sites can adopt."""

    def test_success_path_passes_through(self, monkeypatch):
        """No fallback when the primary call succeeds."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        with patch("genlab_core.llm.fallback.call_openai_fallback") as fb:
            result = with_openai_fallback(
                lambda: "primary-result",
                system="s",
                user="u",
                max_tokens=100,
                temperature=0.5,
                site_label="test",
            )
        assert result == "primary-result"
        fb.assert_not_called()

    def test_exhaustion_triggers_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

        def failing():
            raise Exception("credit balance is too low")

        with patch(
            "genlab_core.llm.fallback.call_openai_fallback",
            return_value="fallback-result",
        ) as fb:
            result = with_openai_fallback(
                failing,
                system="s",
                user="u",
                max_tokens=100,
                temperature=0.5,
                site_label="test-site",
            )
        assert result == "fallback-result"
        fb.assert_called_once()

    def test_auth_error_re_raises(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

        def failing():
            raise Exception("401 unauthorized")

        with patch("genlab_core.llm.fallback.call_openai_fallback") as fb:
            with pytest.raises(Exception, match="401 unauthorized"):
                with_openai_fallback(
                    failing,
                    system="s",
                    user="u",
                    max_tokens=100,
                    temperature=0.5,
                )
        fb.assert_not_called()

    def test_cb_open_bypasses_primary_call(self, monkeypatch):
        """CB open → don't even attempt the anthropic_call."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        fb_module._ANTHROPIC_CB_OPEN_UNTIL = time.time() + 300

        primary = MagicMock(return_value="primary")
        with patch(
            "genlab_core.llm.fallback.call_openai_fallback",
            return_value="fb",
        ):
            result = with_openai_fallback(
                primary,
                system="s",
                user="u",
                max_tokens=100,
                temperature=0.5,
            )
        assert result == "fb"
        primary.assert_not_called()

    def test_no_openai_key_re_raises(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        def failing():
            raise Exception("credit balance is too low")

        with pytest.raises(Exception, match="credit balance is too low"):
            with_openai_fallback(
                failing,
                system="s",
                user="u",
                max_tokens=100,
                temperature=0.5,
            )

    def test_flag_off_disables(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("GENLAB_LLM_FALLBACK_ENABLED", "0")

        def failing():
            raise Exception("credit balance is too low")

        with patch("genlab_core.llm.fallback.call_openai_fallback") as fb:
            with pytest.raises(Exception, match="credit balance is too low"):
                with_openai_fallback(
                    failing,
                    system="s",
                    user="u",
                    max_tokens=100,
                    temperature=0.5,
                )
        fb.assert_not_called()


class TestSharedStateAcrossSites:
    """Simulates 2 different sites firing — CB opens after 3 total
    exhaustions (not 3-per-site)."""

    def test_two_sites_share_counter(self):
        """Site A exhausts twice, site B exhausts once → CB should open."""
        # Site A calls (2 exhaustions)
        cb_record_exhaustion()
        cb_record_exhaustion()
        assert cb_is_open() is False

        # Site B calls (1 more exhaustion, hitting threshold)
        cb_record_exhaustion()
        assert cb_is_open() is True

    def test_success_from_any_site_resets(self):
        """A success from ONE site resets counter for ALL sites."""
        for _ in range(3):
            cb_record_exhaustion()
        assert cb_is_open() is True
        # Site X succeeds → CB closes for everyone
        cb_record_success()
        assert cb_is_open() is False


class TestBothProvidersExhausted:
    """The 2026-07-21 live scenario: Anthropic AND OpenAI both exhausted.
    Pin the behavior so a future refactor doesn't accidentally swallow
    the OpenAI failure OR the Anthropic failure — operator needs BOTH
    surfaced in one journal entry."""

    def test_openai_also_fails_re_raises_original_anthropic(self, monkeypatch):
        """When OpenAI fallback ALSO fails, re-raise the ORIGINAL
        Anthropic exception (not the OpenAI one). Downstream error
        classifiers were built against Anthropic exceptions; if we
        swap in the OpenAI exception mid-flight they misclassify
        the failure."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

        anthropic_exc = Exception("credit balance is too low")
        openai_exc = Exception("You exceeded your current quota (429)")

        def failing_anthropic():
            raise anthropic_exc

        with patch(
            "genlab_core.llm.fallback.call_openai_fallback",
            side_effect=openai_exc,
        ):
            with pytest.raises(Exception) as exc_info:
                with_openai_fallback(
                    failing_anthropic,
                    system="s",
                    user="u",
                    max_tokens=100,
                    temperature=0.5,
                    site_label="test-both-exhausted",
                )

        # Original Anthropic error is re-raised (not the OpenAI 429).
        # `from openai_exc` attaches the OpenAI cause on __cause__ so
        # `logger.exception` still surfaces both in traceback.
        assert str(exc_info.value) == "credit balance is too low"
        assert exc_info.value.__cause__ is openai_exc

    def test_exhaustion_still_counted_when_openai_also_fails(self, monkeypatch):
        """Even when the OpenAI fallback fails, the Anthropic exhaustion
        counter must still increment — this is what shortcuts subsequent
        calls to the CB-open fast path, saving Anthropic burn."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")

        def failing_anthropic():
            raise Exception("credit balance is too low")

        with patch(
            "genlab_core.llm.fallback.call_openai_fallback",
            side_effect=Exception("openai also down"),
        ):
            for _ in range(3):
                try:
                    with_openai_fallback(
                        failing_anthropic,
                        system="s",
                        user="u",
                        max_tokens=100,
                        temperature=0.5,
                    )
                except Exception:
                    pass
        # CB should be open after 3 counted exhaustions
        assert cb_is_open() is True


class TestCBCooldownExpiry:
    """The CB is TIME-BOUNDED (opens for _CB_COOLDOWN_S seconds).
    Pin that it auto-closes after cooldown — this is what lets
    Anthropic recovery be detected automatically without operator
    intervention."""

    def test_cb_auto_closes_after_cooldown_deadline(self):
        """Set the deadline in the past → CB reports closed."""
        fb_module._ANTHROPIC_CB_OPEN_UNTIL = time.time() - 1
        assert cb_is_open() is False, (
            "expired CB deadline must report as closed — otherwise "
            "Anthropic recovery is invisible until process restart"
        )

    def test_cb_open_at_current_time_still_open(self):
        """Deadline strictly in the future = still open."""
        fb_module._ANTHROPIC_CB_OPEN_UNTIL = time.time() + 300
        assert cb_is_open() is True

    def test_cb_default_cooldown_is_600s(self):
        """The 600s (10-min) cooldown is the operator-facing latency
        budget — how quickly Anthropic recovery gets picked up.
        Bumping this needs a deliberate operator decision."""
        assert fb_module._CB_COOLDOWN_S == 600
