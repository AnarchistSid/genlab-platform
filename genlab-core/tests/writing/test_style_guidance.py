"""Pin Phase 4.C session 1 hook style guidance aggregator:

  * _extract_style_from_arm_id parses "style:{niche}:{name}"
  * Ranking is reward_mean DESC, n_plays as tiebreak
  * Below min_plays excluded
  * DB error → empty
  * Empty rows → empty
  * Style with alpha+beta==0 skipped (division guard)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.writing.style_guidance import (
    StyleGuidance,
    _extract_style_from_arm_id,
    compute_top_styles,
)


class TestExtractStyle:
    def test_matches_niche(self):
        assert _extract_style_from_arm_id(
            "style:gaming:question", "gaming",
        ) == "question"

    def test_niche_mismatch_returns_none(self):
        assert _extract_style_from_arm_id(
            "style:anime:question", "gaming",
        ) is None

    def test_wrong_prefix_returns_none(self):
        assert _extract_style_from_arm_id(
            "hour:6:youtube:gaming", "gaming",
        ) is None

    def test_missing_style_returns_none(self):
        assert _extract_style_from_arm_id("style:gaming:", "gaming") is None

    def test_no_colons_returns_none(self):
        assert _extract_style_from_arm_id("style_gaming_question", "gaming") is None


class TestComputeTopStyles:
    def _row(self, arm_id: str, alpha: float, beta: float, n_plays: int):
        return {
            "arm_id": arm_id, "alpha": alpha,
            "beta": beta, "n_plays": n_plays,
        }

    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        styles, total = compute_top_styles(conn, "gaming")
        assert styles == []
        assert total == 0

    def test_no_rows_returns_empty(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        styles, total = compute_top_styles(conn, "gaming")
        assert styles == []
        assert total == 0

    def test_ranks_by_reward_mean_desc(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            # question: mean 0.9 (best)
            self._row("style:gaming:question", 9, 1, 10),
            # comparison: mean 0.5
            self._row("style:gaming:comparison", 5, 5, 10),
            # bold_claim: mean 0.7
            self._row("style:gaming:bold_claim", 7, 3, 10),
        ]
        styles, total = compute_top_styles(conn, "gaming")
        assert [s.style_name for s in styles] == [
            "question", "bold_claim", "comparison",
        ]
        assert [s.rank for s in styles] == [1, 2, 3]
        assert total == 30

    def test_top_n_default_3(self):
        """Even with 5 candidates, return only top 3."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            self._row(f"style:gaming:s{i}", 10 - i, i, 10)
            for i in range(1, 6)
        ]
        styles, _ = compute_top_styles(conn, "gaming")
        assert len(styles) == 3

    def test_n_plays_tiebreaks_equal_reward(self):
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            # question: mean 0.5, n=20
            self._row("style:gaming:question", 10, 10, 20),
            # bold_claim: mean 0.5, n=100 (more evidence, wins tie)
            self._row("style:gaming:bold_claim", 50, 50, 100),
        ]
        styles, _ = compute_top_styles(conn, "gaming")
        assert styles[0].style_name == "bold_claim"
        assert styles[1].style_name == "question"

    def test_non_style_arms_filtered_out(self):
        """hour: and bare-name arms shouldn't leak into style guidance."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            self._row("style:gaming:question", 5, 5, 10),
            # These wouldn't match the SQL LIKE 'style:%%:%%' anyway,
            # but the module-level filter is belt+suspenders.
            self._row("hour:6:youtube:gaming", 5, 5, 10),
            self._row("gameplay_clip", 5, 5, 10),
        ]
        styles, _ = compute_top_styles(conn, "gaming")
        assert len(styles) == 1
        assert styles[0].style_name == "question"

    def test_wrong_niche_style_arms_filtered(self):
        """style:anime:X shouldn't leak into gaming's guidance even
        if the SQL somehow returned it."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            self._row("style:gaming:question", 5, 5, 10),
            self._row("style:anime:comparison", 8, 2, 10),
        ]
        styles, _ = compute_top_styles(conn, "gaming")
        assert [s.style_name for s in styles] == ["question"]

    def test_zero_alpha_beta_skipped(self):
        """Guard against division-by-zero in posterior mean."""
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = [
            self._row("style:gaming:question", 0, 0, 5),
            self._row("style:gaming:comparison", 3, 7, 10),
        ]
        styles, _ = compute_top_styles(conn, "gaming")
        assert [s.style_name for s in styles] == ["comparison"]


class TestStyleGuidanceShape:
    def test_to_dict(self):
        sg = StyleGuidance(
            style_name="question", reward_mean=0.75, n_plays=42, rank=1,
        )
        d = sg.to_dict()
        assert d == {
            "style_name": "question",
            "reward_mean": 0.75,
            "n_plays": 42,
            "rank": 1,
        }
