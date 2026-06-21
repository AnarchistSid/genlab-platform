"""Pin: embeddings primitive — similarity + Protocol (Lever R7 MVP).

Today the system has NO semantic similarity layer. Story dedup uses
string-edit distance, hook dedup uses Jaccard. Lever R7 ships the
data model + cosine-similarity primitives + Protocol. The encoder
itself (sentence-transformers, OpenAI, etc.) is deliberately deferred
to a follow-up PR — that's a bigger-scope dep choice.

These pins lock in:

  cosine_similarity (4):
  1. Identical vectors → 1.0
  2. Orthogonal vectors → 0.0
  3. Opposite vectors → -1.0
  4. Either zero-magnitude → 0.0 (graceful)

  find_top_k_similar (5):
  5. Empty records → []
  6. k <= 0 → []
  7. Sorted DESC by score (top-1 has highest similarity)
  8. min_score filter drops records below threshold
  9. k limits output length

  new_record (2):
  10. Default created_at set to now(UTC)
  11. numpy array vector coerced to list

  InMemoryEmbeddingBackend (4):
  12. Satisfies EmbeddingBackend Protocol via isinstance
  13. encode() is deterministic (same text → same vector)
  14. encode() produces correct dimension
  15. store() + find_similar() round-trip preserves the stored record
"""

from __future__ import annotations

import numpy as np
from genlab_core.learning.embeddings import (
    EmbeddingBackend,
    InMemoryEmbeddingBackend,
    cosine_similarity,
    find_top_k_similar,
    new_record,
)


class TestCosineSimilarity:
    def test_identical_vectors_return_one(self):
        v = [1.0, 2.0, 3.0]
        assert cosine_similarity(v, v) == 1.0

    def test_orthogonal_vectors_return_zero(self):
        """Pin: cosine of perpendicular vectors = 0."""
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_opposite_vectors_return_negative_one(self):
        """Pin: cosine of antiparallel vectors = -1."""
        v_pos = [1.0, 2.0, 3.0]
        v_neg = [-1.0, -2.0, -3.0]
        assert cosine_similarity(v_pos, v_neg) == -1.0

    def test_zero_magnitude_returns_zero(self):
        """Pin: zero vector has no direction — return 0 (orthogonal-like)
        instead of crashing on divide-by-zero."""
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
        assert cosine_similarity([1.0, 0.0], [0.0, 0.0]) == 0.0
        assert cosine_similarity([0.0, 0.0], [0.0, 0.0]) == 0.0


class TestFindTopKSimilar:
    def _make_records(self):
        return [
            _record_with_vec([1.0, 0.0], text="east"),
            _record_with_vec([0.9, 0.1], text="east-ish"),
            _record_with_vec([0.0, 1.0], text="north"),
            _record_with_vec([-1.0, 0.0], text="west"),
        ]

    def test_empty_records_returns_empty(self):
        assert find_top_k_similar([1.0, 0.0], []) == []

    def test_k_zero_returns_empty(self):
        records = self._make_records()
        assert find_top_k_similar([1.0, 0.0], records, k=0) == []

    def test_sorted_desc_by_score(self):
        """Pin: results sorted with highest cosine sim first. With
        min_score=-1.0 (negative scores allowed), the full ranking
        is: east > east-ish > north > west. The default min_score=0.0
        would drop ``west`` (cosine = -1) — exercised separately in
        ``test_min_score_filters_below_threshold``."""
        records = self._make_records()
        results = find_top_k_similar([1.0, 0.0], records, k=4, min_score=-1.0)
        texts = [r.text for r, _ in results]
        assert texts == ["east", "east-ish", "north", "west"]

    def test_min_score_filters_below_threshold(self):
        records = self._make_records()
        # min_score=0.5 → drops "north" (0.0) and "west" (-1.0)
        results = find_top_k_similar([1.0, 0.0], records, k=10, min_score=0.5)
        texts = [r.text for r, _ in results]
        assert texts == ["east", "east-ish"]

    def test_k_limits_output(self):
        records = self._make_records()
        results = find_top_k_similar([1.0, 0.0], records, k=2)
        assert len(results) == 2


class TestNewRecord:
    def test_default_created_at_is_now(self):
        from datetime import UTC, datetime, timedelta

        before = datetime.now(UTC) - timedelta(seconds=1)
        rec = new_record(text="hello", vector=[1.0, 2.0], model_name="test")
        after = datetime.now(UTC) + timedelta(seconds=1)
        rec_dt = datetime.fromisoformat(rec.created_at)
        assert before <= rec_dt <= after

    def test_numpy_array_vector_coerced_to_list(self):
        """Pin: numpy arrays are stored as list[float] for serialization
        friendliness. Future Postgres backend doesn't need ndarray-aware
        serializer."""
        arr = np.array([1.0, 2.0, 3.0], dtype=np.float64)
        rec = new_record(text="x", vector=arr, model_name="test")
        assert isinstance(rec.vector, list)
        assert rec.vector == [1.0, 2.0, 3.0]


class TestInMemoryEmbeddingBackend:
    def test_satisfies_protocol_via_isinstance(self):
        """Pin: runtime_checkable Protocol verifies the duck-type
        contract. Future SentenceTransformersBackend just needs
        encode + store + find_similar methods."""
        backend = InMemoryEmbeddingBackend()
        assert isinstance(backend, EmbeddingBackend)

    def test_encode_is_deterministic(self):
        """Pin: same text → same vector. Critical for re-runs producing
        the same dedup signatures."""
        backend = InMemoryEmbeddingBackend(dim=8)
        v1 = backend.encode("test text")
        v2 = backend.encode("test text")
        assert v1 == v2

    def test_encode_produces_correct_dimension(self):
        backend = InMemoryEmbeddingBackend(dim=16)
        vec = backend.encode("anything")
        assert len(vec) == 16

    def test_store_and_find_similar_round_trip(self):
        backend = InMemoryEmbeddingBackend(dim=8)
        for txt in ["alpha", "beta", "gamma"]:
            vec = backend.encode(txt)
            backend.store(new_record(text=txt, vector=vec, model_name=backend.model_name))
        assert len(backend) == 3

        # Find the most similar to "alpha" — should be alpha itself
        query = backend.encode("alpha")
        results = backend.find_similar(query, k=1)
        assert len(results) == 1
        # cosine_similarity(v, v) = 1.0
        assert results[0][0].text == "alpha"
        assert results[0][1] > 0.99


def _record_with_vec(vec, *, text: str):
    """Helper: build an EmbeddingRecord with a fixed vector + text."""
    return new_record(text=text, vector=vec, model_name="test")
