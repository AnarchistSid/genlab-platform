"""Tests for genlab_core.learning.platform_reward_multipliers (D3.8)."""

from __future__ import annotations

import math
import textwrap
from pathlib import Path

import pytest
from genlab_core.learning import platform_reward_multipliers as prm


@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the module's process-level cache between tests so each
    test sees a fresh load of whatever YAML it stages."""
    prm.clear_cache()
    yield
    prm.clear_cache()


def _write_config(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "platform_reward_multipliers.yaml"
    cfg.write_text(textwrap.dedent(body))
    return cfg


class TestLoadMultipliers:
    def test_missing_config_returns_empty(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        assert prm.load_multipliers(missing) == {}

    def test_canonical_yaml_loads(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            """
            version: "1.0"
            multipliers:
              youtube: 1.5
              tiktok: 1.2
              instagram: 1.0
              facebook: 0.8
              twitter: 0.6
            """,
        )
        out = prm.load_multipliers(cfg)
        assert out["youtube"] == 1.5
        assert out["tiktok"] == 1.2
        assert out["instagram"] == 1.0
        assert out["facebook"] == 0.8
        assert out["twitter"] == 0.6

    def test_keys_lowercased(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            """
            multipliers:
              YouTube: 1.7
            """,
        )
        out = prm.load_multipliers(cfg)
        assert "youtube" in out
        assert "YouTube" not in out

    def test_non_numeric_values_skipped(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            """
            multipliers:
              youtube: 1.5
              tiktok: "high"
            """,
        )
        out = prm.load_multipliers(cfg)
        assert out["youtube"] == 1.5
        assert "tiktok" not in out  # rejected, falls back to 1.0 via get_multiplier

    def test_malformed_yaml_returns_empty(self, tmp_path):
        cfg = tmp_path / "bad.yaml"
        cfg.write_text("not: [valid: yaml")
        # Must not raise
        out = prm.load_multipliers(cfg)
        assert out == {}

    def test_clamped_to_safe_bounds(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            """
            multipliers:
              youtube: 99.0   # too high → clamped down
              tiktok: -5.0    # negative → clamped up
              twitter: 0.05   # below floor → clamped up
            """,
        )
        out = prm.load_multipliers(cfg)
        assert out["youtube"] == prm._MAX_MULTIPLIER
        assert out["tiktok"] == prm._MIN_MULTIPLIER
        assert out["twitter"] == prm._MIN_MULTIPLIER

    def test_infinite_value_becomes_default(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            """
            multipliers:
              youtube: .inf
            """,
        )
        out = prm.load_multipliers(cfg)
        assert out["youtube"] == prm._DEFAULT_MULTIPLIER

    def test_no_multipliers_block_returns_empty(self, tmp_path):
        cfg = _write_config(
            tmp_path,
            """
            version: "1.0"
            # No multipliers key at all
            """,
        )
        out = prm.load_multipliers(cfg)
        assert out == {}


class TestGetMultiplier:
    def test_default_when_platform_unknown(self, monkeypatch, tmp_path):
        # Point _CACHE to empty dict
        cfg = _write_config(tmp_path, "multipliers:\n  youtube: 1.5\n")
        monkeypatch.setattr(prm, "_config_path", lambda: cfg)
        prm.clear_cache()
        assert prm.get_multiplier("youtube") == 1.5
        assert prm.get_multiplier("instagram") == 1.0  # not listed
        assert prm.get_multiplier("nonsense_platform") == 1.0

    def test_empty_platform_returns_default(self):
        assert prm.get_multiplier("") == 1.0
        # cast still default
        assert prm.get_multiplier("YOUTUBE") == prm.get_multiplier("youtube")

    def test_cache_avoids_repeated_disk_reads(self, monkeypatch, tmp_path):
        cfg = _write_config(tmp_path, "multipliers:\n  youtube: 1.5\n")
        monkeypatch.setattr(prm, "_config_path", lambda: cfg)
        prm.clear_cache()

        # Use a counter wrapper to count load_multipliers calls
        call_count = {"n": 0}
        original_load = prm.load_multipliers

        def counting_load(path=None):
            call_count["n"] += 1
            return original_load(path)

        monkeypatch.setattr(prm, "load_multipliers", counting_load)

        prm.get_multiplier("youtube")
        prm.get_multiplier("youtube")
        prm.get_multiplier("instagram")

        # First call populates cache; subsequent calls hit cache only.
        assert call_count["n"] == 1

    def test_canonical_yaml_loads_via_get_multiplier(self):
        """The canonical config file (shipped in genlab-core/config/)
        must load and contain the documented platforms."""
        prm.clear_cache()
        assert prm.get_multiplier("youtube") > 1.0  # documented as 1.5
        assert prm.get_multiplier("twitter") < 1.0  # documented as 0.6


class TestSafetyInvariant:
    """Pin the invariant: get_multiplier must NEVER return a value
    that could brick the bandit (negative, infinity, NaN)."""

    @pytest.mark.parametrize(
        "platform",
        ["youtube", "instagram", "facebook", "tiktok", "twitter", "threads", "unknown"],
    )
    def test_always_returns_finite_positive(self, platform):
        prm.clear_cache()
        mult = prm.get_multiplier(platform)
        assert math.isfinite(mult)
        assert mult > 0
        assert mult <= prm._MAX_MULTIPLIER

    def test_default_multiplier_is_one(self):
        """The default must be exactly 1.0 — anything else changes
        behaviour for platforms not in the YAML."""
        assert prm._DEFAULT_MULTIPLIER == 1.0
