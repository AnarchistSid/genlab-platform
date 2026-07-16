"""Pin the transformation dimension selector (PR 4).

Covers:
  1. Gate 1 (config.enabled=False) → empty result, no proxy touch
  2. Gate 2 (env flag off) → empty result, no proxy touch
  3. Both gates on but no arms → empty result
  4. Both gates on, arms present, config declares them → one choice
     per dimension
  5. ε-greedy exploration vs Thompson exploitation (RNG-seeded)
  6. Config-drift filter — DB has arm not in current config → excluded
  7. Propensity always in [0, 1]
  8. Malformed arm_ids ignored not crash
  9. Serialization to arm_ids_by_dimension format for pending_feedback
"""

from __future__ import annotations

import random
from unittest.mock import MagicMock

import pytest
from genlab_core.media.intelligent_transform import (
    IntelligentTransformConfig,
)
from genlab_core.media.transformation_selector import (
    TransformationChoice,
    TransformationChoices,
    _parse_arm_id,
    _pick_arm,
    select_transformation_dimensions,
)


def _make_enabled_config(
    music_moods: list[str] | None = None,
    caption_styles: list[str] | None = None,
) -> IntelligentTransformConfig:
    """Build a minimal enabled config with 1-2 declared dimensions."""
    dims: dict = {}
    if music_moods is not None:
        dims["music_mood"] = {"enabled": True, "moods": music_moods}
    if caption_styles is not None:
        dims["caption_style"] = {"enabled": True, "styles": caption_styles}
    return IntelligentTransformConfig.from_visuals_dict(
        {
            "intelligent_transform": {
                "enabled": True,
                "dimensions": dims,
            }
        }
    )


def _proxy_returning_arms(
    arms: dict[str, tuple[float, float]],
    niche_id: str = "gaming",
) -> MagicMock:
    """Build a mock bandit_arms proxy that returns the given arms.

    ``niche_id`` must match the niche the selector queries — load_all_arms
    filters rows whose niche_id field differs from the query niche
    (defense-in-depth against RLS not being set).
    """
    proxy = MagicMock()
    items = [
        {
            "id": f"row_{i}",
            "fields": {
                "arm_id": arm_id,
                "alpha": alpha,
                "beta": beta,
                "niche_id": niche_id,
            },
        }
        for i, (arm_id, (alpha, beta)) in enumerate(arms.items())
    ]
    proxy.all.return_value = items
    return proxy


class TestGateFlags:
    def test_config_disabled_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        cfg = IntelligentTransformConfig.from_visuals_dict({})  # enabled=False
        proxy = _proxy_returning_arms({"transform__music_mood__cinematic": (2.0, 1.0)})
        result = select_transformation_dimensions("gaming", cfg, proxy=proxy)
        assert result.is_empty()
        # Proxy must NOT have been queried when config gate blocks.
        proxy.all.assert_not_called()

    def test_env_flag_off_returns_empty(self, monkeypatch) -> None:
        monkeypatch.delenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", raising=False)
        cfg = _make_enabled_config(music_moods=["cinematic"])
        proxy = _proxy_returning_arms({"transform__music_mood__cinematic": (2.0, 1.0)})
        result = select_transformation_dimensions("gaming", cfg, proxy=proxy)
        assert result.is_empty()
        proxy.all.assert_not_called()

    def test_env_flag_case_insensitive(self, monkeypatch) -> None:
        for value in ("1", "true", "TRUE", "yes", "on", "  YES  "):
            monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", value)
            cfg = _make_enabled_config(music_moods=["cinematic"])
            proxy = _proxy_returning_arms({"transform__music_mood__cinematic": (5.0, 1.0)})
            result = select_transformation_dimensions(
                "gaming", cfg, proxy=proxy, rng=random.Random(0)
            )
            assert not result.is_empty(), f"Env value {value!r} should enable selection"

    def test_env_flag_off_values(self, monkeypatch) -> None:
        for value in ("0", "false", "no", "off", ""):
            monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", value)
            cfg = _make_enabled_config(music_moods=["cinematic"])
            proxy = _proxy_returning_arms({"transform__music_mood__cinematic": (5.0, 1.0)})
            result = select_transformation_dimensions("gaming", cfg, proxy=proxy)
            assert result.is_empty(), f"Env value {value!r} should disable selection"


class TestSelectionCore:
    def test_returns_one_choice_per_enabled_dimension(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        cfg = _make_enabled_config(
            music_moods=["cinematic", "hype"],
            caption_styles=["word_by_word"],
        )
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__cinematic": (2.0, 1.0),
                "transform__music_mood__hype": (1.0, 2.0),
                "transform__caption_style__word_by_word": (3.0, 1.0),
            }
        )
        result = select_transformation_dimensions("gaming", cfg, proxy=proxy, rng=random.Random(42))
        assert set(result.choices.keys()) == {"music_mood", "caption_style"}
        for dim, choice in result.choices.items():
            assert choice.dimension == dim
            assert choice.arm_id.startswith(f"transform__{dim}__")

    def test_no_arms_registered_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        cfg = _make_enabled_config(music_moods=["cinematic"])
        proxy = _proxy_returning_arms({})  # No arms at all
        result = select_transformation_dimensions("gaming", cfg, proxy=proxy)
        assert result.is_empty()

    def test_arms_but_no_matching_dim_config(self, monkeypatch) -> None:
        """Arms exist in DB but the corresponding dim is disabled in config."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        # music_mood arms exist in DB, but config only enables caption_style
        cfg = _make_enabled_config(caption_styles=["word_by_word"])
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__cinematic": (5.0, 1.0),
                "transform__caption_style__word_by_word": (3.0, 1.0),
            }
        )
        result = select_transformation_dimensions("gaming", cfg, proxy=proxy, rng=random.Random(0))
        assert "music_mood" not in result.choices
        assert "caption_style" in result.choices


class TestConfigDriftFilter:
    def test_db_arms_not_in_config_excluded(self, monkeypatch) -> None:
        """DB has extra arm ('legacy_mood') but config only declares
        'cinematic'. The legacy_mood arm should be filtered out."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        cfg = _make_enabled_config(music_moods=["cinematic"])
        proxy = _proxy_returning_arms(
            {
                "transform__music_mood__cinematic": (2.0, 1.0),
                "transform__music_mood__legacy_mood": (100.0, 1.0),
            }
        )
        # Use seeded RNG so exploration doesn't randomly pick the
        # legacy arm — force exploitation path.
        result = select_transformation_dimensions("gaming", cfg, proxy=proxy, rng=random.Random(1))
        assert "music_mood" in result.choices
        # Even though legacy_mood has α=100 (much higher), config-drift
        # filter excludes it. Only cinematic can win.
        assert result.choices["music_mood"].dimension_value == "cinematic"


class TestExplorationVsExploitation:
    def test_low_epsilon_seed_lands_on_exploit(self) -> None:
        """RNG that yields random() > EPSILON should trigger the
        Thompson path. Since epsilon=0.15 default, seeding to make
        the first random() call return a value above 0.15 exploits."""
        candidates = [
            ("transform__music_mood__a", "a", 10.0, 1.0),
            ("transform__music_mood__b", "b", 1.0, 10.0),
        ]
        # Verify the first random() > EPSILON on this seed
        # (implementation-detail check — just ensuring test setup is real)
        r = random.Random(0)
        assert r.random() > 0.15
        # The high-alpha arm ('a') should dominate under Thompson.
        # Run 100 times and expect majority a.
        wins_a = 0
        for _ in range(100):
            choice = _pick_arm("music_mood", candidates, rng=random.Random(_ * 7 + 1))
            if choice.dimension_value == "a":
                wins_a += 1
        # Expect a to win at least 70/100 (strong Thompson signal).
        assert wins_a >= 70, (
            f"Arm 'a' with α=10, β=1 won only {wins_a}/100 vs β=10 α=1 — "
            "Thompson selection may be broken."
        )

    def test_high_epsilon_seed_lands_on_explore(self) -> None:
        """Seed the RNG so random() < EPSILON → uniform exploration."""

        class ForceExploreRng:
            def random(self):
                return 0.01  # Below EPSILON=0.15

            def choice(self, seq):
                return seq[0]  # Deterministic pick

            def betavariate(self, a, b):
                return 0.5  # Unused in explore path

        candidates = [
            ("transform__music_mood__a", "a", 10.0, 1.0),
            ("transform__music_mood__b", "b", 1.0, 10.0),
        ]
        choice = _pick_arm("music_mood", candidates, rng=ForceExploreRng())
        # Explore chose seq[0] which is arm 'a'.
        assert choice.dimension_value == "a"
        # Explore propensity is EPSILON/n
        assert choice.propensity == pytest.approx(0.15 / 2)


class TestPropensityBounds:
    def test_propensity_in_unit_interval(self) -> None:
        candidates = [(f"transform__music_mood__{i}", str(i), 1.0 + i, 1.0) for i in range(5)]
        for seed in range(20):
            choice = _pick_arm("music_mood", candidates, rng=random.Random(seed))
            assert 0.0 <= choice.propensity <= 1.0

    def test_propensity_nonzero(self) -> None:
        """Every choice must have positive propensity — required for
        IPS denominator not to explode."""
        candidates = [
            ("transform__music_mood__a", "a", 1.0, 1.0),
        ]
        for seed in range(20):
            choice = _pick_arm("music_mood", candidates, rng=random.Random(seed))
            assert choice.propensity > 0.0


class TestParseArmId:
    def test_valid_composite_parses(self) -> None:
        assert _parse_arm_id("transform__music_mood__cinematic") == (
            "music_mood",
            "cinematic",
        )

    def test_negative_int_value_preserved(self) -> None:
        assert _parse_arm_id("transform__audio_ducking__-12") == (
            "audio_ducking",
            "-12",
        )

    def test_non_transform_prefix_returns_none(self) -> None:
        assert _parse_arm_id("style:gaming:revelation") is None
        assert _parse_arm_id("hour:14:youtube:sports") is None

    def test_malformed_no_value_returns_none(self) -> None:
        # Only 1 __ separator after prefix → no dim + value
        assert _parse_arm_id("transform__nodim") is None


class TestArmIdsByDimensionSerialization:
    def test_empty_choices_gives_empty_dict(self) -> None:
        choices = TransformationChoices(niche_id="gaming")
        assert choices.to_arm_ids_by_dimension() == {}

    def test_choices_serialize_to_dim_arm_map(self) -> None:
        choices = TransformationChoices(niche_id="gaming")
        choices.choices["music_mood"] = TransformationChoice(
            dimension="music_mood",
            dimension_value="hype",
            arm_id="transform__music_mood__hype",
            propensity=0.5,
        )
        choices.choices["caption_style"] = TransformationChoice(
            dimension="caption_style",
            dimension_value="minimal",
            arm_id="transform__caption_style__minimal",
            propensity=0.4,
        )
        payload = choices.to_arm_ids_by_dimension()
        assert payload == {
            "music_mood": "transform__music_mood__hype",
            "caption_style": "transform__caption_style__minimal",
        }


class TestMalformedArmsIgnored:
    def test_non_composite_arm_id_skipped(self, monkeypatch) -> None:
        """DB might contain arm_ids like 'transform__weird' with only
        one __ segment. Selector must skip not crash."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        cfg = _make_enabled_config(music_moods=["cinematic"])
        proxy = _proxy_returning_arms(
            {
                "transform__weird": (5.0, 1.0),  # Malformed
                "transform__music_mood__cinematic": (2.0, 1.0),  # Valid
            }
        )
        # Should not crash; should still pick cinematic.
        result = select_transformation_dimensions("gaming", cfg, proxy=proxy, rng=random.Random(0))
        assert result.choices["music_mood"].dimension_value == "cinematic"


class TestProxyFailure:
    def test_proxy_load_exception_returns_empty(self, monkeypatch) -> None:
        """load_all_arms crashes → selector logs warning + returns empty
        rather than crashing the pipeline."""
        monkeypatch.setenv("GENLAB_INTELLIGENT_TRANSFORM_ENABLED", "1")
        cfg = _make_enabled_config(music_moods=["cinematic"])
        proxy = MagicMock()
        proxy.all.side_effect = RuntimeError("DB unavailable")
        result = select_transformation_dimensions("gaming", cfg, proxy=proxy)
        assert result.is_empty()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
