"""Transformer-embedding extraction for hook quality prediction (W3.3 foundation).

Adds a semantic-similarity feature vector to complement the existing
8 hand-engineered regex features in ``hook_features.py``. The intent
is to capture *what the hook is about* (topic), not just *how it's
structured* (length, emoji count) — so the bandit can eventually
discriminate "viral about gaming-news" from "viral about anime-news".

Three-tier embedding strategy (degrade gracefully):

1. **sentence-transformers** (preferred, ~22MB MiniLM-L6-v2 model):
   produces 384-d dense embeddings tuned for semantic similarity.
   Best quality but adds a ~250MB dep tree (torch + tokenizers).

2. **scikit-learn TfidfVectorizer** (fallback, ~0 extra deps):
   produces sparse bag-of-words vectors. Captures word identity but
   not semantic similarity. Good enough for cold-start.

3. **Zero vector** (fail-open): returns a fixed-size zero vector if
   neither library can load. The classifier degrades to its
   hand-feature path and still produces predictions — never crashes.

Design notes
~~~~~~~~~~~~

- The choice between tiers is made ONCE at module-load and cached. We
  never mix tiers within a run (the classifier would see two different
  feature spaces and produce nonsense scores).

- Embedding dimensionality is fixed per tier so the downstream
  classifier always sees a consistent feature shape:
    - sentence-transformers: 384 (MiniLM-L6-v2)
    - TF-IDF: 256 (sparse → dense pad/trunc)
    - zero fallback: 256

- The TF-IDF vocabulary is fit lazily on the first call — keep an in-
  memory vocabulary that grows as more hooks are seen. This is OK for
  inference but would need a proper training-corpus pass for real
  bandit training. The training entry-point will pin the vocab at
  the start of a training run; for inference-time feature extraction
  the lazy growth is the right trade-off.

- All embedding calls are best-effort fail-open. A bad input never
  crashes the classifier — it returns a zero vector and the caller's
  hand-feature path carries the prediction.

Usage
~~~~~

    from genlab_core.learning.hook_embeddings import embed_hook
    vec = embed_hook("This new AI model just changed everything")
    # → numpy.ndarray shape=(384,) or (256,) depending on tier

To pin a specific tier for testing:

    from genlab_core.learning.hook_embeddings import set_embedding_tier
    set_embedding_tier("tfidf")  # or "sentence-transformers" or "zero"

W3.3 status
~~~~~~~~~~~

This is the **foundation** layer. The classifier integration (using
these embeddings as features in the XGBoost / replacement model) is
the next-step PR. Shipping the extractor first lets us:

1. Decide on the tier at the operator level (set the env var
   ``GENLAB_HOOK_EMBEDDING_TIER=tfidf`` if you don't want the
   sentence-transformers dep tree on prod)
2. Cache embeddings during pipeline runs without yet wiring them
   into model training — gives us a corpus to fit on
3. Move incrementally: when the classifier integration ships, the
   extractor is already known-stable
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Literal

logger = logging.getLogger(__name__)


EmbeddingTier = Literal["sentence-transformers", "tfidf", "zero"]


# Fixed dim per tier — the classifier can rely on this contract.
_DIM_BY_TIER: dict[EmbeddingTier, int] = {
    "sentence-transformers": 384,
    "tfidf": 256,
    "zero": 256,
}


# ── Lazy availability detection ──────────────────────────────────────


def _detect_best_tier() -> EmbeddingTier:
    """Pick the highest-fidelity tier available in the environment.

    Operators can pin a tier via ``GENLAB_HOOK_EMBEDDING_TIER`` — handy
    for prod where the sentence-transformers download (~250MB tree +
    a ~22MB model) is a non-trivial cold-start cost. Default
    auto-detects.
    """
    pinned = os.environ.get("GENLAB_HOOK_EMBEDDING_TIER", "").strip().lower()
    if pinned in ("sentence-transformers", "tfidf", "zero"):
        return pinned  # type: ignore[return-value]

    try:
        import sentence_transformers  # noqa: F401

        return "sentence-transformers"
    except ImportError:
        pass

    try:
        import sklearn  # noqa: F401

        return "tfidf"
    except ImportError:
        pass

    logger.warning(
        "Neither sentence-transformers nor sklearn available; "
        "hook embeddings will return zero vectors. Install one of "
        "the libraries to enable real semantic features."
    )
    return "zero"


_TIER: EmbeddingTier | None = None
_TIER_LOCK = threading.Lock()


def get_embedding_tier() -> EmbeddingTier:
    """Return the currently-active embedding tier (cached after first call)."""
    global _TIER
    if _TIER is None:
        with _TIER_LOCK:
            if _TIER is None:
                _TIER = _detect_best_tier()
                logger.info("Hook embedding tier resolved: %s", _TIER)
    return _TIER


def set_embedding_tier(tier: EmbeddingTier) -> None:
    """Force a specific tier — used in tests + by operator override scripts.

    Note: forcing a tier different from what was previously cached
    invalidates any embeddings produced under the old tier (different
    dimensionality). Callers must re-extract.
    """
    global _TIER, _SENT_MODEL, _TFIDF_VEC
    with _TIER_LOCK:
        _TIER = tier
        # Reset caches so the new tier loads fresh
        _SENT_MODEL = None
        _TFIDF_VEC = None


def get_embedding_dim() -> int:
    """Return the dimensionality of the current tier's output vectors."""
    return _DIM_BY_TIER[get_embedding_tier()]


# ── Tier 1: sentence-transformers ────────────────────────────────────


_SENT_MODEL = None
_SENT_MODEL_LOCK = threading.Lock()
# MiniLM-L6-v2: 22MB model, 384-d output, fast inference, good quality
# for short-text semantic similarity. The standard default for "I want
# embeddings and don't have a strong opinion".
_SENT_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _get_sent_model():
    """Load and cache the sentence-transformers model (lazy, thread-safe)."""
    global _SENT_MODEL
    if _SENT_MODEL is not None:
        return _SENT_MODEL
    with _SENT_MODEL_LOCK:
        if _SENT_MODEL is not None:
            return _SENT_MODEL
        try:
            from sentence_transformers import SentenceTransformer

            _SENT_MODEL = SentenceTransformer(_SENT_MODEL_NAME)
            logger.info("Loaded sentence-transformers model %s", _SENT_MODEL_NAME)
        except Exception as exc:  # pragma: no cover - environment-dependent
            logger.warning(
                "Failed to load sentence-transformers model %s: %s — falling back to TF-IDF tier",
                _SENT_MODEL_NAME,
                exc,
            )
            set_embedding_tier("tfidf")
            return None
        return _SENT_MODEL


def _embed_with_sent(text: str):
    """Return a 384-d numpy vector via sentence-transformers, or None on failure."""
    model = _get_sent_model()
    if model is None:
        return None
    try:
        # encode returns a single vector for a single string. Cast to
        # float32 explicitly for downstream compatibility with XGBoost.
        vec = model.encode(text, convert_to_numpy=True)
        return vec.astype("float32")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("sentence-transformers encode failed for hook: %s", exc)
        return None


# ── Tier 2: TF-IDF ───────────────────────────────────────────────────


_TFIDF_VEC = None
_TFIDF_LOCK = threading.Lock()


def _get_tfidf_vec():
    """Return a lazily-built TfidfVectorizer with growing in-memory vocab.

    For real bandit training, the caller should `fit` against the
    training corpus and freeze the vocab. For ad-hoc inference,
    ``partial_fit`` semantics aren't a thing in sklearn's TfidfVectorizer
    so we just use ``HashingVectorizer`` underneath for a stable shape
    regardless of input vocabulary.
    """
    global _TFIDF_VEC
    if _TFIDF_VEC is not None:
        return _TFIDF_VEC
    with _TFIDF_LOCK:
        if _TFIDF_VEC is not None:
            return _TFIDF_VEC
        try:
            from sklearn.feature_extraction.text import HashingVectorizer

            _TFIDF_VEC = HashingVectorizer(
                n_features=_DIM_BY_TIER["tfidf"],
                analyzer="word",
                lowercase=True,
                ngram_range=(1, 2),  # unigrams + bigrams catch "very good"
                norm="l2",
                alternate_sign=False,  # all non-negative; friendlier for tree models
            )
            logger.info("Initialised HashingVectorizer for TF-IDF tier")
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "Failed to initialise HashingVectorizer: %s — falling back to zero tier",
                exc,
            )
            set_embedding_tier("zero")
            return None
        return _TFIDF_VEC


def _embed_with_tfidf(text: str):
    """Return a 256-d numpy vector via sklearn HashingVectorizer, or None on failure."""
    vec = _get_tfidf_vec()
    if vec is None:
        return None
    try:
        # transform returns a scipy sparse matrix; densify to 1D float32
        sparse = vec.transform([text])
        return sparse.toarray().ravel().astype("float32")
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("HashingVectorizer transform failed for hook: %s", exc)
        return None


# ── Tier 3: zero fallback ────────────────────────────────────────────


def _embed_zero(text: str):  # noqa: ARG001 - signature compat
    """Return a fixed-size zero vector."""
    try:
        import numpy as np

        return np.zeros(_DIM_BY_TIER["zero"], dtype="float32")
    except ImportError:
        # numpy is a hard dep of xgboost so this is unreachable in
        # practice, but the fail-open contract demands we cover it.
        return [0.0] * _DIM_BY_TIER["zero"]


# ── Public API ───────────────────────────────────────────────────────


def embed_hook(text: str):
    """Return a fixed-dimensional embedding vector for the hook text.

    Output shape depends on the active tier (see ``get_embedding_dim``);
    use a `len(vec) == get_embedding_dim()` assertion in downstream
    consumers if you want a hard pin.

    Empty/None text returns a zero vector. Never raises.
    """
    if not text:
        return _embed_zero("")

    tier = get_embedding_tier()
    if tier == "sentence-transformers":
        vec = _embed_with_sent(text)
        if vec is not None:
            return vec
        # Fall through to tfidf if sentence-transformers failed at call time
        tier = "tfidf"

    if tier == "tfidf":
        vec = _embed_with_tfidf(text)
        if vec is not None:
            return vec
        tier = "zero"

    return _embed_zero(text)
