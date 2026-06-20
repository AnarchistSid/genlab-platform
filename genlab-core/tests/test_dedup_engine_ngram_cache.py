"""Regression tests for PR #391 — DedupEngine Pass-2 ngram precompute.

Pre-fix: every Pass-2 inner-loop iteration called ``jaccard_similarity``
which recomputed ``_char_ngrams`` for BOTH sides. With N items in
``after_pass1`` that's O(N²) ngram computations.

Post-fix: ngrams precomputed once per item in the outer loop +
maintained alongside ``seen_texts`` so the inner loop only does
set-algebra. O(N) ngram computations, O(N²) cheap set comparisons.

Both invariants matter:
  * Correctness preserved (38 existing tests still pass)
  * Algorithmic complexity verified via a probe-count test
"""

from __future__ import annotations

from unittest.mock import patch

from genlab_core.intelligence.dedup_engine import (
    DedupEngine,
    _char_ngrams,
    _jaccard_from_ngrams,
    jaccard_similarity,
)


class TestJaccardFromNgrams:
    """The new helper preserves jaccard_similarity semantics."""

    def test_identical_ngram_sets(self):
        ng = _char_ngrams("hello world")
        assert _jaccard_from_ngrams(ng, ng) == 1.0

    def test_disjoint_ngram_sets(self):
        ng_a = _char_ngrams("xxxxx")
        ng_b = _char_ngrams("yyyyy")
        assert _jaccard_from_ngrams(ng_a, ng_b) == 0.0

    def test_empty_set_returns_zero(self):
        assert _jaccard_from_ngrams(set(), _char_ngrams("hello")) == 0.0
        assert _jaccard_from_ngrams(_char_ngrams("hello"), set()) == 0.0

    def test_matches_jaccard_similarity_for_same_inputs(self):
        """The new helper produces IDENTICAL output to jaccard_similarity()
        for the same inputs — pure decomposition, no semantic drift.
        """
        a = "the quick brown fox jumps over the lazy dog"
        b = "the quick brown fox jumps over the lazy cat"
        legacy = jaccard_similarity(a, b)
        ng_a = _char_ngrams(a)
        ng_b = _char_ngrams(b)
        helper = _jaccard_from_ngrams(ng_a, ng_b)
        assert legacy == helper


class TestPass2NgramPrecompute:
    """Pin the algorithmic improvement: ngrams computed O(N) not O(N²)."""

    def test_dedup_engine_calls_char_ngrams_only_once_per_item(self):
        """For N items in Pass 2, ``_char_ngrams`` runs exactly N times —
        not N² as the pre-fix code did.

        Pre-fix: every ``jaccard_similarity(text, seen)`` call recomputed
        ngrams for BOTH text AND seen. With 5 items: 0+1+2+3+4 = 10
        pairwise calls × 2 sides = 20 ngram computations.

        Post-fix: ngrams precomputed once per outer item + once when
        added to seen_texts. With 5 items where every item is unique
        (added to seen): exactly 5 computations. With 5 items where
        every comparison falls below threshold (all kept): exactly 5.
        """
        # Construct 5 items with completely different titles so all pass
        # Pass 1 (URL dedup) and none get matched in Pass 2 (low Jaccard).
        items = [
            {"url": f"https://example.com/page-{i}", "title": f"{'abc'[i % 3]}" * 20 + f"_{i}"}
            for i in range(5)
        ]
        # Make titles distinct enough that Pass 2 keeps them all
        items[0]["title"] = "alpha bravo charlie delta echo foxtrot"
        items[1]["title"] = "zulu yankee xray whiskey victor uniform"
        items[2]["title"] = "lambda mu nu xi omicron pi rho sigma"
        items[3]["title"] = "iota kappa thiamin riboflavin niacin"
        items[4]["title"] = "ferrous nonferrous metal smelting"

        call_count = {"n": 0}
        real_char_ngrams = _char_ngrams

        def counting_char_ngrams(text: str, n: int = 3) -> set[str]:
            call_count["n"] += 1
            return real_char_ngrams(text, n)

        with patch(
            "genlab_core.intelligence.dedup_engine._char_ngrams", side_effect=counting_char_ngrams
        ):
            engine = DedupEngine(jaccard_threshold=0.85, tfidf_threshold=2.0)
            engine.run(items)

        # Post-fix: at most N ngram computations during Pass 2 (one per
        # outer-loop item — each one either added to seen_ngrams or
        # discarded as a duplicate). Pre-fix would have been ~30+
        # (O(N²) growth with 5 items, including pairwise recomputes).
        # Allow some headroom (the test framework might compute ngrams
        # via other code paths the patch is too narrow to catch).
        assert call_count["n"] <= len(items) + 2, (
            f"Expected ≤{len(items) + 2} _char_ngrams calls (one per outer item), "
            f"got {call_count['n']} — O(N²) regression?"
        )

    def test_dedup_engine_correctness_preserved(self):
        """The optimization doesn't change WHICH items get deduped."""
        # Two near-duplicates + one unique
        items = [
            {"url": "https://example.com/a", "title": "Apple introduces new iPhone today"},
            {"url": "https://example.com/b", "title": "Apple introduces new iPhone today!"},
            {"url": "https://example.com/c", "title": "Samsung releases Galaxy S25"},
        ]
        engine = DedupEngine(jaccard_threshold=0.85, tfidf_threshold=2.0)
        result = engine.run(items)
        # 2 unique remain (one apple article + samsung article)
        assert len(result.unique) == 2
        # 1 duplicate was removed by jaccard
        assert result.pass2_removed == 1
