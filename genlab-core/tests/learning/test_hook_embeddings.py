"""Tests for hook_embeddings (W3.3 foundation).

The module is fail-open by design so most tests exercise the contract
shape (right dim, never raises) rather than embedding quality. Quality
tests for the sentence-transformers tier ship with the classifier
integration PR.
"""

from __future__ import annotations

import importlib

import pytest
from genlab_core.learning import hook_embeddings as he


@pytest.fixture(autouse=True)
def _reset_tier_between_tests():
    """Reset cached tier so each test picks its own."""
    # ruff: noqa: SLF001
    he._TIER = None
    he._SENT_MODEL = None
    he._TFIDF_VEC = None
    yield
    he._TIER = None
    he._SENT_MODEL = None
    he._TFIDF_VEC = None


class TestEmbeddingTierResolution:
    """Tier auto-detect + operator-pinned override contract."""

    def test_env_var_overrides_to_zero(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_EMBEDDING_TIER", "zero")
        assert he._detect_best_tier() == "zero"

    def test_env_var_overrides_to_tfidf(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_EMBEDDING_TIER", "tfidf")
        assert he._detect_best_tier() == "tfidf"

    def test_env_var_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("GENLAB_HOOK_EMBEDDING_TIER", "ZERO")
        assert he._detect_best_tier() == "zero"

    def test_unknown_env_value_ignored(self, monkeypatch):
        """Unknown value falls through to auto-detection."""
        monkeypatch.setenv("GENLAB_HOOK_EMBEDDING_TIER", "bogus")
        # Auto-detect picks tfidf since sklearn is installed in test env
        # and sentence-transformers is not
        tier = he._detect_best_tier()
        assert tier in ("sentence-transformers", "tfidf", "zero")

    def test_set_embedding_tier_invalidates_caches(self):
        """Switching tiers must clear model caches — different dims."""
        he.set_embedding_tier("tfidf")
        he._TFIDF_VEC = "fake-cache"  # type: ignore[assignment]
        he.set_embedding_tier("zero")
        assert he._TFIDF_VEC is None
        assert he._SENT_MODEL is None


class TestZeroTier:
    """The fail-open contract: always returns a fixed-size vector."""

    def test_empty_input_returns_zero_vector(self):
        he.set_embedding_tier("zero")
        vec = he.embed_hook("")
        assert len(vec) == he.get_embedding_dim()
        assert all(x == 0.0 for x in vec)

    def test_none_input_returns_zero_vector(self):
        he.set_embedding_tier("zero")
        vec = he.embed_hook(None)  # type: ignore[arg-type]
        assert len(vec) == he.get_embedding_dim()

    def test_zero_tier_returns_zero_vector(self):
        he.set_embedding_tier("zero")
        vec = he.embed_hook("This is a real hook with real words")
        assert len(vec) == 256
        assert all(x == 0.0 for x in vec)

    def test_get_embedding_dim_is_256_for_zero(self):
        he.set_embedding_tier("zero")
        assert he.get_embedding_dim() == 256


class TestTfidfTier:
    """HashingVectorizer-backed semantic features."""

    def test_tfidf_returns_correct_dim(self):
        he.set_embedding_tier("tfidf")
        vec = he.embed_hook("AI just changed everything for content creators")
        assert len(vec) == 256

    def test_tfidf_different_hooks_produce_different_vectors(self):
        he.set_embedding_tier("tfidf")
        v1 = he.embed_hook("New AI tool launched today")
        v2 = he.embed_hook("Football team wins championship")
        # Vectors should differ (at least some components)
        import numpy as np

        assert not np.allclose(v1, v2)

    def test_tfidf_same_hook_produces_same_vector(self):
        """Determinism — same input gives same embedding."""
        he.set_embedding_tier("tfidf")
        v1 = he.embed_hook("Same text here")
        v2 = he.embed_hook("Same text here")
        import numpy as np

        assert np.allclose(v1, v2)

    def test_tfidf_l2_normalised(self):
        """HashingVectorizer with norm='l2' returns unit-magnitude vectors
        (or zero vector for inputs that hash to nothing)."""
        he.set_embedding_tier("tfidf")
        vec = he.embed_hook("Real content words here")
        import numpy as np

        magnitude = float(np.linalg.norm(vec))
        assert abs(magnitude - 1.0) < 1e-5 or magnitude == 0.0

    def test_tfidf_non_negative_entries(self):
        """alternate_sign=False means all entries >= 0 — friendlier
        downstream for tree-based classifiers."""
        he.set_embedding_tier("tfidf")
        vec = he.embed_hook("Some test content with multiple words")
        assert all(x >= 0.0 for x in vec)


class TestPublicAPI:
    """Smoke tests on the public entry points."""

    def test_embed_hook_never_raises_on_garbage_input(self):
        """Contract: embed_hook is fail-open."""
        he.set_embedding_tier("tfidf")
        # Should not raise on any string-like input
        for text in ["", "a", "🚀🎉", "with\nnewlines\n", "unicode 中文 mixed", " " * 100]:
            vec = he.embed_hook(text)
            assert len(vec) == he.get_embedding_dim()

    def test_module_importable_without_sentence_transformers(self):
        """Even on a minimal install (no sentence-transformers), the
        module must import + be usable."""
        importlib.reload(he)
        # Set to tfidf to ensure we don't accidentally try to load
        # sentence-transformers if it ever sneaks in
        he.set_embedding_tier("tfidf")
        assert he.embed_hook("test") is not None
