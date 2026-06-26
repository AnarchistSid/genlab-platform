"""Embedding-distance rejection: catch hook template overfit proactively.

The 2026-05 → 2026-06 audits found a recurring failure mode in
``writing/llm_hook_generator.py``: the LLM drifts into formulaic
patterns ("Cinema is back", "absolutely insane", "No more excuses")
and the only mitigation we had was reactive — adding the offending
phrase to ``_BANNED_PHRASES`` (lines ~148-211 of that module). The
list grew by ~5-10 entries every few weeks and still missed near-
variants (e.g. "Cinema returned", "absolutely wild").

This module is the proactive complement: instead of pinning bad
phrases by hand, we measure each candidate hook against the
embeddings of the last N PUBLISHED hooks for the niche. Anything
above the configured cosine-similarity threshold is dropped from
contention. If ALL 3 candidates exceed the threshold, the caller
falls back to the highest-scored one — we never block generation
outright.

Why embedding distance, not exact-match
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Exact-match banlists only catch phrases the operator already saw
in production. Semantic-distance catches near-paraphrases ("Cinema
is back" vs "Cinema has returned") before they ship. The MiniLM-
L6-v2 embeddings (via ``learning/hook_embeddings.py``) capture
phrase-level similarity well enough for short-text de-dup; the
threshold (default 0.85) is tunable per niche by operator override.

Safety posture (every operator-facing path is fail-OPEN)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Env-flag gated.** ``GENLAB_HOOK_DIVERSITY_ENABLED`` must be
   ``"1"`` for the module to do anything. Default unset → no-op.
   Mirrors the ``GENLAB_HOOK_CRITIC_ENABLED`` pattern in
   ``llm_hook_generator.py`` (shadow-rollout discipline).

2. **Fail-OPEN on every error path.** Postgres unreachable →
   ``False``. Embedding load failed → ``False``. Empty history →
   ``False``. The hook generator MUST work without this signal —
   diversity penalty is augmentation, not a hard dependency.

3. **5-min TTL cache.** Mirrors the cache pattern in
   ``learning/rejection_rag.py``. One pipeline run for a niche
   generates 3-10 hook candidates; we pay the (DB query +
   30-embedding compute) cost once, not per candidate.

Wire-in point
~~~~~~~~~~~~~

``llm_hook_generator.py`` calls ``reject_if_too_similar`` inside
the candidate-ranking loop. When the env flag is OFF (default),
every call returns ``False`` and ranking proceeds identically to
the pre-diversity behavior. When ON, candidates that score above
threshold get dropped before the classifier/critic stages.

Design source: ``docs/AGENT-LEARNING-ENGINES.md``.
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)


# Module-level cache: {niche_id: (timestamp, list_of_embedding_vectors)}.
# 5-min TTL mirrors the rejection_rag cache — one pipeline run for a
# niche generates 3-10 hook candidates; the cached embeddings apply to
# all of them. Operator publishes flow at ~1/day per niche so 5-min
# stale is fine (the 30th hook would have rolled over hours ago anyway).
_DIVERSITY_CACHE: dict[str, tuple[float, list]] = {}
_DIVERSITY_TTL_SECONDS = 300


# Defaults: operator can override either via env var.
_DEFAULT_THRESHOLD = 0.85
_DEFAULT_HISTORY_LIMIT = 30


def _reset_cache_for_tests() -> None:
    """Test helper: clear the module-level cache between unit tests."""
    _DIVERSITY_CACHE.clear()


def _is_enabled() -> bool:
    """Return True iff the operator has opted into diversity rejection.

    Default OFF. Set ``GENLAB_HOOK_DIVERSITY_ENABLED=1`` to enable.
    Only the literal string ``"1"`` enables — mirrors the strict
    parsing in the auto-pause flag (avoids ambiguity around
    "true"/"yes"/"on" copy-paste variants).
    """
    return os.environ.get("GENLAB_HOOK_DIVERSITY_ENABLED", "").strip() == "1"


def _get_threshold() -> float:
    """Return the cosine-similarity threshold above which candidates are dropped.

    Default 0.85 — empirically tight enough to catch near-paraphrases
    of recent hooks without rejecting topically-related but distinct
    framings. Operator override:
    ``GENLAB_HOOK_DIVERSITY_THRESHOLD=0.95``.

    Falls back to default on parse failure (fail-OPEN: bad env value
    must not block hook generation).
    """
    raw = os.environ.get("GENLAB_HOOK_DIVERSITY_THRESHOLD", "").strip()
    if not raw:
        return _DEFAULT_THRESHOLD
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.debug(
            "[hook_diversity] bad GENLAB_HOOK_DIVERSITY_THRESHOLD=%r — using default %.2f",
            raw,
            _DEFAULT_THRESHOLD,
        )
        return _DEFAULT_THRESHOLD


def _fetch_recent_hooks(niche_id: str, limit: int = _DEFAULT_HISTORY_LIMIT) -> list[str]:
    """Pull the last ``limit`` PUBLISHED hooks for ``niche_id`` from Postgres.

    Returns empty list on any failure (DB unreachable, no DATABASE_URL,
    no rows, query error). Fail-OPEN — the caller treats empty history
    as "no signal" and proceeds without rejection.
    """
    if not os.environ.get("DATABASE_URL"):
        return []

    try:
        from genlab_core.storage.tenant_context import pg_connect

        dsn = os.environ["DATABASE_URL"]
        # pg_connect sets ``app.niche_id`` GUC so the RLS policy on
        # blueprints sees this tenant's rows. Same pattern as
        # rejection_rag.get_recent_rejection_context.
        with pg_connect(dsn, connect_timeout=5, niche_id=niche_id) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT hook_text
                    FROM blueprints
                    WHERE niche_id = %s
                      AND status = 'PUBLISHED'
                      AND hook_text IS NOT NULL
                      AND hook_text != ''
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (niche_id, limit),
                )
                rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — diversity is augmentation only
        logger.debug(
            "[hook_diversity] DB query failed for niche=%s (%s); fail-open",
            niche_id,
            exc,
        )
        return []

    # rows is a list of 1-tuples; flatten + drop any defensive Nones.
    return [r[0] for r in rows if r and r[0]]


def _embed_or_none(text: str):
    """Embed ``text`` via the shared MiniLM-L6-v2 path; return None on failure.

    Wraps ``hook_embeddings.embed_hook`` in a try/except so an
    embedding-tier failure (sentence-transformers download, sklearn
    missing, etc.) is treated as "no signal" rather than crashing
    the diversity check.
    """
    try:
        from genlab_core.learning.hook_embeddings import embed_hook

        return embed_hook(text)
    except Exception as exc:  # noqa: BLE001 — fail-OPEN
        logger.debug("[hook_diversity] embedding failed for hook (%s); fail-open", exc)
        return None


def _cosine_sim(vec_a, vec_b) -> float:
    """Cosine similarity between two same-dim numpy vectors.

    Returns 0.0 on degenerate inputs (zero magnitude on either side)
    — that's the "no signal" value, which downstream interprets as
    "definitely not too similar" → don't reject.
    """
    try:
        import numpy as np

        a = np.asarray(vec_a, dtype="float32")
        b = np.asarray(vec_b, dtype="float32")
        na = float(np.linalg.norm(a))
        nb = float(np.linalg.norm(b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(a, b) / (na * nb))
    except Exception as exc:  # noqa: BLE001 — fail-OPEN
        logger.debug("[hook_diversity] cosine sim failed (%s); fail-open", exc)
        return 0.0


def _get_history_embeddings(niche_id: str) -> list:
    """Return cached history embeddings for ``niche_id``, refreshing on TTL miss.

    Cache contents: list of embedding vectors (numpy ndarrays, or
    whatever ``embed_hook`` returns for the active tier). Empty list
    on any failure — caller interprets as "no rejection possible".
    """
    now = time.time()
    cached = _DIVERSITY_CACHE.get(niche_id)
    if cached and (now - cached[0]) < _DIVERSITY_TTL_SECONDS:
        return cached[1]

    hooks = _fetch_recent_hooks(niche_id)
    if not hooks:
        _DIVERSITY_CACHE[niche_id] = (now, [])
        return []

    embeddings: list = []
    for h in hooks:
        vec = _embed_or_none(h)
        if vec is not None:
            embeddings.append(vec)

    _DIVERSITY_CACHE[niche_id] = (now, embeddings)
    return embeddings


def reject_if_too_similar(
    candidate_hook: str,
    niche_id: str,
    threshold: float = _DEFAULT_THRESHOLD,
) -> bool:
    """Return True iff ``candidate_hook`` is too close to recent published hooks.

    Args:
        candidate_hook: The hook string to evaluate.
        niche_id: The niche whose recent history we compare against.
        threshold: Cosine-similarity cutoff. Default 0.85. Overridden
            by ``GENLAB_HOOK_DIVERSITY_THRESHOLD`` env var when set.

    Returns:
        ``True`` if any of the last 30 PUBLISHED hooks for this niche
        has cosine similarity > effective threshold. ``False`` in every
        other case, including:
          - env flag unset (default OFF — no-op)
          - empty candidate
          - DB unreachable / no history
          - embedding failed for candidate
          - all history embeddings failed

    Fail-OPEN: every error path returns ``False`` so a broken
    diversity check NEVER blocks hook generation. The caller's
    candidate ranking proceeds unchanged when this returns ``False``.

    Cache: 5-min TTL keyed on ``niche_id``. Tests should call
    ``_reset_cache_for_tests()`` between assertions.
    """
    if not _is_enabled():
        return False

    if not candidate_hook:
        return False

    # Env-var override takes precedence over the explicit kwarg —
    # operator's runtime knob > caller's default. Argument-level
    # overrides remain reachable by callers that pass a non-default
    # value AND haven't set the env var (the common case).
    effective_threshold = (
        _get_threshold()
        if os.environ.get("GENLAB_HOOK_DIVERSITY_THRESHOLD", "").strip()
        else threshold
    )

    history = _get_history_embeddings(niche_id)
    if not history:
        return False

    cand_vec = _embed_or_none(candidate_hook)
    if cand_vec is None:
        return False

    for hist_vec in history:
        sim = _cosine_sim(cand_vec, hist_vec)
        if sim > effective_threshold:
            logger.info(
                "[%s] hook diversity reject: candidate=%r sim=%.3f > threshold=%.3f",
                niche_id,
                candidate_hook[:60],
                sim,
                effective_threshold,
            )
            return True

    return False
