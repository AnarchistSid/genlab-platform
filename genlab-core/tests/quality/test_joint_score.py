"""Pin Phase 4.A session 3 joint quality score fusion:

  * _weighted_geometric_mean: empty → None; single value → itself;
    missing subset renormalizes weights; zero-clip protects against
    -inf log
  * Missing sub-scores excluded, not treated as 0
  * All-missing → None modality → None joint
  * Fusion weights sum to 1 per modality
  * Video hash is deterministic + changes with size
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from genlab_core.quality.joint_score import (
    JointQualityScore,
    _AUDIO_JOINT_WEIGHT,
    _AUDIO_WEIGHTS,
    _VISUAL_JOINT_WEIGHT,
    _VISUAL_WEIGHTS,
    _combine_joint,
    _hash_video,
    _weighted_geometric_mean,
    compute_joint_score,
)


class TestWeights:
    def test_visual_weights_sum_to_1(self):
        assert abs(sum(_VISUAL_WEIGHTS.values()) - 1.0) < 1e-9

    def test_audio_weights_sum_to_1(self):
        assert abs(sum(_AUDIO_WEIGHTS.values()) - 1.0) < 1e-9

    def test_joint_weights_sum_to_1(self):
        assert abs(_VISUAL_JOINT_WEIGHT + _AUDIO_JOINT_WEIGHT - 1.0) < 1e-9


class TestWeightedGeometricMean:
    def test_all_none_returns_none(self):
        scores = {"a": None, "b": None}
        weights = {"a": 0.5, "b": 0.5}
        assert _weighted_geometric_mean(scores, weights) is None

    def test_all_equal_returns_that_value(self):
        scores = {"a": 0.5, "b": 0.5, "c": 0.5}
        weights = {"a": 0.4, "b": 0.3, "c": 0.3}
        result = _weighted_geometric_mean(scores, weights)
        assert abs(result - 0.5) < 1e-6

    def test_single_value_returns_that_value(self):
        """When only one sub-score is present, geometric mean = that value."""
        scores = {"a": 0.8, "b": None, "c": None}
        weights = {"a": 0.4, "b": 0.3, "c": 0.3}
        result = _weighted_geometric_mean(scores, weights)
        assert abs(result - 0.8) < 1e-6

    def test_zero_dimension_collapses_result(self):
        """One collapsed sub-score should drag the mean down significantly —
        this is the ANTI-arithmetic-mean property that motivated using
        geometric mean in the first place."""
        scores = {"a": 0.9, "b": 0.9, "c": 0.0}
        weights = {"a": 0.4, "b": 0.3, "c": 0.3}
        result = _weighted_geometric_mean(scores, weights)
        # Arithmetic mean would be 0.36 + 0.27 + 0 = 0.63
        # Geometric mean with zero-clip: exp(0.4*ln(.9) + 0.3*ln(.9) + 0.3*ln(1e-6))
        # ≈ exp(-0.0738 + -0.0316 + -4.14) ≈ 0.014
        assert result < 0.05  # Much lower than arithmetic mean

    def test_missing_subset_renormalizes_weights(self):
        """When 'a' is missing, remaining weights {b: 0.3, c: 0.3}
        renormalize to {b: 0.5, c: 0.5} so the fusion doesn't
        systematically pull toward zero."""
        scores = {"a": None, "b": 0.6, "c": 0.6}
        weights = {"a": 0.4, "b": 0.3, "c": 0.3}
        result = _weighted_geometric_mean(scores, weights)
        # With renormalized 50/50 weights: geometric mean of 0.6, 0.6 = 0.6
        assert abs(result - 0.6) < 1e-6

    def test_empty_scores_dict_returns_none(self):
        assert _weighted_geometric_mean({}, {}) is None


class TestCombineJoint:
    def test_both_present(self):
        # 60/40 weighted geometric mean of visual=0.5, audio=0.5 = 0.5
        result = _combine_joint(0.5, 0.5)
        assert abs(result - 0.5) < 1e-6

    def test_visual_only(self):
        """Missing audio → joint == visual (weight renormalized)."""
        result = _combine_joint(0.7, None)
        assert abs(result - 0.7) < 1e-6

    def test_audio_only(self):
        result = _combine_joint(None, 0.4)
        assert abs(result - 0.4) < 1e-6

    def test_both_none_returns_none(self):
        assert _combine_joint(None, None) is None

    def test_visual_weighted_higher_than_audio(self):
        """Video-first: visual should pull the joint more than audio.
        Pin: joint(1.0, 0.0) > joint(0.0, 1.0) since visual weight is higher."""
        vis_high = _combine_joint(1.0, 0.0)
        aud_high = _combine_joint(0.0, 1.0)
        # geometric mean with clip: both are dragged toward 0 by the 0,
        # but the LARGER weight on the 0 pulls harder. Since visual
        # weight is 0.6:
        #   vis_high: 1.0^0.6 * 0.0^0.4 (audio drags harder proportionally)
        #   aud_high: 0.0^0.6 * 1.0^0.4 (visual drags harder proportionally)
        # aud_high should be LOWER because visual weight is higher.
        assert vis_high > aud_high


class TestVideoHash:
    def test_missing_file_returns_sentinel(self, tmp_path):
        p = tmp_path / "nope.mp4"
        assert _hash_video(p) == "unhashable-0"

    def test_hash_deterministic_for_same_file(self, tmp_path):
        p = tmp_path / "clip.bin"
        p.write_bytes(b"hello world" * 100)
        assert _hash_video(p) == _hash_video(p)

    def test_hash_changes_with_content(self, tmp_path):
        p1 = tmp_path / "a.bin"
        p1.write_bytes(b"hello world" * 100)
        p2 = tmp_path / "b.bin"
        p2.write_bytes(b"different!!" * 100)
        assert _hash_video(p1) != _hash_video(p2)

    def test_hash_includes_size(self, tmp_path):
        """Same head-chunk bytes but different total size → different
        hash. Guards against a truncated re-render matching the
        head of the original."""
        p1 = tmp_path / "a.bin"
        p1.write_bytes(b"a" * 65536)  # exactly one chunk
        p2 = tmp_path / "b.bin"
        p2.write_bytes(b"a" * 100000)  # same head, more content
        h1 = _hash_video(p1)
        h2 = _hash_video(p2)
        assert h1 != h2
        assert h1.endswith("-65536")
        assert h2.endswith("-100000")


class TestComputeJointScore:
    """End-to-end mock: patch every extractor to return canned
    FeatureResult, then verify the fusion + persist shape."""

    def _mock_result(self, ok: bool, score: float | None = None):
        from genlab_core.quality.visual_features import FeatureResult as VFR
        return VFR(ok=ok, score=score, raw=None, reason="mocked")

    def test_all_extractors_succeed(self, tmp_path):
        """All 7 extractors succeed with 0.5 → joint should be near 0.5."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake mp4")

        with patch(
            "genlab_core.quality.visual_features.extract_color_palette_dominance",
            return_value=self._mock_result(ok=True, score=0.5),
        ), patch(
            "genlab_core.quality.visual_features.extract_motion_energy",
            return_value=self._mock_result(ok=True, score=0.5),
        ), patch(
            "genlab_core.quality.visual_features.extract_cut_frequency",
            return_value=self._mock_result(ok=True, score=0.5),
        ), patch(
            "genlab_core.quality.visual_features.extract_brand_consistency",
            return_value=self._mock_result(ok=True, score=0.5),
        ), patch(
            "genlab_core.quality.audio_features.extract_audio_energy_variance",
            return_value=self._mock_result(ok=True, score=0.5),
        ), patch(
            "genlab_core.quality.audio_features.extract_dialogue_density",
            return_value=self._mock_result(ok=True, score=0.5),
        ), patch(
            "genlab_core.quality.audio_features.extract_music_to_voice_ratio",
            return_value=self._mock_result(ok=True, score=0.5),
        ):
            score = compute_joint_score(video, "#FF0000")

        assert isinstance(score, JointQualityScore)
        assert score.joint_score is not None
        assert abs(score.joint_score - 0.5) < 1e-6
        assert score.failed_extractors == ()

    def test_all_extractors_fail(self, tmp_path):
        """When every extractor fails, joint is None + all 7 in failed
        list. Runner will persist NULL joint; reward multiplier
        treats NULL as 1.0."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake mp4")

        with patch(
            "genlab_core.quality.visual_features.extract_color_palette_dominance",
            return_value=self._mock_result(ok=False),
        ), patch(
            "genlab_core.quality.visual_features.extract_motion_energy",
            return_value=self._mock_result(ok=False),
        ), patch(
            "genlab_core.quality.visual_features.extract_cut_frequency",
            return_value=self._mock_result(ok=False),
        ), patch(
            "genlab_core.quality.visual_features.extract_brand_consistency",
            return_value=self._mock_result(ok=False),
        ), patch(
            "genlab_core.quality.audio_features.extract_audio_energy_variance",
            return_value=self._mock_result(ok=False),
        ), patch(
            "genlab_core.quality.audio_features.extract_dialogue_density",
            return_value=self._mock_result(ok=False),
        ), patch(
            "genlab_core.quality.audio_features.extract_music_to_voice_ratio",
            return_value=self._mock_result(ok=False),
        ):
            score = compute_joint_score(video, "#FF0000")

        assert score.joint_score is None
        assert score.visual_score is None
        assert score.audio_score is None
        assert len(score.failed_extractors) == 7

    def test_partial_failure_still_produces_score(self, tmp_path):
        """When cut_frequency + brand_consistency fail but others
        succeed, visual score still computes from the 2 present ones.
        Ensures a single flaky extractor doesn't null out the whole
        signal."""
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"fake mp4")

        with patch(
            "genlab_core.quality.visual_features.extract_color_palette_dominance",
            return_value=self._mock_result(ok=True, score=0.6),
        ), patch(
            "genlab_core.quality.visual_features.extract_motion_energy",
            return_value=self._mock_result(ok=True, score=0.6),
        ), patch(
            "genlab_core.quality.visual_features.extract_cut_frequency",
            return_value=self._mock_result(ok=False),
        ), patch(
            "genlab_core.quality.visual_features.extract_brand_consistency",
            return_value=self._mock_result(ok=False),
        ), patch(
            "genlab_core.quality.audio_features.extract_audio_energy_variance",
            return_value=self._mock_result(ok=True, score=0.7),
        ), patch(
            "genlab_core.quality.audio_features.extract_dialogue_density",
            return_value=self._mock_result(ok=True, score=0.7),
        ), patch(
            "genlab_core.quality.audio_features.extract_music_to_voice_ratio",
            return_value=self._mock_result(ok=True, score=0.7),
        ):
            score = compute_joint_score(video, "#FF0000")

        assert score.visual_score is not None
        assert score.audio_score is not None
        assert score.joint_score is not None
        assert score.cut_frequency is None
        assert score.brand_consistency is None
        assert "cut_frequency" in score.failed_extractors
        assert "brand_consistency" in score.failed_extractors
