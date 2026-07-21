"""Pin tests for `check_anthropic_credit` (2026-07-21).

Prevents 4th recurrence of the Anthropic credit-exhaustion class-of-bug
(hit 2026-06-XX, 2026-07-06, 2026-07-18). Exhaustion caused writers to
return refusal preambles, auto_approval_gate to silently fail, and
dashboards to show VISUAL_READY blueprints stuck — nothing surfaced
"Anthropic is broken" until manual investigation.

Design:
  * Fires 1-token probe against Haiku (cheapest model)
  * Only alerts on exhaustion-class exceptions (uses fallback.should_fallback)
  * Fail-open on any non-exhaustion error (network, auth, tooling)
  * Kill switch: GENLAB_ANTHROPIC_HEALTHCHECK_DISABLED=1
  * Skip if ANTHROPIC_API_KEY unset (test/dev environments)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.monitoring.checks.infrastructure import check_anthropic_credit


class _FakeExhaustionError(Exception):
    """Mimics the shape of Anthropic's BadRequestError with credit-low body."""

    def __str__(self):
        return "credit balance is too low"


class TestAnthropicCredit:
    def test_healthy_probe_returns_no_alerts(self, monkeypatch):
        """Successful 1-token probe → no alert."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        with patch("anthropic.Anthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create.return_value = MagicMock(
                content=[MagicMock(text="hi")]
            )
            alerts = check_anthropic_credit()
        assert alerts == []

    def test_exhaustion_returns_critical_alert(self, monkeypatch):
        """`credit balance is too low` triggers CRITICAL alert."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        with patch("anthropic.Anthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create.side_effect = _FakeExhaustionError()
            alerts = check_anthropic_credit()
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert alerts[0].check == "anthropic_credit_exhausted"
        assert "billing" in alerts[0].auto_fix.lower()

    def test_auth_error_silently_skipped(self, monkeypatch):
        """401 unauthorized → not an exhaustion signal → no alert.
        (Separately investigated via credential check, not this probe.)"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")

        class _AuthError(Exception):
            def __str__(self):
                return "401 unauthorized"

        with patch("anthropic.Anthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create.side_effect = _AuthError()
            alerts = check_anthropic_credit()
        assert alerts == []

    def test_network_error_silently_skipped(self, monkeypatch):
        """ConnectionError → not exhaustion → no alert."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        with patch("anthropic.Anthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create.side_effect = ConnectionError("DNS failed")
            alerts = check_anthropic_credit()
        assert alerts == []

    def test_kill_switch_env_var(self, monkeypatch):
        """GENLAB_ANTHROPIC_HEALTHCHECK_DISABLED=1 skips the probe entirely."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        monkeypatch.setenv("GENLAB_ANTHROPIC_HEALTHCHECK_DISABLED", "1")
        with patch("anthropic.Anthropic") as MockClient:
            alerts = check_anthropic_credit()
        assert alerts == []
        MockClient.assert_not_called(), "kill switch must prevent any SDK call"

    def test_missing_api_key_skipped(self, monkeypatch):
        """No API key configured (dev/test) → silent skip."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        with patch("anthropic.Anthropic") as MockClient:
            alerts = check_anthropic_credit()
        assert alerts == []
        MockClient.assert_not_called()

    def test_sdk_missing_silently_skipped(self, monkeypatch):
        """If anthropic package somehow uninstalled, monitor still runs."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        import sys

        # Simulate import failure by removing from sys.modules + patch
        with patch.dict(sys.modules, {"anthropic": None}):
            alerts = check_anthropic_credit()
        assert alerts == []

    def test_alert_details_include_error_snippet(self, monkeypatch):
        """Operator needs the actual error text to confirm root cause."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        with patch("anthropic.Anthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create.side_effect = _FakeExhaustionError()
            alerts = check_anthropic_credit()
        assert alerts[0].details is not None
        assert "error_snippet" in alerts[0].details
        assert "credit balance" in alerts[0].details["error_snippet"]

    def test_probe_uses_min_tokens(self, monkeypatch):
        """max_tokens=1 keeps cost negligible ($0.0001/day at 24 fires)."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fake")
        with patch("anthropic.Anthropic") as MockClient:
            instance = MockClient.return_value
            instance.messages.create.return_value = MagicMock(
                content=[MagicMock(text="hi")]
            )
            check_anthropic_credit()
        call = instance.messages.create.call_args
        assert call.kwargs["max_tokens"] == 1, (
            "probe must stay at max_tokens=1 — bumping this multiplies "
            "the annual cost linearly"
        )
