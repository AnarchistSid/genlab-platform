"""Pin: embedding-distance hook diversity rejection.

Companion to ``test_rejection_rag.py`` — same fail-OPEN + cache +
env-gated discipline. This module catches the template-overfit
failure mode proactively (cosine distance) instead of reactively
(growing ``_BANNED_PHRASES`` list in
``writing/llm_hook_generator.py:148-211``).

These pins guard:
- Env-flag default OFF — no-op without ``GENLAB_HOOK_DIVERSITY_ENABLED=1``
- Cosine-similarity threshold rejects near-paraphrases
- Niche isolation — gaming history doesn't affect anime decisions
- Fail-OPEN on DB error (psycopg raise → False, no propagation)
- ``GENLAB_HOOK_DIVERSITY_THRESHOLD`` env override raises/lowers bar
"""

from __future__ import annotations

from unittest.mock import patch


class TestDiversityFlagGate:
    """Default OFF: module is a no-op until operator opts in."""

    def test_diversity_module_no_op_when_flag_unset(self, monkeypatch):
        """Pin: env var unset → always returns False, no DB hit, no
        embedding compute. Mirrors the auto-pause flag pattern
        (default OFF for shadow rollout before enforcement)."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.delenv("GENLAB_HOOK_DIVERSITY_ENABLED", raising=False)
        # Even with a 100% identical hook, flag-off short-circuits to False
        with patch.object(hdc, "_get_history_embeddings") as fetch:
            result = hdc.reject_if_too_similar("Any hook text", "gaming")
        assert result is False
        # And we never even touched the history loader (no DB / no embedding)
        fetch.assert_not_called()

    def test_strict_flag_parsing(self, monkeypatch):
        """Pin: only literal '1' enables. 'true'/'yes'/'on' must NOT
        accidentally enable — avoids ambiguity from operator copy-paste."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        for val in ("true", "yes", "on", "TRUE", "0", ""):
            monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", val)
            assert hdc._is_enabled() is False, f"value {val!r} must NOT enable"
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")
        assert hdc._is_enabled() is True


class TestSimilarityRejection:
    """Cosine similarity above threshold drops the candidate; below
    threshold lets it through."""

    def test_rejects_when_cosine_above_threshold(self, monkeypatch):
        """Pin: identical hook (sim=1.0) is rejected at default threshold 0.85."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")

        # Force a deterministic embedding: every hook maps to the same
        # unit vector so cos_sim = 1.0 between candidate and history.
        import numpy as np

        same_vec = np.array([1.0, 0.0, 0.0], dtype="float32")
        with patch.object(hdc, "_fetch_recent_hooks", return_value=["historic hook"]):
            with patch.object(hdc, "_embed_or_none", return_value=same_vec):
                result = hdc.reject_if_too_similar("identical candidate", "gaming")
        assert result is True

    def test_accepts_when_cosine_below_threshold(self, monkeypatch):
        """Pin: orthogonal vectors (cos_sim = 0.0) are NOT rejected."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")

        import numpy as np

        # Two orthogonal unit vectors → cos_sim = 0.0, far below 0.85
        history_vec = np.array([1.0, 0.0, 0.0], dtype="float32")
        candidate_vec = np.array([0.0, 1.0, 0.0], dtype="float32")

        # _fetch_recent_hooks returns the strings; _embed_or_none gets
        # called once per history hook AND once for the candidate. Use
        # side_effect to return history_vec first, candidate_vec second.
        with patch.object(hdc, "_fetch_recent_hooks", return_value=["historic"]):
            with patch.object(hdc, "_embed_or_none", side_effect=[history_vec, candidate_vec]):
                result = hdc.reject_if_too_similar("new candidate", "gaming")
        assert result is False


class TestNicheIsolation:
    """Per-niche cache + per-niche RLS query: anime's history must
    not influence gaming decisions and vice versa."""

    def test_niche_isolation(self, monkeypatch):
        """Pin: a hook that would be rejected against gaming history
        must be accepted against anime history (the caches are keyed
        independently and the SQL filters by niche_id)."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")

        import numpy as np

        gaming_vec = np.array([1.0, 0.0, 0.0], dtype="float32")
        anime_vec = np.array([0.0, 1.0, 0.0], dtype="float32")
        candidate_vec = np.array([1.0, 0.0, 0.0], dtype="float32")

        # Pre-populate the cache with niche-specific history vectors so
        # we can prove the cache-lookup path is the niche-isolation
        # boundary (skips DB; only embeds the candidate).
        import time

        now = time.time()
        hdc._DIVERSITY_CACHE["gaming"] = (now, [gaming_vec])
        hdc._DIVERSITY_CACHE["anime"] = (now, [anime_vec])

        with patch.object(hdc, "_embed_or_none", return_value=candidate_vec):
            gaming_result = hdc.reject_if_too_similar("X", "gaming")
            anime_result = hdc.reject_if_too_similar("X", "anime")

        assert gaming_result is True, "gaming history matches → must reject"
        assert anime_result is False, "anime history orthogonal → must accept"


class TestFailOpen:
    """Every error path returns False — hook generation MUST continue
    even when DB/embedding infrastructure is broken."""

    def test_fail_open_on_db_failure(self, monkeypatch):
        """Pin: psycopg raising during the history query → False.
        No exception propagates up to the hook generator."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")
        monkeypatch.setenv("DATABASE_URL", "postgresql://fake")

        # Make pg_connect blow up — _fetch_recent_hooks must swallow
        with patch(
            "genlab_core.storage.tenant_context.pg_connect",
            side_effect=RuntimeError("DB down"),
        ):
            # Don't propagate even when called repeatedly
            for _ in range(3):
                result = hdc.reject_if_too_similar("any hook", "gaming")
                assert result is False

    def test_fail_open_on_empty_candidate(self, monkeypatch):
        """Pin: empty candidate string returns False without DB hit."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")

        with patch.object(hdc, "_fetch_recent_hooks") as fetch:
            assert hdc.reject_if_too_similar("", "gaming") is False
        fetch.assert_not_called()

    def test_fail_open_on_no_database_url(self, monkeypatch):
        """Pin: no DATABASE_URL → empty history → False (no rejection
        possible). Same posture as rejection_rag's DB-unset path."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert hdc.reject_if_too_similar("any hook", "gaming") is False


class TestThresholdOverride:
    """Operator can tighten or loosen the cutoff via env var."""

    def test_threshold_overridden_by_env_var(self, monkeypatch):
        """Pin: GENLAB_HOOK_DIVERSITY_THRESHOLD=0.95 raises the bar so
        a hook that WOULD be rejected at 0.85 (sim=0.9) is accepted at
        0.95. Operator's runtime knob overrides the kwarg default."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")

        import numpy as np

        # Two vectors with cos_sim ≈ 0.9 (above 0.85, below 0.95).
        # The dot product of these unit vectors is exactly 0.9.
        history_vec = np.array([1.0, 0.0, 0.0], dtype="float32")
        candidate_vec = np.array([0.9, float(np.sqrt(1.0 - 0.9 * 0.9)), 0.0], dtype="float32")

        # At default 0.85 → REJECT (sim 0.9 > 0.85)
        monkeypatch.delenv("GENLAB_HOOK_DIVERSITY_THRESHOLD", raising=False)
        with patch.object(hdc, "_fetch_recent_hooks", return_value=["h"]):
            with patch.object(hdc, "_embed_or_none", side_effect=[history_vec, candidate_vec]):
                assert hdc.reject_if_too_similar("candidate", "gaming") is True, (
                    "sim 0.9 > default 0.85 must reject"
                )

        # At override 0.95 → ACCEPT (sim 0.9 < 0.95)
        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_THRESHOLD", "0.95")
        with patch.object(hdc, "_fetch_recent_hooks", return_value=["h"]):
            with patch.object(hdc, "_embed_or_none", side_effect=[history_vec, candidate_vec]):
                assert hdc.reject_if_too_similar("candidate", "gaming") is False, (
                    "sim 0.9 < override 0.95 must accept"
                )

    def test_bad_threshold_env_falls_back_to_default(self, monkeypatch):
        """Pin: garbage env value (non-float) falls back to 0.85, not
        a crash. Fail-OPEN posture extends to env parsing."""
        from genlab_core.learning import hook_diversity_cache as hdc

        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_THRESHOLD", "not-a-number")
        assert hdc._get_threshold() == 0.85


class TestCacheTTL:
    """5-min TTL prevents the DB+embedding cost from compounding
    across the 3-10 candidate-evaluation calls per pipeline run."""

    def test_cache_hit_skips_db_and_embedding(self, monkeypatch):
        """Pin: second call within TTL must NOT hit _fetch_recent_hooks
        (DB call) or re-embed the history. Only the candidate embeds
        each call."""
        from genlab_core.learning import hook_diversity_cache as hdc

        hdc._reset_cache_for_tests()
        monkeypatch.setenv("GENLAB_HOOK_DIVERSITY_ENABLED", "1")

        import numpy as np

        # Orthogonal candidate → never rejected, but we still pay the
        # embedding cost per candidate. Pin that the HISTORY fetcher
        # only runs once.
        history_vec = np.array([1.0, 0.0, 0.0], dtype="float32")
        candidate_vec = np.array([0.0, 1.0, 0.0], dtype="float32")

        fetch_calls = []

        def fake_fetch(niche_id, **kw):
            fetch_calls.append(niche_id)
            return ["historic hook"]

        # Side-effect order across BOTH calls:
        #   call 1: history embed, candidate embed
        #   call 2: candidate embed only (history is cached)
        with patch.object(hdc, "_fetch_recent_hooks", side_effect=fake_fetch):
            with patch.object(
                hdc,
                "_embed_or_none",
                side_effect=[history_vec, candidate_vec, candidate_vec],
            ):
                r1 = hdc.reject_if_too_similar("A", "gaming")
                r2 = hdc.reject_if_too_similar("B", "gaming")

        assert r1 is False and r2 is False
        assert len(fetch_calls) == 1, (
            f"history fetcher must run ONCE within TTL — got {len(fetch_calls)} calls"
        )


class TestWireIntoHookGenerator:
    """Source-level pin: the diversity wire is structurally present in
    llm_hook_generator.py and is wrapped in try/except (fail-OPEN)."""

    def test_hook_generator_imports_diversity_module(self):
        """Pin: llm_hook_generator.py imports + calls
        reject_if_too_similar. Source-level pin guards against
        accidental wire removal in future refactors."""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "genlab_core"
            / "writing"
            / "llm_hook_generator.py"
        )
        content = src.read_text()
        assert "reject_if_too_similar" in content, (
            "llm_hook_generator.py is missing the diversity wire. "
            "Add: from genlab_core.learning.hook_diversity_cache import "
            "reject_if_too_similar"
        )
        # The wire must be wrapped — diversity is augmentation, must
        # never block hook generation on infra failure.
        assert "hook diversity check failed" in content, (
            "llm_hook_generator.py is missing the try/except guard "
            "around the diversity call. Diversity must fail-open."
        )
        # The all-candidates-rejected fallback must be present too —
        # otherwise an over-aggressive threshold could starve niches.
        assert "keeping originals" in content, (
            "llm_hook_generator.py is missing the all-candidates-"
            "rejected fallback. Diversity must never block generation."
        )
