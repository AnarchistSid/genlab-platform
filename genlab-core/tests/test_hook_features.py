"""Tests for genlab_core.learning.hook_features.

Verifies regex-based text feature extraction for hook quality prediction.
"""
from __future__ import annotations

from genlab_core.learning.hook_features import (
    build_feature_vector,
    extract_text_features,
)


class TestExtractTextFeaturesQuestionDetection:
    def test_question_mark_detected(self):
        features = extract_text_features("Is this the end of coding?")
        assert features["has_question"] == 1.0

    def test_no_question_mark(self):
        features = extract_text_features("AI just changed everything")
        assert features["has_question"] == 0.0

    def test_question_with_trailing_whitespace(self):
        features = extract_text_features("Will GPT-5 replace developers?  ")
        assert features["has_question"] == 1.0


class TestExtractTextFeaturesEmojiCount:
    def test_single_emoji(self):
        features = extract_text_features("Breaking news \U0001F525")
        assert features["emoji_count"] >= 1.0

    def test_multiple_emoji(self):
        features = extract_text_features("\U0001F6A8 OpenAI just dropped GPT-5 \U0001F525\U0001F4A5")
        assert features["emoji_count"] >= 2.0

    def test_no_emoji(self):
        features = extract_text_features("This is a plain text hook")
        assert features["emoji_count"] == 0.0


class TestExtractTextFeaturesHasNumber:
    def test_integer(self):
        features = extract_text_features("10 reasons AI will change your life")
        assert features["has_number"] == 1.0

    def test_percentage(self):
        features = extract_text_features("GPT-5 is 50% faster")
        assert features["has_number"] == 1.0

    def test_dollar_amount(self):
        features = extract_text_features("This $10B deal changes everything")
        assert features["has_number"] == 1.0

    def test_no_number(self):
        features = extract_text_features("AI is the future")
        assert features["has_number"] == 0.0


class TestExtractTextFeaturesEmpty:
    def test_empty_string(self):
        assert extract_text_features("") == {}

    def test_whitespace_only(self):
        assert extract_text_features("   ") == {}

    def test_none_like_empty(self):
        # None would cause TypeError in practice, but empty string is safe
        assert extract_text_features("") == {}


class TestBuildFeatureVector:
    def test_returns_text_features(self):
        features = build_feature_vector("Is this the best AI tool?")
        assert "word_count" in features
        assert "has_question" in features
        assert "has_superlative" in features
        assert features["has_question"] == 1.0
        assert features["has_superlative"] == 1.0  # "best" is a superlative

    def test_audio_path_ignored(self):
        f1 = build_feature_vector("Test hook text")
        f2 = build_feature_vector("Test hook text", audio_path="/some/path.mp3")
        assert f1 == f2


class TestExtractTextFeaturesStartsWithYou:
    def test_starts_with_you(self):
        features = extract_text_features("You need to see this")
        assert features["starts_with_you"] == 1.0

    def test_starts_with_your(self):
        features = extract_text_features("Your next AI assistant is here")
        assert features["starts_with_you"] == 1.0

    def test_does_not_start_with_you(self):
        features = extract_text_features("OpenAI just released GPT-5")
        assert features["starts_with_you"] == 0.0

    def test_you_not_first_word(self):
        features = extract_text_features("What you need to know")
        assert features["starts_with_you"] == 0.0
