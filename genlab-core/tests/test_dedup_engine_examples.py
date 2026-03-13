"""Example-based tests for dedup_engine (complement Sprint 21 Hypothesis tests).

These tests use concrete, readable examples to document the 3-pass dedup
pipeline and the make_content_id utility.
"""
import hashlib

import pytest

from genlab_core.intelligence.dedup_engine import (
    DedupEngine,
    DedupResult,
    jaccard_similarity,
    make_content_id,
)


# ---------------------------------------------------------------------------
# make_content_id
# ---------------------------------------------------------------------------


class TestMakeContentId:
    def test_is_64_chars(self):
        cid = make_content_id("hello world")
        assert len(cid) == 64

    def test_deterministic(self):
        assert make_content_id("test") == make_content_id("test")

    def test_matches_raw_sha256(self):
        text = "some content to hash"
        expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert make_content_id(text) == expected

    def test_different_inputs_different_ids(self):
        assert make_content_id("alpha") != make_content_id("beta")

    def test_empty_string(self):
        cid = make_content_id("")
        assert len(cid) == 64
        # SHA-256 of empty string is a known constant
        assert cid == hashlib.sha256(b"").hexdigest()


# ---------------------------------------------------------------------------
# DedupEngine — batch run examples
# ---------------------------------------------------------------------------


class TestDedupEngineExamples:
    def test_exact_url_match_deduplicates(self):
        """Pass 1: same URL (case-insensitive) = hard duplicate."""
        items = [
            {"url": "https://example.com/article1", "title": "First version"},
            {"url": "https://example.com/article1", "title": "Second version"},
        ]
        engine = DedupEngine()
        result = engine.run(items)
        assert len(result.unique) == 1
        assert result.pass1_removed == 1

    def test_near_duplicate_titles_caught(self):
        """Pass 2: Jaccard similarity catches similar headlines."""
        items = [
            {"url": "https://a.com/1", "title": "Breaking: New AI model achieves record performance"},
            {"url": "https://b.com/2", "title": "Breaking: New AI model achieves record performance scores"},
        ]
        engine = DedupEngine(jaccard_threshold=0.70, tfidf_threshold=1.0)
        result = engine.run(items)
        assert result.pass2_removed >= 1
        assert len(result.unique) == 1

    def test_distinct_texts_not_flagged(self):
        """Genuinely different content survives all 3 passes."""
        items = [
            {"url": "https://a.com/1", "title": "Apple releases new iPhone with advanced camera"},
            {"url": "https://b.com/2", "title": "Google announces quantum computing breakthrough"},
        ]
        engine = DedupEngine(jaccard_threshold=0.85, tfidf_threshold=0.80)
        result = engine.run(items)
        assert len(result.unique) == 2
        assert result.pass1_removed == 0
        assert result.pass2_removed == 0

    def test_existing_ids_block_republish(self):
        """Pre-seeded URL hashes prevent re-ingesting known content."""
        prior_url = "https://example.com/old-story"
        prior_hash = hashlib.sha256(prior_url.strip().lower().encode()).hexdigest()

        items = [
            {"url": "https://example.com/old-story", "title": "Old Story"},
            {"url": "https://example.com/new-story", "title": "New Story"},
        ]
        engine = DedupEngine(existing_ids={prior_hash})
        result = engine.run(items)
        assert result.pass1_removed == 1
        assert len(result.unique) == 1
        assert result.unique[0]["title"] == "New Story"

    def test_empty_batch_returns_empty(self):
        engine = DedupEngine()
        result = engine.run([])
        assert len(result.unique) == 0
        assert result.pass1_removed == 0

    def test_single_item_survives(self):
        items = [{"url": "https://a.com/1", "title": "Only one item"}]
        engine = DedupEngine()
        result = engine.run(items)
        assert len(result.unique) == 1

    def test_per_pass_counts_sum_to_total_removed(self):
        """Accounting invariant: removed counts must add up."""
        items = [
            {"url": "https://a.com/1", "title": "Alpha Beta Gamma Story"},
            {"url": "https://a.com/1", "title": "Duplicate URL"},  # pass 1
            {"url": "https://b.com/2", "title": "Alpha Beta Gamma Story Again"},  # pass 2
        ]
        engine = DedupEngine(jaccard_threshold=0.50, tfidf_threshold=1.0)
        result = engine.run(items)
        total_removed = result.pass1_removed + result.pass2_removed + result.pass3_removed
        assert total_removed == len(items) - len(result.unique)


# ---------------------------------------------------------------------------
# jaccard_similarity — edge case examples
# ---------------------------------------------------------------------------


class TestJaccardExamples:
    def test_identical_strings(self):
        assert jaccard_similarity("hello world", "hello world") == 1.0

    def test_one_char_difference(self):
        """Single-word swap: still very similar."""
        a = "Elden Ring Sets Steam Record"
        b = "Elden Ring Breaks Steam Record"
        sim = jaccard_similarity(a, b)
        assert sim > 0.60

    def test_empty_string_yields_zero(self):
        assert jaccard_similarity("", "anything") == 0.0

    def test_below_ngram_length(self):
        """Strings shorter than n=3 produce no ngrams -> 0.0."""
        assert jaccard_similarity("ab", "ab") == 0.0

    def test_symmetry(self):
        a, b = "hello world", "world hello"
        assert jaccard_similarity(a, b) == jaccard_similarity(b, a)
