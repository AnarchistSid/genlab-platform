"""Pin Phase 4.E session 1 content ideator:

  * _parse_ideas_response: valid JSON → list of Ideas
  * _parse_ideas_response: code-fenced JSON stripped
  * _parse_ideas_response: bad JSON → []
  * _parse_ideas_response: dedups titles case-insensitively
  * _parse_ideas_response: clips score to [0, 1]
  * _parse_ideas_response: truncates title/hook_seed/rationale
  * generate_ideas: LLM crash → empty batch
  * generate_ideas: empty LLM text → empty batch
  * generate_ideas: happy path returns populated IdeaBatch
  * source_signals snapshot always present
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from genlab_core.intelligence.content_ideator import (
    Idea,
    IdeaBatch,
    _parse_ideas_response,
    generate_ideas,
)


class TestParseResponse:
    def test_valid_ideas_list(self):
        raw = json.dumps({"ideas": [
            {"title": "A great topic", "hook_seed": "Watch this",
             "rationale": "trend + persona match", "score": 0.75},
            {"title": "Another topic", "hook_seed": "Wow", "score": 0.5},
        ]})
        ideas = _parse_ideas_response(raw)
        assert len(ideas) == 2
        assert ideas[0].title == "A great topic"
        assert ideas[0].score == 0.75

    def test_code_fenced_json_stripped(self):
        raw = '```json\n{"ideas": [{"title": "T", "score": 0.5}]}\n```'
        ideas = _parse_ideas_response(raw)
        assert len(ideas) == 1

    def test_bad_json_returns_empty(self):
        assert _parse_ideas_response("not json {{") == []

    def test_non_dict_root_returns_empty(self):
        assert _parse_ideas_response('["array not object"]') == []

    def test_missing_ideas_key_returns_empty(self):
        assert _parse_ideas_response('{"other": "field"}') == []

    def test_dedups_titles_case_insensitive(self):
        raw = json.dumps({"ideas": [
            {"title": "Same Topic", "score": 0.5},
            {"title": "SAME TOPIC", "score": 0.6},
            {"title": "Different", "score": 0.4},
        ]})
        ideas = _parse_ideas_response(raw)
        assert len(ideas) == 2
        assert ideas[0].title == "Same Topic"
        assert ideas[1].title == "Different"

    def test_clips_score_out_of_range(self):
        raw = json.dumps({"ideas": [
            {"title": "A", "score": 2.5},  # clip to 1.0
            {"title": "B", "score": -0.5},  # clip to 0.0
        ]})
        ideas = _parse_ideas_response(raw)
        assert ideas[0].score == 1.0
        assert ideas[1].score == 0.0

    def test_bad_score_defaults_0p5(self):
        raw = json.dumps({"ideas": [{"title": "A", "score": "not a number"}]})
        ideas = _parse_ideas_response(raw)
        assert ideas[0].score == 0.5

    def test_truncates_long_fields(self):
        raw = json.dumps({"ideas": [{
            "title": "X" * 200,
            "hook_seed": "H" * 100,
            "rationale": "R" * 500,
            "score": 0.5,
        }]})
        ideas = _parse_ideas_response(raw)
        assert len(ideas[0].title) <= 100
        assert len(ideas[0].hook_seed) <= 60
        assert len(ideas[0].rationale) <= 200

    def test_empty_title_skipped(self):
        raw = json.dumps({"ideas": [
            {"title": "", "score": 0.5},
            {"title": "Real", "score": 0.5},
        ]})
        ideas = _parse_ideas_response(raw)
        assert len(ideas) == 1
        assert ideas[0].title == "Real"

    def test_non_dict_entries_skipped(self):
        raw = json.dumps({"ideas": [
            "just a string",
            {"title": "Real", "score": 0.5},
        ]})
        ideas = _parse_ideas_response(raw)
        assert len(ideas) == 1


class TestGenerateIdeas:
    def _fake_client(self, text: str, cost: float = 0.005):
        client = MagicMock()
        result = MagicMock()
        result.text = text
        result.cost_usd = cost
        client.generate_report.return_value = result
        return client

    def test_llm_crash_returns_empty(self):
        client = MagicMock()
        client.generate_report.side_effect = RuntimeError("boom")
        batch = generate_ideas(
            "gaming", {}, ["trend1"], ["comp1"], ["question"], ["hook1"],
            _client=client,
        )
        assert batch.ideas == []
        assert batch.niche_id == "gaming"

    def test_empty_llm_text_returns_empty_batch(self):
        client = self._fake_client("")
        batch = generate_ideas("gaming", {}, [], [], [], [], _client=client)
        assert batch.ideas == []
        # source_signals still present so analyzer knows we tried
        assert "trend_topics_n" in batch.source_signals

    def test_happy_path_returns_ideas(self):
        raw = json.dumps({"ideas": [
            {"title": "Topic A", "hook_seed": "Hook A",
             "rationale": "trend + style match", "score": 0.7},
            {"title": "Topic B", "hook_seed": "Hook B",
             "rationale": "competitor overlap", "score": 0.6},
        ]})
        client = self._fake_client(raw, cost=0.003)
        batch = generate_ideas(
            "gaming", {"name": "TestChannel"},
            ["trend1", "trend2"], ["comp hook"], ["question"],
            ["recent hook"],
            _client=client,
        )
        assert len(batch.ideas) == 2
        assert batch.llm_cost_usd == 0.003
        assert batch.source_signals["trend_topics_n"] == 2
        assert batch.source_signals["competitor_hooks_n"] == 1
        assert batch.source_signals["persona_present"] is True

    def test_source_signals_snapshot(self):
        """Analyzer needs the input snapshot to attribute reward
        back to which signal drove which idea. Must be populated
        even on failure paths."""
        client = MagicMock()
        client.generate_report.side_effect = Exception("network")
        batch = generate_ideas(
            "gaming", None,
            ["t1"], ["c1", "c2"], ["question", "bold_claim"], [],
            _client=client,
        )
        assert batch.source_signals["trend_topics_n"] == 1
        assert batch.source_signals["competitor_hooks_n"] == 2
        assert batch.source_signals["top_styles"] == ["question", "bold_claim"]
        assert batch.source_signals["persona_present"] is False
