"""Property-based tests for DedupEngine and jaccard_similarity."""
import pytest
from hypothesis import given, assume, settings
from hypothesis import strategies as st

from genlab_core.intelligence.dedup_engine import jaccard_similarity, DedupEngine, _char_ngrams

text_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=4, max_size=300,
)


@given(text_strategy)
def test_jaccard_identity(text):
    """Jaccard similarity of a text with itself must be 1.0."""
    assume(len(text.strip()) >= 3)  # Need at least 3 chars for a 3-gram
    sim = jaccard_similarity(text, text)
    assert abs(sim - 1.0) < 1e-9


@given(text_strategy, text_strategy)
def test_jaccard_symmetry(a, b):
    """Jaccard(a, b) must equal Jaccard(b, a)."""
    sim_ab = jaccard_similarity(a, b)
    sim_ba = jaccard_similarity(b, a)
    assert abs(sim_ab - sim_ba) < 1e-9


@given(text_strategy, text_strategy)
def test_jaccard_bounded_0_1(a, b):
    """Jaccard similarity must be in [0, 1]."""
    sim = jaccard_similarity(a, b)
    assert 0.0 <= sim <= 1.0


@given(text_strategy)
def test_dedup_same_url_always_deduplicates(text):
    """Identical URLs must be detected as duplicates."""
    assume(len(text.strip()) >= 3)
    items = [
        {"url": "https://example.com/same", "title": text},
        {"url": "https://example.com/same", "title": text},
    ]
    engine = DedupEngine()
    result = engine.run(items)
    assert result.pass1_removed >= 1
    assert len(result.unique) == 1


@settings(deadline=None)
@given(
    st.text(min_size=20, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
    st.text(min_size=20, max_size=200, alphabet=st.characters(blacklist_categories=("Cs",))),
)
def test_dedup_different_urls_different_titles(text_a, text_b):
    """Different URLs with very different titles should both survive."""
    assume(text_a.strip() != text_b.strip())
    sim = jaccard_similarity(text_a, text_b)
    assume(sim < 0.3)  # Only test genuinely different texts

    items = [
        {"url": "https://a.com/1", "title": text_a},
        {"url": "https://b.com/2", "title": text_b},
    ]
    engine = DedupEngine(jaccard_threshold=0.85, tfidf_threshold=0.80)
    result = engine.run(items)
    assert len(result.unique) == 2


@given(st.text(min_size=4, max_size=100, alphabet=st.characters(blacklist_categories=("Cs",))))
def test_ngrams_size(text):
    """Number of 3-grams should be max(0, len(text.strip()) - 2)."""
    ngrams = _char_ngrams(text, n=3)
    stripped = text.lower().strip()
    expected_max = max(0, len(stripped) - 2)
    assert len(ngrams) <= expected_max
