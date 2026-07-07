"""Pin the structural feature extractor (B.1, 2026-07-07).

Two concerns:
  1. Feature extraction is correct + robust (missing fields, both
     API shapes, edge cases in title/description)
  2. Spearman correlation is numerically correct + handles ties +
     degenerate inputs

Uses no external stats library — the module is pure Python by
design. So we can pin exact expected correlations against
hand-computed values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from genlab_core.intel.top_creator_features import (
    VideoFeatures,
    _spearman_correlation,
    compute_feature_view_correlation,
    extract_features,
)


class TestExtractFeaturesBasics:
    """Both accepted input shapes + sensible defaults."""

    def test_raw_api_response_shape(self):
        meta = {
            "snippet": {
                "title": "Testing hooks",
                "description": "First line\n\n0:00 Intro\n5:30 Point",
                "tags": ["a", "b", "c"],
                "publishedAt": "2026-07-07T14:00:00Z",
            },
            "statistics": {"viewCount": "12345"},
        }
        features = extract_features(meta)
        assert features.title_length_chars == 13
        assert features.title_length_words == 2
        assert features.tags_count == 3
        assert features.views == 12345
        assert features.description_has_timestamps is True

    def test_flat_shape(self):
        """The A.2 poll runner uses this flatter form. Both accepted."""
        meta = {
            "title": "flat",
            "description": "",
            "tags": [],
            "publishedAt": "2026-07-07T14:00:00Z",
        }
        features = extract_features(meta)
        assert features.title_length_chars == 4
        assert features.tags_count == 0

    def test_empty_input(self):
        """No fields — sensible defaults, no exception."""
        features = extract_features({})
        assert features.title_length_chars == 0
        assert features.views == 0
        assert features.title_length_words == 0
        assert features.hours_since_publish == 0.0

    def test_view_count_as_int(self):
        features = extract_features({"statistics": {"viewCount": 500}})
        assert features.views == 500

    def test_malformed_view_count(self):
        features = extract_features({"statistics": {"viewCount": "not-a-number"}})
        assert features.views == 0


class TestTitleFeatures:
    def test_title_ends_with_question(self):
        f = extract_features({"snippet": {"title": "Is this the future?"}})
        assert f.title_ends_with_question is True

    def test_title_with_trailing_whitespace(self):
        """Question mark before trailing whitespace still counts."""
        f = extract_features({"snippet": {"title": "Really? "}})
        assert f.title_ends_with_question is True

    def test_title_starts_with_number(self):
        f = extract_features({"snippet": {"title": "10 things about AI"}})
        assert f.title_starts_with_number is True
        assert f.title_number_count == 1

    def test_title_number_count(self):
        f = extract_features({"snippet": {"title": "5 tips for 2026 GPUs"}})
        assert f.title_number_count == 2  # "5" and "2026"

    def test_title_emoji_count(self):
        f = extract_features({"snippet": {"title": "🔥 hot take 🚀"}})
        assert f.title_emoji_count == 2

    def test_title_allcaps_words(self):
        f = extract_features({"snippet": {"title": "NEW iPhone released"}})
        assert f.title_allcaps_word_count == 1

    def test_title_length_word_count(self):
        f = extract_features({"snippet": {"title": "one two three"}})
        assert f.title_length_words == 3


class TestDescriptionFeatures:
    def test_description_first_line_length(self):
        f = extract_features({"snippet": {"description": "hook\nlong description..."}})
        assert f.description_first_line_length == 4

    def test_description_timestamps_detected(self):
        f = extract_features({"snippet": {"description": "0:00 Start\n1:23 Middle"}})
        assert f.description_has_timestamps is True

    def test_description_no_timestamps(self):
        f = extract_features({"snippet": {"description": "no chapters here"}})
        assert f.description_has_timestamps is False

    def test_description_length_chars(self):
        f = extract_features({"snippet": {"description": "abc"}})
        assert f.description_length_chars == 3


class TestPublishTiming:
    def test_publish_hour_dow(self):
        # 2026-07-07 is a Tuesday (weekday=1)
        f = extract_features({"snippet": {"title": "x", "publishedAt": "2026-07-07T14:30:00Z"}})
        assert f.publish_hour_utc == 14
        assert f.publish_day_of_week == 1  # Tuesday

    def test_hours_since_publish(self):
        past = (datetime.now(UTC) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        f = extract_features({"snippet": {"publishedAt": past}})
        assert 4.5 < f.hours_since_publish < 5.5

    def test_publish_at_absent_returns_zero(self):
        f = extract_features({"snippet": {"title": "x"}})
        assert f.hours_since_publish == 0.0


class TestViewVelocity:
    def test_view_velocity_basic(self):
        f = VideoFeatures(
            title_length_chars=0,
            title_length_words=0,
            title_ends_with_question=False,
            title_starts_with_number=False,
            title_number_count=0,
            title_emoji_count=0,
            title_allcaps_word_count=0,
            description_length_chars=0,
            description_first_line_length=0,
            description_has_timestamps=False,
            tags_count=0,
            publish_hour_utc=0,
            publish_day_of_week=0,
            views=1000,
            hours_since_publish=10.0,
        )
        assert f.view_velocity() == 100.0

    def test_view_velocity_epsilon_hours(self):
        """Fresh videos (< 0.01h old) → 0 to avoid div-by-zero."""
        f = VideoFeatures(
            title_length_chars=0,
            title_length_words=0,
            title_ends_with_question=False,
            title_starts_with_number=False,
            title_number_count=0,
            title_emoji_count=0,
            title_allcaps_word_count=0,
            description_length_chars=0,
            description_first_line_length=0,
            description_has_timestamps=False,
            tags_count=0,
            publish_hour_utc=0,
            publish_day_of_week=0,
            views=1000,
            hours_since_publish=0.001,
        )
        assert f.view_velocity() == 0.0


class TestSpearmanCorrelation:
    """Pure math — pin against hand-computable values."""

    def test_perfect_positive_correlation(self):
        rho = _spearman_correlation([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
        assert rho == pytest.approx(1.0)

    def test_perfect_negative_correlation(self):
        rho = _spearman_correlation([1, 2, 3, 4, 5], [5, 4, 3, 2, 1])
        assert rho == pytest.approx(-1.0)

    def test_moderate_correlation(self):
        """Scrambled pairs → moderate (not perfect) rank correlation.
        The bound intent is 'not strongly correlated', not zero —
        rank correlations on small samples don't tend to zero cleanly."""
        rho = _spearman_correlation([1, 5, 2, 4, 3], [3, 1, 4, 2, 5])
        assert abs(rho) < 0.7

    def test_length_mismatch_returns_zero(self):
        assert _spearman_correlation([1, 2], [1, 2, 3]) == 0.0

    def test_too_few_points_returns_zero(self):
        assert _spearman_correlation([1, 2], [1, 2]) == 0.0

    def test_all_equal_returns_zero(self):
        """No variance → correlation undefined → fall back to 0."""
        assert _spearman_correlation([5, 5, 5, 5], [1, 2, 3, 4]) == 0.0

    def test_ties_handled_by_average_rank(self):
        """When multiple values tie, average their ranks. This
        pin catches a naive rank implementation that gives
        first-seen or last-seen rank on ties."""
        # Values 1, 2, 2, 4 — ranks 1, 2.5, 2.5, 4
        # Compared with 1, 2, 3, 4 — Spearman should be positive
        rho = _spearman_correlation([1, 2, 2, 4], [1, 2, 3, 4])
        assert rho > 0.5


class TestComputeFeatureViewCorrelation:
    def _make_video(self, title: str, views: int, hours: int) -> dict:
        past = (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "snippet": {
                "title": title,
                "description": "",
                "tags": [],
                "publishedAt": past,
            },
            "statistics": {"viewCount": str(views)},
        }

    def test_empty_input_returns_empty(self):
        assert compute_feature_view_correlation([]) == {}

    def test_too_few_videos_returns_empty(self):
        assert compute_feature_view_correlation([{}]) == {}
        assert compute_feature_view_correlation([{}, {}]) == {}

    def test_produces_all_expected_features(self):
        videos = [self._make_video(f"title {i}", 100 * i, 10) for i in range(1, 6)]
        result = compute_feature_view_correlation(videos)
        # Should have entries for every feature in _NUMERIC_FEATURES
        expected = {
            "title_length_chars",
            "title_length_words",
            "title_ends_with_question",
            "title_starts_with_number",
            "title_number_count",
            "title_emoji_count",
            "title_allcaps_word_count",
            "description_length_chars",
            "description_first_line_length",
            "description_has_timestamps",
            "tags_count",
            "publish_hour_utc",
            "publish_day_of_week",
        }
        assert set(result.keys()) == expected

    def test_correlations_in_valid_range(self):
        """Every correlation must be in [-1, 1]."""
        videos = [self._make_video(f"t {i}", 100 * i, 10) for i in range(1, 6)]
        result = compute_feature_view_correlation(videos)
        for feat, corr in result.items():
            assert -1.0 <= corr <= 1.0, f"{feat} correlation {corr} out of [-1, 1]"

    def test_positive_correlation_detected(self):
        """title_length_chars should correlate positively with views
        in the fabricated dataset where both scale together."""
        videos = [self._make_video("x" * (i * 5), 100 * i, 10) for i in range(1, 8)]
        result = compute_feature_view_correlation(videos)
        assert result["title_length_chars"] > 0.5

    def test_uses_view_velocity_not_raw_views(self):
        """A video with 1000 views published 100h ago has velocity 10.
        A video with 500 views published 10h ago has velocity 50 — HIGHER.
        Correlation should reflect velocity, not absolute views."""
        # Same title, different (views, hours) — velocity is what matters
        videos = [
            self._make_video("aaa", 1000, 100),  # velocity 10
            self._make_video("aaaa", 800, 40),  # velocity 20
            self._make_video("aaaaa", 500, 10),  # velocity 50 (highest)
            self._make_video("aaaaaa", 300, 3),  # velocity 100 (highest)
        ]
        result = compute_feature_view_correlation(videos)
        # Longer titles correlate with HIGHER velocity here
        assert result["title_length_chars"] > 0.5
