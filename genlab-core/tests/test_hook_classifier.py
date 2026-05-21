"""Tests for genlab_core.learning.hook_classifier.

Tests verify graceful degradation when no model/xgboost is available,
and correct behavior with mock models.
"""

from __future__ import annotations

from genlab_core.learning.hook_classifier import HookClassifier, train_and_save
from genlab_core.learning.hook_training_data import HookExample


class TestPredictProbaReturnsNeutralWhenNoModel:
    def test_no_model_returns_half(self, tmp_path):
        """Classifier returns 0.5 when no trained model exists."""
        clf = HookClassifier(niche_id="test_niche", models_dir=tmp_path)
        features = {
            "word_count": 5.0,
            "has_question": 1.0,
            "has_number": 0.0,
            "emoji_count": 0.0,
            "has_superlative": 0.0,
            "starts_with_you": 0.0,
            "avg_word_length": 4.5,
            "unique_word_ratio": 1.0,
        }
        result = clf.predict_proba(features)
        assert result == 0.5


class TestTrainSkipsWhenBelowMinExamples:
    def test_train_skips_with_few_examples(self, tmp_path):
        """Training returns False when example count < MIN_EXAMPLES."""
        examples = [
            HookExample(
                hook_text=f"Test hook {i}",
                platform="instagram",
                niche_id="test",
                reward_48h=float(i) / 10,
            )
            for i in range(50)  # Well below MIN_EXAMPLES (200)
        ]
        labels = [0] * 25 + [1] * 25

        result = train_and_save(examples, labels, niche_id="test", models_dir=tmp_path)
        assert result is False


class TestScoreHookReturnsFloat:
    def test_returns_float_value(self, tmp_path):
        """score_hook always returns a float."""
        clf = HookClassifier(niche_id="test_niche", models_dir=tmp_path)
        result = clf.score_hook("OpenAI just dropped GPT-5")
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

    def test_returns_neutral_without_model(self, tmp_path):
        """Without a model, score_hook returns 0.5."""
        clf = HookClassifier(niche_id="nonexistent", models_dir=tmp_path)
        result = clf.score_hook("Breaking: new AI model released")
        assert result == 0.5


class TestScoreHookGracefulOnEmptyText:
    def test_empty_string(self, tmp_path):
        """Empty hook text returns 0.5 without error."""
        clf = HookClassifier(niche_id="test_niche", models_dir=tmp_path)
        assert clf.score_hook("") == 0.5

    def test_whitespace_only(self, tmp_path):
        """Whitespace-only hook text returns 0.5 without error."""
        clf = HookClassifier(niche_id="test_niche", models_dir=tmp_path)
        assert clf.score_hook("   ") == 0.5

    def test_none_returns_neutral(self, tmp_path):
        """None hook text returns 0.5 without error."""
        clf = HookClassifier(niche_id="test_niche", models_dir=tmp_path)
        assert clf.score_hook(None) == 0.5
