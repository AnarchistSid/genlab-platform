"""Tests for auto_experiment_parser."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestConfidenceFilter:
    def test_high_confidence_accepted(self):
        from genlab_core.scheduling.auto_experiment_parser import (
            is_confidence_acceptable,
        )

        assert is_confidence_acceptable("high") is True

    def test_medium_confidence_accepted(self):
        from genlab_core.scheduling.auto_experiment_parser import (
            is_confidence_acceptable,
        )

        assert is_confidence_acceptable("medium") is True

    def test_low_confidence_rejected(self):
        from genlab_core.scheduling.auto_experiment_parser import (
            is_confidence_acceptable,
        )

        assert is_confidence_acceptable("low") is False

    def test_empty_rejected(self):
        from genlab_core.scheduling.auto_experiment_parser import (
            is_confidence_acceptable,
        )

        assert is_confidence_acceptable("") is False


class TestPromptShape:
    def test_prompt_includes_niche_and_prediction(self):
        from genlab_core.scheduling.auto_experiment_parser import _build_prompt

        system, user = _build_prompt(
            "reward >= 0.20 vs baseline 0.10",
            "gaming",
            ["style:gaming:comparison", "clip"],
        )
        assert "gaming" in user
        assert "reward >= 0.20 vs baseline 0.10" in user
        assert "style:gaming:comparison" in user

    def test_system_prompt_locks_json_schema(self):
        """Source-grep pin: the system prompt must explicitly instruct
        STRICT JSON with the specific schema. A regression that drops
        the schema directive would produce free-form responses that
        fail parsing — dead parser signal."""
        from genlab_core.scheduling.auto_experiment_parser import _build_prompt

        system, _ = _build_prompt("test", "gaming", [])
        assert "STRICT JSON" in system
        assert "arms" in system
        assert "expected_metric_shift" in system
        assert "duration_days" in system


class TestParseFailurePaths:
    def _enable_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def test_missing_api_key_returns_not_configured(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        spec, err = parse_testable_prediction("test", "gaming", [])
        assert spec is None
        assert err == "not_configured"

    def test_credit_exhausted_classified(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            client.messages.create.side_effect = RuntimeError(
                "credit balance is too low"
            )
            mock.return_value = client
            spec, err = parse_testable_prediction("test", "gaming", [])
        assert spec is None
        assert err == "credit_exhausted"

    def test_non_json_returns_invalid_json(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [MagicMock(text="not JSON at all")]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction("test", "gaming", [])
        assert spec is None
        assert err == "invalid_json"

    def test_llm_marks_unparseable(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(text='{"unparseable": true, "reason": "no numeric target"}')
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction("vague prediction", "gaming", [])
        assert spec is None
        assert err.startswith("unparseable:")


class TestParseSuccess:
    def _enable_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def test_valid_json_produces_spec(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '{"arms": ["gameplay_clip", "hot_take_opinion"], '
                        '"niche_id": "gaming", "expected_metric_shift": 0.07, '
                        '"duration_days": 14, "notes": "test rationale"}'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction(
                "reward >= 0.18 vs baseline 0.11",
                "gaming",
                # Both arms present in the real list — 2026-08-14
                # tightening rejects specs referencing invented arms
                ["gameplay_clip", "hot_take_opinion"],
            )
        assert err == ""
        assert spec is not None
        assert spec.arms == ["gameplay_clip", "hot_take_opinion"]
        assert spec.niche_id == "gaming"
        assert spec.expected_metric_shift == 0.07
        assert spec.duration_days == 14

    def test_metric_shift_clamped_to_unit_interval(self, monkeypatch):
        """LLM might return >1.0 → clamp; <0 → clamp."""
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '{"arms": ["a", "b"], "niche_id": "gaming", '
                        '"expected_metric_shift": 5.0, "duration_days": 14}'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction("test", "gaming", [])
        assert spec is not None
        assert spec.expected_metric_shift == 1.0  # clamped

    def test_duration_clamped_to_7_30_range(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        # Too-short → 7
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '{"arms": ["a", "b"], "niche_id": "gaming", '
                        '"expected_metric_shift": 0.1, "duration_days": 3}'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, _ = parse_testable_prediction("test", "gaming", [])
        assert spec is not None
        assert spec.duration_days == 7

    def test_code_fence_stripped(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '```json\n{"arms": ["a", "b"], '
                        '"niche_id": "gaming", '
                        '"expected_metric_shift": 0.1, '
                        '"duration_days": 14}\n```'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction("test", "gaming", [])
        assert err == ""
        assert spec is not None
        assert spec.arms == ["a", "b"]

    def test_missing_arms_returns_invalid_json(self, monkeypatch):
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '{"niche_id": "gaming", '
                        '"expected_metric_shift": 0.1, '
                        '"duration_days": 14}'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction("test", "gaming", [])
        assert spec is None
        assert "missing_arms" in err

    def test_invented_arm_rejected(self, monkeypatch):
        """2026-08-14 follow-up: LLM invented an arm not in the real
        bandit set → reject with 'invalid_arms:...' reason. This is
        the belt to the prompt's suspenders — even the tightened
        prompt can be ignored, validation is the hard stop.

        Motivating incident: Phase 3.D dry-run showed 30 of 42 running
        experiments had zero samples because the LLM had proposed
        arm_ids that never existed (metric names like
        'reward_binary_success', compound names like
        'scene_reaction__tiktok_instagram')."""
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '{"arms": ["gameplay_clip", "reward_binary_success"], '
                        '"niche_id": "gaming", '
                        '"expected_metric_shift": 0.1, '
                        '"duration_days": 14}'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction(
                "test", "gaming",
                ["gameplay_clip", "hot_take_opinion", "gaming_analysis"],
            )
        assert spec is None
        assert err.startswith("invalid_arms:")
        assert "reward_binary_success" in err

    def test_multiple_invented_arms_all_surfaced(self, monkeypatch):
        """When both arms are invalid, both surface in the error
        string so the operator can see the full extent of the
        strategist's hallucination."""
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '{"arms": ["ghost_arm_a", "ghost_arm_b"], '
                        '"niche_id": "gaming", '
                        '"expected_metric_shift": 0.1, '
                        '"duration_days": 14}'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction(
                "test", "gaming", ["real_arm_a", "real_arm_b"],
            )
        assert spec is None
        assert "ghost_arm_a" in err
        assert "ghost_arm_b" in err

    def test_empty_existing_arms_skips_validation(self, monkeypatch):
        """Test/cold-start case: when caller passes [] as existing
        arms, skip validation. Otherwise every spec would reject —
        that's the wrong default for the "no bandit arms exist yet"
        cold-start."""
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '{"arms": ["a", "b"], "niche_id": "gaming", '
                        '"expected_metric_shift": 0.1, '
                        '"duration_days": 14}'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction("test", "gaming", [])
        assert spec is not None
        assert err == ""

    def test_single_arm_returns_invalid_json(self, monkeypatch):
        """A/B experiment requires ≥2 arms — single-arm response
        is malformed."""
        from genlab_core.scheduling.auto_experiment_parser import (
            parse_testable_prediction,
        )

        self._enable_env(monkeypatch)
        with patch("anthropic.Anthropic") as mock:
            client = MagicMock()
            resp = MagicMock()
            resp.content = [
                MagicMock(
                    text=(
                        '{"arms": ["only_one"], "niche_id": "gaming", '
                        '"expected_metric_shift": 0.1, '
                        '"duration_days": 14}'
                    )
                )
            ]
            client.messages.create.return_value = resp
            mock.return_value = client
            spec, err = parse_testable_prediction("test", "gaming", [])
        assert spec is None
        assert "missing_arms" in err


class TestSystemdWire:
    from pathlib import Path

    _PHASE2 = (
        Path(__file__).resolve().parents[3]
        / "deploy"
        / "systemd-phase2"
    )

    def test_service_present(self):
        assert (self._PHASE2 / "genlab-experiment-parser.service").is_file()

    def test_timer_present(self):
        assert (self._PHASE2 / "genlab-experiment-parser.timer").is_file()

    def test_service_uses_apply(self):
        content = (self._PHASE2 / "genlab-experiment-parser.service").read_text()
        assert "parse_testable_predictions.py --apply" in content

    def test_service_has_failure_alert(self):
        content = (self._PHASE2 / "genlab-experiment-parser.service").read_text()
        assert "OnFailure=genlab-service-failure-alert@%n.service" in content

    def test_timer_fires_after_hypothesis_promote(self):
        """03:45 UTC — after strategist (02:00), strategist-apply (03:00),
        hypothesis-promote (03:15), proposal-auto-accept (03:30)."""
        content = (self._PHASE2 / "genlab-experiment-parser.timer").read_text()
        assert "03:45:00 UTC" in content
