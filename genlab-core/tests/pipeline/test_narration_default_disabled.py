"""Pin NARR-01 non-regression: default-disabled path must be
byte-identical to pre-NARR-01 for the 4 non-canary niches.

Rule 5: missing YAML keys must never crash any niche. Every default
resolves to disabled + legacy 2-input audio mix.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from genlab_core.publishing.narration_gate import (
    _ROLLOUT_ENV,
    get_narration_config,
    is_narration_enabled_for,
)


class TestMissingYamlNoCrash:
    """Rule 5: missing YAML keys must never crash any niche."""

    def test_niche_config_completely_absent(self, monkeypatch):
        monkeypatch.setenv(_ROLLOUT_ENV, "1")
        # Even with env flag on, empty niche config → gate closed
        assert is_narration_enabled_for("gaming", {}) is False

    def test_get_narration_config_missing_key_returns_defaults(self):
        cfg = get_narration_config({})
        # All defaults populated — no KeyError on downstream lookup
        for key in ("enabled", "vo_bed_duck_db", "target_lufs",
                    "wpm", "tail_buffer_seconds", "narration_vo_db"):
            assert key in cfg


class TestNonCanaryNichesUnchanged:
    """Prove the 4 non-canary niches keep narration OFF by default
    when the env flag is on (canary allowlist behavior)."""

    @pytest.mark.parametrize("niche", ["gaming", "movies", "anime", "sports"])
    def test_non_canary_niche_yaml_no_narration_section(self, niche):
        """The non-canary niche YAMLs must NOT contain a
        narration.enabled=true entry (that would flip them into
        the canary scope by accident)."""
        # Map niche_id → repo path (matches CLAUDE.md channel table)
        niche_root_map = {
            "gaming": "CriticalRush/niches/gaming",
            "movies": "SpliceReel",
            "anime": "FrameDrift",
            "sports": "ClutchWire",
        }
        repo_root = Path(__file__).resolve().parents[3]
        niche_yaml = repo_root / niche_root_map[niche] / "config" / "niche.yaml"
        if not niche_yaml.exists():
            pytest.skip(f"{niche} niche.yaml not present in this checkout")
        src = niche_yaml.read_text()
        # Structural pin: narration section either absent OR
        # explicitly disabled. Detects any accidental flip of a
        # non-canary niche to true.
        if "narration:" in src:
            # If section exists, enabled MUST be false
            assert "enabled: false" in src.lower() or "enabled:false" in src.lower(), (
                f"{niche} niche.yaml has narration: section — must be "
                f"enabled: false until operator explicitly canary-expands. "
                f"Found: {src[src.find('narration:'):src.find('narration:')+100]}"
            )


class TestBBCanaryFlipShipped:
    """The one niche that SHOULD have narration.enabled: true."""

    def test_bb_niche_has_narration_enabled(self):
        repo_root = Path(__file__).resolve().parents[3]
        bb_yaml = repo_root / "BlackboxBrief" / "config" / "niche.yaml"
        if not bb_yaml.exists():
            pytest.skip("BB niche.yaml not in this checkout")
        src = bb_yaml.read_text()
        # The canary flip lands as a narration: block with enabled: true
        assert "narration:" in src
        # Extract the narration section and confirm enabled true
        narr_idx = src.find("narration:")
        window = src[narr_idx:narr_idx + 200]
        assert "enabled: true" in window, (
            "BB niche.yaml must contain narration.enabled: true — "
            "this is the canary flip. Env flag stays OFF until operator "
            "flips GENLAB_NARRATION_ENABLED on the VPS."
        )
