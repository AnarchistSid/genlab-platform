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
        features = extract_text_features("Breaking news \U0001f525")
        assert features["emoji_count"] >= 1.0

    def test_multiple_emoji(self):
        features = extract_text_features(
            "\U0001f6a8 OpenAI just dropped GPT-5 \U0001f525\U0001f4a5"
        )
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


# ── W3.3 Layer 2: include_embeddings flag ──────────────────────────


class TestBuildFeatureVectorWithEmbeddings:
    """Pin the contract for the include_embeddings flag.

    W3.3 Layer 2 — see docs/W3-3-transformer-hook-classifier-roadmap.md.
    """

    def test_default_off_returns_only_text_features(self):
        """Backwards-compat: existing callers see no schema change."""
        features = build_feature_vector("Some hook text")
        assert "word_count" in features
        # No embedding keys when flag is off
        emb_keys = [k for k in features if k.startswith("emb_")]
        assert emb_keys == []

    def test_include_embeddings_appends_emb_keys(self):
        """include_embeddings=True adds emb_N keys to the dict."""
        from genlab_core.learning import hook_embeddings as he

        # Pin to zero tier so dim is deterministic across environments
        he.set_embedding_tier("zero")
        try:
            features = build_feature_vector("Some hook text", include_embeddings=True)
            assert "word_count" in features  # text features still present
            emb_keys = [k for k in features if k.startswith("emb_")]
            # Zero tier returns 256-dim vector
            assert len(emb_keys) == 256
            assert all(features[k] == 0.0 for k in emb_keys)
        finally:
            he._TIER = None  # noqa: SLF001 - reset for other tests

    def test_include_embeddings_keys_are_zero_indexed(self):
        """emb_0..emb_N-1 with no gaps."""
        from genlab_core.learning import hook_embeddings as he

        he.set_embedding_tier("zero")
        try:
            features = build_feature_vector("x", include_embeddings=True)
            emb_indices = sorted(int(k.split("_")[1]) for k in features if k.startswith("emb_"))
            assert emb_indices == list(range(256))
        finally:
            he._TIER = None  # noqa: SLF001

    def test_tfidf_tier_produces_different_vectors(self):
        """TF-IDF tier emits semantically-distinguishable vectors."""
        from genlab_core.learning import hook_embeddings as he

        he.set_embedding_tier("tfidf")
        try:
            f1 = build_feature_vector("AI just changed gaming", include_embeddings=True)
            f2 = build_feature_vector("Football team wins title", include_embeddings=True)
            emb_keys = sorted(k for k in f1 if k.startswith("emb_"))
            assert len(emb_keys) > 0
            # At least one embedding component should differ between the two texts
            differs = [k for k in emb_keys if f1[k] != f2[k]]
            assert differs, "TF-IDF tier should distinguish different texts"
        finally:
            he._TIER = None  # noqa: SLF001

    def test_text_features_unchanged_with_or_without_embeddings(self):
        """The 8 text-feature values must be identical regardless of
        whether embeddings are appended."""
        from genlab_core.learning import hook_embeddings as he

        he.set_embedding_tier("zero")
        try:
            f_off = build_feature_vector("Some hook text")
            f_on = build_feature_vector("Some hook text", include_embeddings=True)
            for key, value in f_off.items():
                assert f_on[key] == value, f"text feature {key} differs"
        finally:
            he._TIER = None  # noqa: SLF001
