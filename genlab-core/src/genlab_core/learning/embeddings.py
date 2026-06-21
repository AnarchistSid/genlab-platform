"""Embeddings primitive — similarity helpers + Protocol (Lever R7 MVP).

Today the system has NO embedding-based similarity layer. Story dedup
uses string-edit distance (``edit_distance_ratio`` in Lever M).
Hook dedup uses Jaccard token overlap. Semantic similarity — "this new
patch-notes story is conceptually close to one we covered last week
even though the wording is different" — has no surface to query.

Lever R7 ships the **data model + cosine-similarity primitives** for
text embeddings. The encoder is NOT shipped — that requires either a
new dep (``sentence-transformers``, ``openai``, etc.) or a vector DB
(``pgvector``). Both are bigger-scope decisions that deserve their own
PR with operator sign-off.

What this PR ships:
- ``EmbeddingRecord`` dataclass for stored vectors
- Pure-numpy ``cosine_similarity(a, b)`` and ``find_top_k_similar``
- ``EmbeddingBackend`` Protocol (encode + store + find_similar)
- ``InMemoryEmbeddingBackend`` test-only impl

Future PRs will:
- Add a sentence-transformers backend (or OpenAI text-embedding-3-small)
- Migration for ``embeddings`` Postgres table with pgvector
- Wire-up: story ingest → embed → store → query for semantic dedup

## Why split it this way

Same "primitive-first" pattern as 11 of the 11 PRs shipped this
session. The math + Protocol are testable in isolation with synthetic
vectors. The encoder integration is a separate concern with its own
risk surface (dep size, API costs, model selection).

Run via:
    python -m genlab_core.learning.embeddings --help
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddingRecord:
    """One stored embedding.

    The ``vector`` is stored as ``list[float]`` for serialization
    friendliness — Postgres backend (future PR) can store as
    ``vector(N)`` pgvector type or jsonb. ``numpy`` conversion happens
    inside the similarity helpers, not in the storage layer.
    """

    text: str
    vector: list[float]
    model_name: str  # which encoder produced this vector
    metadata: dict[str, Any]  # niche_id, blueprint_id, etc.
    created_at: str  # ISO-8601 UTC


def new_record(
    *,
    text: str,
    vector: list[float] | np.ndarray,
    model_name: str,
    metadata: dict[str, Any] | None = None,
    when: datetime | None = None,
) -> EmbeddingRecord:
    """Build an EmbeddingRecord with sane defaults.

    Accepts ``vector`` as either ``list[float]`` or ``np.ndarray`` —
    numpy arrays are converted to lists for storage.
    """
    when = when or datetime.now(UTC)
    if hasattr(vector, "tolist"):
        vec_list = list(vector.tolist())  # type: ignore[attr-defined]
    else:
        vec_list = [float(v) for v in vector]
    return EmbeddingRecord(
        text=str(text or ""),
        vector=vec_list,
        model_name=str(model_name or "unknown"),
        metadata=dict(metadata) if metadata else {},
        created_at=when.isoformat(),
    )


def cosine_similarity(a: np.ndarray | list[float], b: np.ndarray | list[float]) -> float:
    """Cosine similarity in [-1, 1] (effectively [0, 1] for embedding vectors).

    Pure function. Lazy numpy import — module loadable in envs without
    numpy (similarity helpers will raise ImportError when called, but
    the data model + Protocol remain usable).

    Edge cases:
    - Either vector zero-magnitude → 0.0 (orthogonal, no info)
    - Mismatched dimensions → 0.0 (defensive; caller bug but don't crash)
    """
    try:
        import numpy as np
    except ImportError as err:
        raise RuntimeError(
            "cosine_similarity requires numpy — install genlab-core with [ml] extras"
        ) from err

    arr_a = np.asarray(a, dtype=np.float64)
    arr_b = np.asarray(b, dtype=np.float64)

    if arr_a.shape != arr_b.shape:
        logger.debug("[embeddings] shape mismatch: a=%s b=%s", arr_a.shape, arr_b.shape)
        return 0.0

    norm_a = float(np.linalg.norm(arr_a))
    norm_b = float(np.linalg.norm(arr_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return float(np.dot(arr_a, arr_b) / (norm_a * norm_b))


def find_top_k_similar(
    query: np.ndarray | list[float],
    records: list[EmbeddingRecord],
    *,
    k: int = 5,
    min_score: float = 0.0,
) -> list[tuple[EmbeddingRecord, float]]:
    """Return the top-K records by cosine similarity to ``query``.

    Filters out records with score < ``min_score`` (default 0.0 = no
    filter). Returns sorted DESC by score so callers can stream from
    the top.

    Pure function — no I/O. Caller fetches records from the backend
    and applies this filter.

    Edge cases:
    - Empty records → []
    - k <= 0 → []
    - All records below min_score → []
    """
    if not records or k <= 0:
        return []

    scored: list[tuple[EmbeddingRecord, float]] = []
    for r in records:
        score = cosine_similarity(query, r.vector)
        if score >= min_score:
            scored.append((r, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Persistence + encoding protocol for embeddings.

    Concrete implementations:
    - ``InMemoryEmbeddingBackend``: test-only, no persistence
    - ``SentenceTransformersBackend`` (future): wraps a model + stores
      in Postgres/pgvector
    - ``OpenAIEmbeddingBackend`` (future): API-backed encoder

    Methods:
    - ``encode(text) -> vector``: turn text into a vector
    - ``store(record)``: persist an EmbeddingRecord
    - ``find_similar(query_vec, k)``: return top-K most similar
    """

    def encode(self, text: str) -> list[float]: ...  # pragma: no cover
    def store(self, record: EmbeddingRecord) -> None: ...  # pragma: no cover
    def find_similar(
        self, query_vec: list[float], k: int = 5
    ) -> list[tuple[EmbeddingRecord, float]]: ...  # pragma: no cover


class InMemoryEmbeddingBackend:
    """Test-only backend with a fake encoder.

    The encoder is a deterministic hash-based mapping (NOT semantic) —
    enough to validate the storage + similarity round-trip in tests.
    DO NOT use in production; semantic similarity requires a real
    encoder (sentence-transformers, OpenAI, etc.).

    Implements the EmbeddingBackend Protocol so callers can swap to
    a real backend without touching call sites.
    """

    def __init__(self, dim: int = 8, model_name: str = "test_hash_encoder") -> None:
        self.dim = dim
        self.model_name = model_name
        self._records: list[EmbeddingRecord] = []

    def encode(self, text: str) -> list[float]:
        """Hash-based pseudo-encoder for tests.

        Deterministic: same text → same vector. NOT semantic — "hello"
        and "hi" produce orthogonal vectors, not similar ones.
        """
        import hashlib

        # Hash the text, then split bytes into floats in [-1, 1]
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Take dim bytes, normalize to [-1, 1]
        bytes_used = digest[: self.dim]
        return [(b - 127.5) / 127.5 for b in bytes_used]

    def store(self, record: EmbeddingRecord) -> None:
        self._records.append(record)

    def find_similar(
        self, query_vec: list[float], k: int = 5
    ) -> list[tuple[EmbeddingRecord, float]]:
        return find_top_k_similar(query_vec, self._records, k=k)

    def __len__(self) -> int:
        return len(self._records)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Embeddings primitive (Lever R7 MVP)")
    parser.add_argument(
        "--demo", action="store_true", help="Print a small demo of the test backend"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.demo:
        backend = InMemoryEmbeddingBackend(dim=8)
        for txt in ["esports tournament wrap", "new game patch notes", "movie trailer drop"]:
            vec = backend.encode(txt)
            backend.store(new_record(text=txt, vector=vec, model_name=backend.model_name))
        print(f"Stored {len(backend)} records.")
        query = backend.encode("game patch update")
        results = backend.find_similar(query, k=3)
        print("Top-3 similar to 'game patch update':")
        for rec, score in results:
            print(f"  {score:+.4f} — {rec.text}")
    else:
        print("Use --demo to see a sample run.")
        print("Programmatic usage:")
        print(
            "  from genlab_core.learning.embeddings import ("
            "EmbeddingRecord, InMemoryEmbeddingBackend, cosine_similarity, find_top_k_similar)"
        )
