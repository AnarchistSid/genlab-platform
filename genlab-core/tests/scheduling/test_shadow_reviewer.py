"""Tests for genlab_core.scheduling.shadow_reviewer.

The shadow reviewer runs a scheduled LLM pass over VISUAL_READY
blueprints to grow calibration samples without waiting on operator
dashboard clicks. It writes to auto_approval_calibration with
source='shadow_reviewer' — the paired migration + calibration_logger
extension keep it distinguishable from operator signal.

See:
* [[class-of-bug-signal-loss-through-merged-failure-paths]]
* Rule #22 (CLAUDE.md): shadow signal must NOT trigger enrollment
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestFeatureFlag:
    def test_off_by_default(self, monkeypatch):
        from genlab_core.scheduling.shadow_reviewer import is_enabled

        monkeypatch.delenv("GENLAB_SHADOW_REVIEWER_ENABLED", raising=False)
        assert is_enabled() is False

    def test_true_lowercase_enables(self, monkeypatch):
        from genlab_core.scheduling.shadow_reviewer import is_enabled

        monkeypatch.setenv("GENLAB_SHADOW_REVIEWER_ENABLED", "true")
        assert is_enabled() is True

    def test_one_does_not_enable(self, monkeypatch):
        """Strict pattern — '1' should NOT enable. Matches the
        discipline in linucb.py _temporal_context_enabled + prevents
        the ambiguity that bit AUTO #2 rollout."""
        from genlab_core.scheduling.shadow_reviewer import is_enabled

        monkeypatch.setenv("GENLAB_SHADOW_REVIEWER_ENABLED", "1")
        assert is_enabled() is False

    def test_random_string_does_not_enable(self, monkeypatch):
        from genlab_core.scheduling.shadow_reviewer import is_enabled

        monkeypatch.setenv("GENLAB_SHADOW_REVIEWER_ENABLED", "yes")
        assert is_enabled() is False


class TestEvaluateBlueprintFlagGate:
    def test_flag_off_returns_none(self, monkeypatch):
        """When the flag is off, evaluate_blueprint returns None so
        the caller treats it as skip — never accidentally writes."""
        from genlab_core.scheduling.shadow_reviewer import evaluate_blueprint

        monkeypatch.delenv("GENLAB_SHADOW_REVIEWER_ENABLED", raising=False)
        result = evaluate_blueprint({"niche_id": "gaming", "hook_text": "test"})
        assert result is None


class TestEvaluateBlueprintFailurePaths:
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("GENLAB_SHADOW_REVIEWER_ENABLED", "true")

    def test_missing_api_key_returns_auth_error(self, monkeypatch):
        from genlab_core.scheduling.shadow_reviewer import evaluate_blueprint

        self._enable_flag(monkeypatch)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        result = evaluate_blueprint({"niche_id": "gaming"})
        assert result is not None
        assert result.is_error
        assert result.error_reason == "auth"

    def test_llm_credit_exhaustion_classified(self, monkeypatch):
        """Anthropic exhaustion must classify to 'credit_exhausted'
        so the CLI's short-circuit-on-fatal check kicks in — avoids
        burning through 100 blueprints against a dead API."""
        from genlab_core.scheduling.shadow_reviewer import evaluate_blueprint

        self._enable_flag(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        with patch("anthropic.Anthropic") as mock_anthropic:
            client = MagicMock()
            client.messages.create.side_effect = RuntimeError(
                "Error 400: credit balance is too low"
            )
            mock_anthropic.return_value = client

            result = evaluate_blueprint({"niche_id": "gaming"})

        assert result is not None
        assert result.is_error
        assert result.error_reason == "credit_exhausted", (
            f"Expected credit_exhausted, got {result.error_reason}. "
            "The classify_llm_error message-marker path must escalate "
            "'credit balance too low' above the class-name fallback."
        )

    def test_non_json_response_classified_as_invalid_request(self, monkeypatch):
        from genlab_core.scheduling.shadow_reviewer import evaluate_blueprint

        self._enable_flag(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        with patch("anthropic.Anthropic") as mock_anthropic:
            client = MagicMock()
            response = MagicMock()
            response.content = [MagicMock(text="this is not JSON")]
            client.messages.create.return_value = response
            mock_anthropic.return_value = client

            result = evaluate_blueprint({"niche_id": "gaming"})

        assert result is not None
        assert result.is_error
        assert result.error_reason == "invalid_request"


class TestEvaluateBlueprintSuccess:
    def _enable_flag(self, monkeypatch):
        monkeypatch.setenv("GENLAB_SHADOW_REVIEWER_ENABLED", "true")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def test_returns_would_approve_true(self, monkeypatch):
        from genlab_core.scheduling.shadow_reviewer import evaluate_blueprint

        self._enable_flag(monkeypatch)

        with patch("anthropic.Anthropic") as mock_anthropic:
            client = MagicMock()
            response = MagicMock()
            response.content = [
                MagicMock(text='{"would_approve": true, "confidence": 0.85, "reason": "strong hook"}')
            ]
            client.messages.create.return_value = response
            mock_anthropic.return_value = client

            result = evaluate_blueprint(
                {"niche_id": "gaming", "hook_text": "Big trailer drop just landed"}
            )

        assert result is not None
        assert not result.is_error
        assert result.would_approve is True
        assert result.confidence == 0.85
        assert result.reason == "strong hook"

    def test_confidence_clamped_to_unit_interval(self, monkeypatch):
        """LLM may return confidence outside [0, 1] — must be clamped
        so downstream stats aren't polluted by wild values."""
        from genlab_core.scheduling.shadow_reviewer import evaluate_blueprint

        self._enable_flag(monkeypatch)

        with patch("anthropic.Anthropic") as mock_anthropic:
            client = MagicMock()
            response = MagicMock()
            response.content = [
                MagicMock(text='{"would_approve": false, "confidence": 1.5, "reason": "bad"}')
            ]
            client.messages.create.return_value = response
            mock_anthropic.return_value = client

            result = evaluate_blueprint({"niche_id": "gaming"})

        assert result.confidence == 1.0  # clamped

    def test_code_fence_stripped(self, monkeypatch):
        """LLMs sometimes wrap JSON in ```json ... ``` — parser must
        strip the fence before json.loads."""
        from genlab_core.scheduling.shadow_reviewer import evaluate_blueprint

        self._enable_flag(monkeypatch)

        with patch("anthropic.Anthropic") as mock_anthropic:
            client = MagicMock()
            response = MagicMock()
            response.content = [
                MagicMock(
                    text='```json\n{"would_approve": true, "confidence": 0.7, "reason": "ok"}\n```'
                )
            ]
            client.messages.create.return_value = response
            mock_anthropic.return_value = client

            result = evaluate_blueprint({"niche_id": "gaming"})

        assert result is not None
        assert not result.is_error
        assert result.would_approve is True


class TestPromptShape:
    def test_prompt_asks_for_strict_json(self):
        """Source-grep pin: the system prompt must instruct STRICT JSON
        output so parse failures fall through to invalid_request cleanly.
        A prompt regression that drops the JSON directive would produce
        prose responses → every shadow call classified as
        invalid_request → dead shadow signal."""
        import inspect

        from genlab_core.scheduling.shadow_reviewer import _build_prompt

        src = inspect.getsource(_build_prompt)
        # System prompt must reference JSON
        assert "STRICT JSON" in src or "strict JSON" in src.lower(), (
            "_build_prompt must instruct STRICT JSON output"
        )

    def test_prompt_includes_hook_and_niche(self):
        from genlab_core.scheduling.shadow_reviewer import _build_prompt

        system, user = _build_prompt(
            {"niche_id": "gaming", "hook_text": "Big test hook"}
        )
        assert "gaming" in user
        assert "Big test hook" in user


class TestCalibrationLoggerSourceParam:
    """The paired calibration_logger extension accepts a source kwarg
    that defaults to 'operator'. Existing callers unaffected."""

    def test_log_signature_accepts_source(self):
        import inspect

        from genlab_core.scheduling.calibration_logger import log

        sig = inspect.signature(log)
        assert "source" in sig.parameters
        assert sig.parameters["source"].default == "operator"

    def test_stats_signature_accepts_source_filter(self):
        import inspect

        from genlab_core.scheduling.calibration_logger import stats

        sig = inspect.signature(stats)
        assert "source_filter" in sig.parameters
        assert sig.parameters["source_filter"].default == "operator"


class TestSystemdUnits:
    """Deploy-time pins for the shadow reviewer unit files."""

    from pathlib import Path

    _PHASE2 = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "systemd-phase2"
    )

    def test_service_present(self):
        unit = self._PHASE2 / "genlab-shadow-reviewer.service"
        assert unit.is_file()

    def test_timer_present(self):
        unit = self._PHASE2 / "genlab-shadow-reviewer.timer"
        assert unit.is_file()

    def test_service_has_failure_alert(self):
        content = (
            self._PHASE2 / "genlab-shadow-reviewer.service"
        ).read_text()
        assert "OnFailure=genlab-service-failure-alert@%n.service" in content

    def test_timer_persistent(self):
        content = (
            self._PHASE2 / "genlab-shadow-reviewer.timer"
        ).read_text()
        assert "Persistent=true" in content

    def test_service_calls_run_shadow_reviewer(self):
        content = (
            self._PHASE2 / "genlab-shadow-reviewer.service"
        ).read_text()
        assert "run_shadow_reviewer.py" in content
