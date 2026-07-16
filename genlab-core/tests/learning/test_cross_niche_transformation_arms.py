"""Pin the cross-niche transformation-arm extension (PR 13).

Covers:
  1. extract_transformation_suffix parses the composite key correctly
  2. Non-transformation arm_ids return None
  3. extract_prior_key routes style / transformation / other
  4. compute_transferred_priors picks up transformation arms alongside
     style arms
  5. get_transferred_prior resolves transformation arms via the
     unified extractor
  6. Existing style-arm behavior unchanged (regression pin)
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from genlab_core.learning.cross_niche_transfer import (
    TransferredPrior,
    compute_transferred_priors,
    extract_prior_key,
    extract_style_suffix,
    extract_transformation_suffix,
    get_transferred_prior,
)


class TestExtractTransformationSuffix:
    def test_basic_composite_key(self) -> None:
        assert (
            extract_transformation_suffix("transform__music_mood__cinematic")
            == "music_mood__cinematic"
        )

    def test_negative_int_value_preserved(self) -> None:
        """Integer arm values (audio_ducking dB, caption_pacing ms)
        stringify with signs and dashes — must survive extraction."""
        assert (
            extract_transformation_suffix("transform__audio_ducking__-12") == "audio_ducking__-12"
        )
        assert (
            extract_transformation_suffix("transform__caption_pacing__750") == "caption_pacing__750"
        )

    def test_style_arm_returns_none(self) -> None:
        assert extract_transformation_suffix("style:gaming:revelation") is None

    def test_source_arm_returns_none(self) -> None:
        assert extract_transformation_suffix("source:youtube_trending__facebook") is None

    def test_hour_arm_returns_none(self) -> None:
        assert extract_transformation_suffix("hour:14:youtube:sports") is None

    def test_content_type_arm_returns_none(self) -> None:
        """Legacy content_type arms don't start with 'transform__'."""
        assert extract_transformation_suffix("gameplay_clip") is None
        assert extract_transformation_suffix("gameplay_clip__instagram") is None

    def test_missing_value_returns_none(self) -> None:
        """'transform__music_mood' with no value → None."""
        assert extract_transformation_suffix("transform__music_mood") is None

    def test_empty_dimension_returns_none(self) -> None:
        assert extract_transformation_suffix("transform____cinematic") is None

    def test_empty_value_returns_none(self) -> None:
        assert extract_transformation_suffix("transform__music_mood__") is None


class TestExtractPriorKey:
    def test_routes_style_arm(self) -> None:
        """extract_prior_key delegates to style extractor for style: arms."""
        assert extract_prior_key("style:gaming:revelation") == "revelation"

    def test_routes_transformation_arm(self) -> None:
        assert extract_prior_key("transform__music_mood__cinematic") == "music_mood__cinematic"

    def test_unknown_class_returns_none(self) -> None:
        assert extract_prior_key("hour:14:youtube:sports") is None
        assert extract_prior_key("source:youtube_trending") is None
        assert extract_prior_key("gameplay_clip") is None


class TestComputeTransferredPriorsIncludesTransformationArms:
    """Test data notes:

    The moment-matching guardrails require:
      * effective_n = max_var / variance - 1 within (2, 20)
      * Both niches with ≥ _MIN_OBS_PER_NICHE (5) observations
      * Cross-niche rate variance non-zero (identical rates give
        variance=0 which fails moment-matching)

    Rates ~0.4 and ~0.7 with 5-10 obs each land in the sweet spot.
    """

    def test_transformation_arm_gets_prior_when_2_niches_have_obs(self) -> None:
        state = {
            # α + β - 2 = 6 observations, rate = 0.375
            "movies": {"transform__music_mood__cinematic": (3.0, 5.0)},
            # α + β - 2 = 7 observations, rate ≈ 0.667
            "gaming": {"transform__music_mood__cinematic": (6.0, 3.0)},
        }
        priors = compute_transferred_priors(state)
        assert "music_mood__cinematic" in priors
        assert priors["music_mood__cinematic"].n_niches == 2

    def test_style_arm_still_works(self) -> None:
        """Regression pin — legacy style-arm transfer unchanged."""
        state = {
            "movies": {"style:movies:revelation": (3.0, 5.0)},
            "gaming": {"style:gaming:revelation": (6.0, 3.0)},
        }
        priors = compute_transferred_priors(state)
        assert "revelation" in priors
        # And no transformation-key pollution
        assert not any("__" in k for k in priors)

    def test_mixed_style_and_transformation_arms(self) -> None:
        """Both arm classes coexist in one refit."""
        state = {
            "movies": {
                "style:movies:revelation": (3.0, 5.0),
                "transform__music_mood__cinematic": (3.0, 5.0),
            },
            "gaming": {
                "style:gaming:revelation": (6.0, 3.0),
                "transform__music_mood__cinematic": (6.0, 3.0),
            },
        }
        priors = compute_transferred_priors(state)
        assert "revelation" in priors
        assert "music_mood__cinematic" in priors

    def test_single_niche_transformation_arm_skipped(self) -> None:
        """Only one niche has data on this key → no prior emitted
        (matches _MIN_NICHES_FOR_TRANSFER=2 guardrail)."""
        state = {
            "movies": {
                "transform__music_mood__cinematic": (10.0, 5.0),
            },
        }
        priors = compute_transferred_priors(state)
        assert "music_mood__cinematic" not in priors


class TestGetTransferredPriorForTransformationArms:
    def test_returns_prior_when_persisted(self, tmp_path, monkeypatch) -> None:
        """When integration is enabled AND a prior exists for the
        transformation key, get_transferred_prior returns it."""
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")

        fake_priors = {
            "music_mood__cinematic": TransferredPrior(
                style="music_mood__cinematic",
                alpha=5.0,
                beta=3.0,
                observed_rate_mean=0.625,
                observed_rate_variance=0.01,
                n_niches=3,
            ),
        }
        with patch(
            "genlab_core.learning.cross_niche_transfer.load_persisted_priors",
            return_value=fake_priors,
        ):
            result = get_transferred_prior("sports", "transform__music_mood__cinematic")
        assert result == (5.0, 3.0)

    def test_returns_none_when_no_persisted_prior(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        with patch(
            "genlab_core.learning.cross_niche_transfer.load_persisted_priors",
            return_value={},
        ):
            result = get_transferred_prior("sports", "transform__music_mood__cinematic")
        assert result is None

    def test_returns_none_when_flag_off(self, monkeypatch) -> None:
        monkeypatch.delenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", raising=False)
        result = get_transferred_prior("sports", "transform__music_mood__cinematic")
        assert result is None

    def test_returns_none_for_non_extractable(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")
        result = get_transferred_prior("sports", "hour:14:youtube:sports")
        assert result is None


class TestRegressionExistingStyleBehavior:
    """Pin that PR 13 doesn't regress legacy style-arm transfer."""

    def test_style_extractor_unchanged(self) -> None:
        assert extract_style_suffix("style:gaming:revelation__youtube") == "revelation"

    def test_style_prior_still_returned(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "true")

        fake_priors = {
            "revelation": TransferredPrior(
                style="revelation",
                alpha=6.0,
                beta=4.0,
                observed_rate_mean=0.6,
                observed_rate_variance=0.05,
                n_niches=2,
            ),
        }
        with patch(
            "genlab_core.learning.cross_niche_transfer.load_persisted_priors",
            return_value=fake_priors,
        ):
            result = get_transferred_prior("sports", "style:sports:revelation")
        assert result == (6.0, 4.0)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
