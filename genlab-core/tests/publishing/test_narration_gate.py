"""Pin narration_gate: env + YAML gate + get_narration_config defaults.

NARR-01 (2026-08-18). Two-key gate: env master + niche.yaml canary.
"""
from __future__ import annotations

import logging

import pytest

from genlab_core.publishing.narration_gate import (
    _ROLLOUT_ENV,
    get_narration_config,
    is_narration_enabled_for,
)


class TestEnvMasterFlag:
    """The env flag is the master kill switch. Off = narration off
    everywhere regardless of niche YAML."""

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE", "On"])
    def test_on_tokens_env_enabled(self, monkeypatch, val):
        monkeypatch.setenv(_ROLLOUT_ENV, val)
        cfg = {"narration": {"enabled": True}}
        assert is_narration_enabled_for("ai_creators", cfg) is True

    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "FALSE"])
    def test_off_tokens_env_disabled(self, monkeypatch, val):
        monkeypatch.setenv(_ROLLOUT_ENV, val)
        cfg = {"narration": {"enabled": True}}  # YAML on but env off
        assert is_narration_enabled_for("ai_creators", cfg) is False

    def test_env_unset_disabled(self, monkeypatch):
        monkeypatch.delenv(_ROLLOUT_ENV, raising=False)
        cfg = {"narration": {"enabled": True}}
        assert is_narration_enabled_for("ai_creators", cfg) is False


class TestPerNicheYaml:
    """Per-niche YAML is the canary allowlist. Missing key = off."""

    def test_missing_narration_key_disabled(self, monkeypatch):
        monkeypatch.setenv(_ROLLOUT_ENV, "1")
        cfg = {"niche_id": "gaming"}  # no narration section
        assert is_narration_enabled_for("gaming", cfg) is False

    def test_narration_enabled_false_disabled(self, monkeypatch):
        monkeypatch.setenv(_ROLLOUT_ENV, "1")
        cfg = {"narration": {"enabled": False}}
        assert is_narration_enabled_for("gaming", cfg) is False

    def test_narration_section_wrong_shape_disabled(self, monkeypatch):
        """narration: 'yes' (string not dict) → fail-open to False."""
        monkeypatch.setenv(_ROLLOUT_ENV, "1")
        cfg = {"narration": "yes"}
        assert is_narration_enabled_for("gaming", cfg) is False

    def test_niche_config_none_disabled(self, monkeypatch):
        monkeypatch.setenv(_ROLLOUT_ENV, "1")
        assert is_narration_enabled_for("gaming", None) is False


class TestBothMustBeTruthy:
    """Both gates required — matches every other canary in the tree."""

    def test_env_on_yaml_off_disabled(self, monkeypatch):
        monkeypatch.setenv(_ROLLOUT_ENV, "1")
        cfg = {"narration": {"enabled": False}}
        assert is_narration_enabled_for("ai_creators", cfg) is False

    def test_env_off_yaml_on_disabled(self, monkeypatch):
        monkeypatch.setenv(_ROLLOUT_ENV, "0")
        cfg = {"narration": {"enabled": True}}
        assert is_narration_enabled_for("ai_creators", cfg) is False

    def test_both_on_enabled(self, monkeypatch):
        monkeypatch.setenv(_ROLLOUT_ENV, "1")
        cfg = {"narration": {"enabled": True}}
        assert is_narration_enabled_for("ai_creators", cfg) is True


class TestFailOpen:
    """Rule #19: any unexpected exception → WARN log + return False.
    Never crash the caller for a canary check."""

    def test_niche_config_wrong_type_no_crash(self, monkeypatch, caplog):
        monkeypatch.setenv(_ROLLOUT_ENV, "1")
        # Pass a list where dict expected — dict.get path would raise
        # AttributeError. Gate must catch + return False.
        with caplog.at_level(logging.WARNING):
            result = is_narration_enabled_for("gaming", ["not", "a", "dict"])  # type: ignore[arg-type]
        assert result is False


class TestGetNarrationConfigDefaults:
    """Missing YAML keys resolve to sensible defaults per plan §3.2."""

    def test_all_defaults_when_niche_config_none(self):
        cfg = get_narration_config(None)
        assert cfg["enabled"] is False
        assert cfg["vo_bed_duck_db"] == -8
        assert cfg["target_lufs"] == -14.0
        assert cfg["wpm"] == 150
        assert cfg["tail_buffer_seconds"] == 2.0
        assert cfg["narration_vo_db"] == 0

    def test_all_defaults_when_narration_section_missing(self):
        cfg = get_narration_config({"niche_id": "gaming"})
        assert cfg["enabled"] is False
        assert cfg["target_lufs"] == -14.0

    def test_partial_override_preserves_other_defaults(self):
        cfg = get_narration_config({
            "narration": {"enabled": True, "wpm": 175},
        })
        assert cfg["enabled"] is True
        assert cfg["wpm"] == 175
        # non-overridden keys keep their defaults
        assert cfg["target_lufs"] == -14.0
        assert cfg["vo_bed_duck_db"] == -8

    def test_unknown_keys_ignored(self):
        """Rule 5: missing YAML keys never crash any niche.
        Unknown/typo keys are silently dropped rather than propagated."""
        cfg = get_narration_config({
            "narration": {"enabled": True, "typo_key": "value"},
        })
        assert "typo_key" not in cfg
        assert cfg["enabled"] is True

    def test_narration_section_wrong_shape_returns_defaults(self):
        """narration: 'yes' → return defaults, don't crash."""
        cfg = get_narration_config({"narration": "yes"})
        assert cfg["enabled"] is False
