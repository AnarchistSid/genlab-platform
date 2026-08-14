"""Pin Phase 4.C session 2 style-guidance writer wire:

  * should_inject_guidance: flag off → False
  * should_inject_guidance: flag on + rollout 0 → False
  * should_inject_guidance: flag on + rollout 100 → True (all)
  * should_inject_guidance is deterministic per story_id
  * _rollout_pct clamps [0, 100]
  * _dice distributes uniformly-enough across 1000 inputs
  * load_latest_guidance handles JSONB string + dict
  * load_latest_guidance fail-opens
  * build_prompt_block: empty styles → empty string
  * build_prompt_block: renders rank/name/reward/n
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.writing.style_guidance import (
    StyleGuidance,
    _dice,
    _rollout_pct,
    build_prompt_block,
    load_latest_guidance,
    should_inject_guidance,
)


class TestFlagAndRollout:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_STYLE_GUIDANCE_ENABLED", raising=False)
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "100")
        assert should_inject_guidance("any-story") is False

    def test_flag_on_zero_rollout_off(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ENABLED", "1")
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "0")
        assert should_inject_guidance("any-story") is False

    def test_flag_on_100_rollout_on(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ENABLED", "1")
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "100")
        assert should_inject_guidance("any-story") is True

    def test_deterministic_per_story_id(self, monkeypatch):
        """Same story always lands in same bucket — critical so A/B
        assignment doesn't flip on re-writes."""
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ENABLED", "1")
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "50")
        results = {should_inject_guidance("story-1") for _ in range(20)}
        assert len(results) == 1  # always the same result


class TestRolloutParsing:
    def test_default_zero(self, monkeypatch):
        monkeypatch.delenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", raising=False)
        assert _rollout_pct() == 0

    def test_valid_int(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "42")
        assert _rollout_pct() == 42

    def test_over_100_clamped(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "500")
        assert _rollout_pct() == 100

    def test_negative_clamped(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "-10")
        assert _rollout_pct() == 0

    def test_non_numeric_returns_0(self, monkeypatch):
        monkeypatch.setenv("GENLAB_STYLE_GUIDANCE_ROLLOUT_PCT", "half")
        assert _rollout_pct() == 0


class TestDice:
    def test_deterministic_per_input(self):
        assert _dice("story-1") == _dice("story-1")

    def test_different_inputs_different_dice(self):
        assert _dice("story-1") != _dice("story-2")

    def test_range_0_to_99(self):
        for i in range(500):
            d = _dice(f"story-{i}")
            assert 0 <= d < 100

    def test_uniform_enough_distribution(self):
        """1000 unique inputs → each 10-percentile bucket has
        50-150 samples (loose bound accommodates hash quirks)."""
        buckets = [0] * 10
        for i in range(1000):
            buckets[_dice(f"story-{i}") // 10] += 1
        assert all(50 <= n <= 150 for n in buckets)


class TestLoadLatestGuidance:
    def test_no_row_returns_empty(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        styles, size = load_latest_guidance(conn, "gaming")
        assert styles == []
        assert size == 0

    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        styles, size = load_latest_guidance(conn, "gaming")
        assert styles == []
        assert size == 0

    def test_valid_list_parses(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "top_styles": [
                {"style_name": "question", "reward_mean": 0.5, "n_plays": 10, "rank": 1},
                {"style_name": "bold_claim", "reward_mean": 0.4, "n_plays": 8, "rank": 2},
            ],
            "sample_size": 18,
        }
        styles, size = load_latest_guidance(conn, "gaming")
        assert len(styles) == 2
        assert styles[0].style_name == "question"
        assert size == 18

    def test_string_jsonb_parsed(self):
        """psycopg sometimes returns JSONB as str — must parse."""
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "top_styles": '[{"style_name": "question", "reward_mean": 0.5, "n_plays": 10, "rank": 1}]',
            "sample_size": 10,
        }
        styles, _ = load_latest_guidance(conn, "gaming")
        assert len(styles) == 1
        assert styles[0].style_name == "question"

    def test_malformed_jsonb_returns_empty(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "top_styles": "not json {{",
            "sample_size": 0,
        }
        styles, _ = load_latest_guidance(conn, "gaming")
        assert styles == []

    def test_skips_malformed_items(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "top_styles": [
                {"style_name": "question", "reward_mean": 0.5, "n_plays": 10, "rank": 1},
                "not a dict",
                {"style_name": "comparison", "reward_mean": "bad_float"},
            ],
            "sample_size": 20,
        }
        styles, _ = load_latest_guidance(conn, "gaming")
        # First one parses; second is str (skipped); third's bad float
        # triggers ValueError → skipped
        assert len(styles) == 1
        assert styles[0].style_name == "question"


class TestBuildPromptBlock:
    def test_empty_returns_empty(self):
        assert build_prompt_block([]) == ""

    def test_renders_rank_name_reward_n(self):
        styles = [
            StyleGuidance(style_name="question", reward_mean=0.42,
                          n_plays=100, rank=1),
            StyleGuidance(style_name="bold_claim", reward_mean=0.35,
                          n_plays=80, rank=2),
        ]
        block = build_prompt_block(styles)
        assert "STYLE-OF-THE-WEEK" in block
        assert "#1 question" in block
        assert "reward=0.420" in block
        assert "n=100" in block
        assert "#2 bold_claim" in block
