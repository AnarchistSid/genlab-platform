"""Tests for genlab_core.learning.config_writer — bandit → YAML write-back."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from genlab_core.learning.config_writer import (
    MAX_RATIO,
    MIN_OBSERVATIONS_PER_ARM,
    MIN_RATIO,
    _load_yaml,
    _save_yaml_atomic,
    update_schedule_from_bandit,
    update_templates_from_bandit,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Schedule tests
# ---------------------------------------------------------------------------


class TestUpdateScheduleFromBandit:
    """Tests for update_schedule_from_bandit."""

    def test_shifts_hours_within_bounds(self, tmp_path: Path) -> None:
        """Optimal hour is 1h away — shift is applied directly."""
        pub = tmp_path / "publishing.yaml"
        _write_yaml(pub, {
            "instagram": {
                "schedule_slots": ["12:00", "20:00"],
                "timezone": "Asia/Kolkata",
            },
        })

        result = update_schedule_from_bandit(
            pub,
            niche_id="test",
            optimal_hours={"instagram": 13},
            observation_counts={"instagram": 100},
        )

        assert result is True
        config = _load_yaml(pub)
        slots = config["instagram"]["schedule_slots"]
        assert slots[0] == "13:00"
        # Second slot: optimal=13, current=20, shift=max(-2, min(2, 13-20))=-2 → 18:00
        assert slots[1] == "18:00"

    def test_never_exceeds_max_shift(self, tmp_path: Path) -> None:
        """Optimal hour is far away — shift is capped at ±2h."""
        pub = tmp_path / "publishing.yaml"
        _write_yaml(pub, {
            "instagram": {
                "schedule_slots": ["12:00"],
                "timezone": "Asia/Kolkata",
            },
        })

        # Optimal = 6, current = 12 → raw shift = -6, capped to -2 → 10:00
        result = update_schedule_from_bandit(
            pub,
            niche_id="test",
            optimal_hours={"instagram": 6},
            observation_counts={"instagram": 100},
        )

        assert result is True
        config = _load_yaml(pub)
        assert config["instagram"]["schedule_slots"] == ["10:00"]

    def test_clamps_to_allowed_hour_range(self, tmp_path: Path) -> None:
        """Result is clamped to [06:00, 23:00] even after applying shift."""
        pub = tmp_path / "publishing.yaml"
        # Current is 06:00, optimal = 3 → raw shift = -3, capped = -2 → 4 → clamped to 6
        _write_yaml(pub, {
            "instagram": {
                "schedule_slots": ["06:00"],
                "timezone": "Asia/Kolkata",
            },
        })

        result = update_schedule_from_bandit(
            pub,
            niche_id="test",
            optimal_hours={"instagram": 3},
            observation_counts={"instagram": 100},
        )

        # 06 + max(-2, min(2, 3-6)) = 06 + (-2) = 4 → clamped to 06:00.
        # No change, so result is False.
        assert result is False
        config = _load_yaml(pub)
        assert config["instagram"]["schedule_slots"] == ["06:00"]

    def test_clamps_upper_bound(self, tmp_path: Path) -> None:
        """Shift toward late night is clamped to 23:00."""
        pub = tmp_path / "publishing.yaml"
        _write_yaml(pub, {
            "instagram": {
                "schedule_slots": ["22:00"],
                "timezone": "Asia/Kolkata",
            },
        })

        # optimal = 25 (impossible hour, but tests clamp)
        result = update_schedule_from_bandit(
            pub,
            niche_id="test",
            optimal_hours={"instagram": 25},
            observation_counts={"instagram": 100},
        )

        # 22 + 2 = 24 → clamped to 23:00
        assert result is True
        config = _load_yaml(pub)
        assert config["instagram"]["schedule_slots"] == ["23:00"]

    def test_skips_arm_below_min_observations(self, tmp_path: Path) -> None:
        """Platform with too few observations is not updated."""
        pub = tmp_path / "publishing.yaml"
        _write_yaml(pub, {
            "instagram": {
                "schedule_slots": ["12:00", "20:00"],
                "timezone": "Asia/Kolkata",
            },
        })

        result = update_schedule_from_bandit(
            pub,
            niche_id="test",
            optimal_hours={"instagram": 15},
            observation_counts={"instagram": MIN_OBSERVATIONS_PER_ARM - 1},
        )

        assert result is False
        config = _load_yaml(pub)
        # Unchanged
        assert config["instagram"]["schedule_slots"] == ["12:00", "20:00"]

    def test_no_change_when_already_optimal(self, tmp_path: Path) -> None:
        """If optimal == current, nothing changes."""
        pub = tmp_path / "publishing.yaml"
        _write_yaml(pub, {
            "instagram": {
                "schedule_slots": ["12:00"],
                "timezone": "Asia/Kolkata",
            },
        })

        result = update_schedule_from_bandit(
            pub,
            niche_id="test",
            optimal_hours={"instagram": 12},
            observation_counts={"instagram": 100},
        )

        assert result is False

    def test_multiple_platforms(self, tmp_path: Path) -> None:
        """Different platforms can be updated independently."""
        pub = tmp_path / "publishing.yaml"
        _write_yaml(pub, {
            "instagram": {
                "schedule_slots": ["12:00"],
                "timezone": "Asia/Kolkata",
            },
            "youtube": {
                "schedule_slots": ["18:00"],
                "timezone": "Asia/Kolkata",
            },
        })

        result = update_schedule_from_bandit(
            pub,
            niche_id="test",
            optimal_hours={"instagram": 14, "youtube": 20},
            observation_counts={"instagram": 100, "youtube": 100},
        )

        assert result is True
        config = _load_yaml(pub)
        assert config["instagram"]["schedule_slots"] == ["14:00"]
        assert config["youtube"]["schedule_slots"] == ["20:00"]

    def test_skips_platform_without_schedule_slots(self, tmp_path: Path) -> None:
        """Platforms like CR gaming (Postiz) with no schedule_slots are skipped."""
        pub = tmp_path / "publishing.yaml"
        _write_yaml(pub, {
            "postiz": {"enabled": True},
            "instagram": {"video_aspect_ratio": "9:16"},
        })

        result = update_schedule_from_bandit(
            pub,
            niche_id="test",
            optimal_hours={"instagram": 14},
            observation_counts={"instagram": 100},
        )

        assert result is False


# ---------------------------------------------------------------------------
# Template ratio tests
# ---------------------------------------------------------------------------


class TestUpdateTemplatesFromBandit:
    """Tests for update_templates_from_bandit."""

    def test_rebalances_ratios_when_ratio_field_exists(self, tmp_path: Path) -> None:
        """Templates with ratio fields get rebalanced toward better-performing arms."""
        tpl = tmp_path / "templates.yaml"
        _write_yaml(tpl, {
            "templates": [
                {"template_id": "TPL_A", "format": "reel", "ratio": 0.33},
                {"template_id": "TPL_B", "format": "carousel", "ratio": 0.34},
                {"template_id": "TPL_C", "format": "story", "ratio": 0.33},
            ],
        })

        # reel and carousel are strong, story is weak → 3 arms allow differentiation
        result = update_templates_from_bandit(
            tpl,
            niche_id="test",
            arm_posteriors={
                "reel": (35.0, 15.0),      # mean = 0.70
                "carousel": (30.0, 20.0),   # mean = 0.60
                "story": (10.0, 40.0),      # mean = 0.20
            },
            observation_counts={"reel": 100, "carousel": 100, "story": 100},
        )

        assert result is True
        config = _load_yaml(tpl)
        ratios = {t["format"]: t["ratio"] for t in config["templates"]}
        # reel should get the most, story the least
        assert ratios["reel"] > ratios["story"]
        assert ratios["carousel"] > ratios["story"]

    def test_skips_when_no_ratio_field(self, tmp_path: Path) -> None:
        """BB-style templates with no ratio field → skip gracefully."""
        tpl = tmp_path / "templates.yaml"
        _write_yaml(tpl, {
            "version": "3.0",
            "blueprint_limits": {"max_per_story": 1},
            "templates": [
                {"template_id": "TPL_REE_NEWS", "format": "reel", "best_for": ["news"]},
                {"template_id": "TPL_REE_CREATOR", "format": "reel", "best_for": ["creative"]},
            ],
        })

        result = update_templates_from_bandit(
            tpl,
            niche_id="bb",
            arm_posteriors={"reel": (40.0, 10.0)},
            observation_counts={"reel": 100},
        )

        assert result is False

    def test_enforces_min_max_ratio(self, tmp_path: Path) -> None:
        """Extreme posteriors are bounded by MIN_RATIO and MAX_RATIO."""
        tpl = tmp_path / "templates.yaml"
        _write_yaml(tpl, {
            "templates": [
                {"template_id": "A", "format": "reel", "ratio": 0.33},
                {"template_id": "B", "format": "carousel", "ratio": 0.33},
                {"template_id": "C", "format": "story", "ratio": 0.34},
            ],
        })

        # reel has overwhelming posterior, others are tiny
        result = update_templates_from_bandit(
            tpl,
            niche_id="test",
            arm_posteriors={
                "reel": (100.0, 1.0),       # mean ≈ 0.99
                "carousel": (1.0, 100.0),    # mean ≈ 0.01
                "story": (1.0, 100.0),       # mean ≈ 0.01
            },
            observation_counts={"reel": 200, "carousel": 200, "story": 200},
        )

        assert result is True
        config = _load_yaml(tpl)
        for t in config["templates"]:
            assert t["ratio"] >= MIN_RATIO, f"{t['format']} below min"
            assert t["ratio"] <= MAX_RATIO, f"{t['format']} above max"

    def test_skips_content_type_below_min_observations(self, tmp_path: Path) -> None:
        """Content types with insufficient data are not factored in."""
        tpl = tmp_path / "templates.yaml"
        _write_yaml(tpl, {
            "templates": [
                {"template_id": "A", "format": "reel", "ratio": 0.5},
                {"template_id": "B", "format": "carousel", "ratio": 0.5},
            ],
        })

        result = update_templates_from_bandit(
            tpl,
            niche_id="test",
            arm_posteriors={"reel": (40.0, 10.0), "carousel": (10.0, 40.0)},
            observation_counts={
                "reel": 100,
                "carousel": MIN_OBSERVATIONS_PER_ARM - 1,
            },
        )

        # Only reel is eligible — a single-arm ratio can't rebalance meaningfully
        # reel gets renormed to 1.0 (clamped to MAX_RATIO=0.5 then renormed to 1.0)
        # The carousel template's ratio key exists but 'carousel' isn't in final_ratios
        # so it won't be changed, meaning the only potential update is for reel
        config = _load_yaml(tpl)
        reel_tpl = next(t for t in config["templates"] if t["format"] == "reel")
        # With only one arm, the ratio is 1.0 → clamped to 0.5 → renormed to 1.0
        # This differs from the original 0.5 only if renorm changes it
        # Actually: single entry → 0.5/0.5 = 1.0 → clamp(1.0, 0.10, 0.50) = 0.50 → renorm 0.50/0.50 = 1.0
        # So the ratio becomes 1.0 which != 0.5 → file is updated
        assert result is True
        assert reel_tpl["ratio"] == 1.0


# ---------------------------------------------------------------------------
# Atomic write tests
# ---------------------------------------------------------------------------


class TestSaveYamlAtomic:
    """Tests for _save_yaml_atomic safety guarantees."""

    def test_leaves_original_on_crash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the write crashes mid-way, the original file is untouched."""
        target = tmp_path / "config.yaml"
        original_data = {"key": "original_value", "schedule_slots": ["12:00"]}
        _write_yaml(target, original_data)

        # Monkeypatch shutil.move to raise an error AFTER tmp file is written
        import genlab_core.learning.config_writer as cw

        real_move = cw.shutil.move

        def failing_move(src: str, dst: str) -> None:
            raise OSError("Simulated disk failure during rename")

        monkeypatch.setattr(cw.shutil, "move", failing_move)

        with pytest.raises(OSError, match="Simulated disk failure"):
            _save_yaml_atomic(target, {"key": "new_value"})

        # Original file is untouched
        result = _load_yaml(target)
        assert result == original_data

        # Tmp file may or may not exist (it was written before the crash)
        tmp_file = target.with_suffix(".yaml.tmp")
        if tmp_file.exists():
            tmp_data = _load_yaml(tmp_file)
            assert tmp_data == {"key": "new_value"}

    def test_successful_atomic_write(self, tmp_path: Path) -> None:
        """Normal write succeeds and no .tmp file remains."""
        target = tmp_path / "config.yaml"
        _write_yaml(target, {"old": True})

        _save_yaml_atomic(target, {"new": True})

        result = _load_yaml(target)
        assert result == {"new": True}
        assert not target.with_suffix(".yaml.tmp").exists()
