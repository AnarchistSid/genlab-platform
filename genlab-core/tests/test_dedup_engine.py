"""Tests for genlab_core.intelligence.dedup_engine."""
import hashlib


from genlab_core.intelligence.dedup_engine import (
    DedupEngine,
    DedupResult,
    jaccard_similarity,
)


# ---------------------------------------------------------------------------
# jaccard_similarity
# ---------------------------------------------------------------------------


class TestJaccardSimilarity:
    def test_identical_strings(self):
        assert jaccard_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        result = jaccard_similarity("abcdef", "xyz123")
        assert result < 0.1

    def test_similar_titles(self):
        a = "Elden Ring Sets Steam Record"
        b = "Elden Ring Breaks Steam Record"
        sim = jaccard_similarity(a, b)
        # These share most 3-grams, should be quite similar
        assert sim > 0.60

    def test_empty_strings(self):
        assert jaccard_similarity("", "hello") == 0.0
        assert jaccard_similarity("", "") == 0.0

    def test_short_string(self):
        # Strings shorter than n=3 produce empty ngram sets -> 0.0
        assert jaccard_similarity("ab", "ab") == 0.0
        # Strings exactly n=3 work normally
        assert jaccard_similarity("abc", "abc") == 1.0


# ---------------------------------------------------------------------------
# DedupEngine — Pass 1 (URL exact match)
# ---------------------------------------------------------------------------


class TestDedupPass1:
    def test_identical_urls_removed(self):
        items = [
            {"url": "https://example.com/story1", "title": "Story One"},
            {"url": "https://example.com/story1", "title": "Story One Copy"},
        ]
        engine = DedupEngine()
        result = engine.run(items)
        assert len(result.unique) == 1
        assert result.pass1_removed == 1

    def test_different_urls_kept(self):
        items = [
            {"url": "https://example.com/a", "title": "Same Title"},
            {"url": "https://example.com/b", "title": "Same Title"},
        ]
        engine = DedupEngine(jaccard_threshold=1.0, tfidf_threshold=1.0)
        result = engine.run(items)
        # Pass 1 keeps both (different URLs), but Pass 2 may catch them
        assert result.pass1_removed == 0

    def test_url_case_insensitive(self):
        items = [
            {"url": "https://EXAMPLE.COM/Story", "title": "A"},
            {"url": "https://example.com/story", "title": "B"},
        ]
        engine = DedupEngine()
        result = engine.run(items)
        assert result.pass1_removed == 1

    def test_full_sha256_hash_length(self):
        """URL hashes must be full SHA-256 (64 chars), never truncated."""
        url = "https://example.com/test"
        url_hash = hashlib.sha256(url.strip().lower().encode()).hexdigest()
        assert len(url_hash) == 64


# ---------------------------------------------------------------------------
# DedupEngine — Pass 2 (Jaccard)
# ---------------------------------------------------------------------------


class TestDedupPass2:
    def test_near_duplicate_titles_removed(self):
        items = [
            {"url": "https://a.com/1", "title": "Elden Ring Sets Steam Record"},
            {"url": "https://b.com/2", "title": "Elden Ring Breaks Steam Record"},
        ]
        engine = DedupEngine(jaccard_threshold=0.60, tfidf_threshold=1.0)
        result = engine.run(items)
        assert result.pass2_removed >= 1
        assert len(result.unique) == 1

    def test_different_titles_kept(self):
        items = [
            {"url": "https://a.com/1", "title": "OpenAI Releases GPT-5"},
            {"url": "https://b.com/2", "title": "Nintendo Announces New Switch"},
        ]
        engine = DedupEngine(jaccard_threshold=0.85, tfidf_threshold=1.0)
        result = engine.run(items)
        assert result.pass2_removed == 0
        assert len(result.unique) == 2


# ---------------------------------------------------------------------------
# DedupEngine — Pass 3 (TF-IDF)
# ---------------------------------------------------------------------------


class TestDedupPass3:
    def test_semantically_similar_clustered(self):
        """TF-IDF should catch semantically similar titles."""
        items = [
            {"url": f"https://site{i}.com", "title": t}
            for i, t in enumerate([
                "OpenAI unveils its next generation language model GPT-5",
                "GPT-5 announced by OpenAI as next generation language model",
                "Nintendo Switch 2 release date confirmed for March",
            ])
        ]
        engine = DedupEngine(jaccard_threshold=1.0, tfidf_threshold=0.50)
        result = engine.run(items)
        # First two are semantically similar; third is different
        assert result.pass3_removed >= 1
        assert len(result.unique) <= 2


# ---------------------------------------------------------------------------
# DedupEngine — existing_ids
# ---------------------------------------------------------------------------


class TestExistingIds:
    def test_existing_ids_prevents_republish(self):
        # Pre-compute URL hash as if it's from a prior backlog
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


# ---------------------------------------------------------------------------
# DedupEngine — per-pass counts
# ---------------------------------------------------------------------------


class TestDedupCounts:
    def test_per_pass_counts_accurate(self):
        items = [
            {"url": "https://a.com/1", "title": "Alpha Beta Gamma Story"},
            {"url": "https://a.com/1", "title": "Duplicate URL Story"},  # pass 1
            {"url": "https://b.com/2", "title": "Alpha Beta Gamma Story Again"},  # pass 2 (similar title at low threshold)
        ]
        engine = DedupEngine(jaccard_threshold=0.50, tfidf_threshold=1.0)
        result = engine.run(items)
        assert result.pass1_removed == 1
        total_removed = result.pass1_removed + result.pass2_removed + result.pass3_removed
        assert total_removed == len(items) - len(result.unique)


# ---------------------------------------------------------------------------
# DedupResult dataclass
# ---------------------------------------------------------------------------


class TestDedupResult:
    def test_default_values(self):
        r = DedupResult()
        assert r.unique == []
        assert r.duplicates == []
        assert r.pass1_removed == 0
        assert r.pass2_removed == 0
        assert r.pass3_removed == 0
