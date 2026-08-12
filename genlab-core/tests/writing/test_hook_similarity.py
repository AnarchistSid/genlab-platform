"""Pin the hook similarity primitive.

Threshold + algorithm MUST match `push_to_backlog.py:2406-2423` so
the two sites agree on which hooks are near-dupes. Divergence would
create a detection-vs-action gap.
"""

from __future__ import annotations

import logging

from genlab_core.writing.hook_similarity import (
    MIN_WORDS,
    SIMILARITY_THRESHOLD,
    SimilarityMatch,
    find_most_similar,
    jaccard_similarity,
    log_similarity_signal,
)


class TestJaccard:
    def test_identical_hooks_score_one(self):
        assert jaccard_similarity(
            "the greatest goal of the season",
            "the greatest goal of the season",
        ) == 1.0

    def test_totally_different_score_zero(self):
        assert jaccard_similarity(
            "Elden Ring boss fight goes wrong",
            "New Marvel trailer breaks internet",
        ) == 0.0

    def test_partial_overlap_scores_between(self):
        score = jaccard_similarity(
            "the greatest goal of the season",
            "the greatest play of the season",
        )
        # {the, greatest, of, season} shared (4); union (6): {the,
        # greatest, goal, play, of, season}. 4/6 = 0.667.
        assert 0.6 < score < 0.75

    def test_case_insensitive(self):
        assert jaccard_similarity(
            "THE GREATEST GOAL", "the greatest goal"
        ) == jaccard_similarity(
            "the greatest goal", "the greatest goal"
        )

    def test_too_short_returns_zero(self):
        # Both under MIN_WORDS
        assert jaccard_similarity("Wild play", "Wild moment") == 0.0

    def test_one_too_short_returns_zero(self):
        # One under MIN_WORDS
        assert jaccard_similarity(
            "Wild play", "This is a really wild play"
        ) == 0.0

    def test_empty_inputs(self):
        assert jaccard_similarity("", "anything at all") == 0.0
        assert jaccard_similarity("anything at all", "") == 0.0
        assert jaccard_similarity("", "") == 0.0

    def test_threshold_locked_at_zero_six(self):
        """0.6 is the historical Jaccard threshold from the push_to_
        backlog inline logic (pre-2026-08-12). All three sites (writer
        WARN, writer retry, push drop) now import from here — this
        assertion locks the value so future changes are deliberate."""
        assert SIMILARITY_THRESHOLD == 0.6

    def test_push_to_backlog_uses_shared_module(self):
        """Structural pin: push_to_backlog imports find_most_similar
        rather than reimplementing the Jaccard math inline. Guards
        against a future refactor accidentally re-introducing the
        divergence class-of-bug."""
        import pathlib

        push_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "pipeline"
            / "stages"
            / "push_to_backlog.py"
        )
        src = push_path.read_text()
        assert "from genlab_core.writing.hook_similarity import" in src
        assert "find_most_similar" in src
        # And the old inline Jaccard math should be GONE
        assert "hook_words & existing_words" not in src, (
            "inline Jaccard duplicated in push_to_backlog — should import "
            "from writing.hook_similarity instead"
        )

    def test_min_words_stability(self):
        """MIN_WORDS = 3 avoids the "2-word hooks score 50% on any
        single-word overlap" instability."""
        assert MIN_WORDS >= 3


class TestFindMostSimilar:
    def test_no_recent_hooks(self):
        assert find_most_similar("hello world story", []) is None
        assert find_most_similar("hello world story", set()) is None

    def test_no_hook(self):
        assert find_most_similar("", ["some recent hook"]) is None

    def test_below_threshold_returns_none(self):
        result = find_most_similar(
            "the greatest goal of the season",
            ["something completely unrelated"],
        )
        assert result is None

    def test_above_threshold_returns_match(self):
        result = find_most_similar(
            "the greatest goal of the season",
            ["the greatest play of the season"],
        )
        assert isinstance(result, SimilarityMatch)
        assert result.similarity > 0.6

    def test_picks_highest_of_multiple_matches(self):
        recent = [
            "the greatest play of the season",       # ~0.83
            "the greatest goal of the season ever",  # ~0.86
            "boring unrelated hook",                 # 0.0
        ]
        result = find_most_similar(
            "the greatest goal of the season",
            recent,
        )
        assert result is not None
        assert result.matched_hook == "the greatest goal of the season ever"


class TestLogSimilaritySignal:
    def test_logs_warn_on_near_dupe(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = log_similarity_signal(
                "the greatest goal of the season",
                ["the greatest play of the season"],
                niche_id="sports",
            )
        assert result is not None
        assert any("NEAR_DUPE" in r.message for r in caplog.records)
        assert any("sports" in r.message for r in caplog.records)

    def test_no_log_when_below_threshold(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = log_similarity_signal(
                "totally unique hook here today",
                ["some other unrelated hook exists"],
                niche_id="sports",
            )
        assert result is None
        assert not any("NEAR_DUPE" in r.message for r in caplog.records)

    def test_no_recent_hooks_no_log(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = log_similarity_signal(
                "some hook here today", [], niche_id="anime"
            )
        assert result is None
        assert not any("NEAR_DUPE" in r.message for r in caplog.records)

    def test_log_line_contains_score_and_both_hooks(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_similarity_signal(
                "the greatest goal of the season",
                ["the greatest play of the season"],
                niche_id="sports",
            )
        msg = next(r.message for r in caplog.records if "NEAR_DUPE" in r.message)
        assert "score=" in msg
        assert "emitted=" in msg
        assert "matched=" in msg
        assert "niche=sports" in msg
