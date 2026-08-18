"""Pin chart_data_extract (2026-08-18, task #193):

  * Empty / thin summary → None
  * No API key → None (fail-open)
  * LLM raises → None
  * LLM returns 'null' → None
  * LLM returns malformed JSON → None
  * LLM returns 1 bar → None (needs ≥2)
  * LLM returns valid → ChartData with float bars
  * LLM wraps output in code fence → still parses
  * >7 bars → capped
  * bar with 0 or negative value → excluded
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from genlab_core.media.chart_data_extract import (
    ChartData,
    _parse_response,
    extract_chart_data,
)


class TestParseResponse:
    def test_null_string_returns_none(self):
        assert _parse_response("null") is None
        assert _parse_response("NULL") is None
        assert _parse_response('"null"') is None

    def test_empty_returns_none(self):
        assert _parse_response("") is None

    def test_malformed_json_returns_none(self):
        assert _parse_response("{ not json") is None
        assert _parse_response("some text without json") is None

    def test_valid_two_bars(self):
        raw = json.dumps({
            "title": "AI Adoption",
            "bars": [
                {"label": "2023", "value": 12.5, "unit": "%"},
                {"label": "2024", "value": 35.0, "unit": "%"},
            ],
        })
        r = _parse_response(raw)
        assert isinstance(r, ChartData)
        assert r.title == "AI Adoption"
        assert r.bars == [("2023", 12.5), ("2024", 35.0)]

    def test_one_bar_returns_none(self):
        """<2 bars is not a chart."""
        raw = json.dumps({
            "title": "Solo",
            "bars": [{"label": "X", "value": 42}],
        })
        assert _parse_response(raw) is None

    def test_missing_title_returns_none(self):
        raw = json.dumps({
            "bars": [
                {"label": "A", "value": 1},
                {"label": "B", "value": 2},
            ],
        })
        assert _parse_response(raw) is None

    def test_code_fence_unwrapped(self):
        raw = (
            '```json\n{"title": "T", "bars": ['
            '{"label": "A", "value": 1}, {"label": "B", "value": 2}]}\n```'
        )
        r = _parse_response(raw)
        assert isinstance(r, ChartData)
        assert r.title == "T"

    def test_preamble_stripped(self):
        raw = (
            "Here is the extracted data:\n"
            '{"title": "T", "bars": ['
            '{"label": "A", "value": 1}, {"label": "B", "value": 2}]}'
            "\nHope that helps!"
        )
        r = _parse_response(raw)
        assert isinstance(r, ChartData)

    def test_zero_and_negative_values_excluded(self):
        raw = json.dumps({
            "title": "T",
            "bars": [
                {"label": "A", "value": 0},
                {"label": "B", "value": -5},
                {"label": "C", "value": 100},
                {"label": "D", "value": 200},
            ],
        })
        r = _parse_response(raw)
        assert isinstance(r, ChartData)
        assert r.bars == [("C", 100.0), ("D", 200.0)]

    def test_more_than_seven_bars_capped(self):
        raw = json.dumps({
            "title": "T",
            "bars": [
                {"label": f"L{i}", "value": i + 1}
                for i in range(12)
            ],
        })
        r = _parse_response(raw)
        assert r is not None
        assert len(r.bars) == 7

    def test_non_numeric_value_excluded(self):
        raw = json.dumps({
            "title": "T",
            "bars": [
                {"label": "A", "value": "not a number"},
                {"label": "B", "value": 5},
                {"label": "C", "value": 10},
            ],
        })
        r = _parse_response(raw)
        assert isinstance(r, ChartData)
        assert ("A", 0.0) not in [(a, b) for a, b in r.bars]
        assert len(r.bars) == 2


class TestExtractChartData:
    def test_thin_summary_returns_none(self):
        assert extract_chart_data("") is None
        assert extract_chart_data("short") is None  # <40 chars
        assert extract_chart_data("x" * 39) is None

    def test_no_api_key_returns_none(self):
        """If AnthropicLLMClient reports not-available (no key), return
        None. Prevents pipeline noise on unconfigured environments."""
        mock_client = MagicMock()
        mock_client.is_available = False
        # Extract passes the client through — check it hits the guard
        summary = "x" * 50  # long enough to pass thin-check
        # We patch the LAZY init path
        with patch(
            "genlab_core.writing.llm_client.AnthropicLLMClient",
            return_value=mock_client,
        ):
            assert extract_chart_data(summary) is None

    def test_llm_raises_returns_none(self):
        mock_client = MagicMock()
        mock_client.is_available = True
        mock_client.complete.side_effect = Exception("network down")
        r = extract_chart_data("x" * 50, client=mock_client)
        assert r is None

    def test_llm_null_returns_none(self):
        mock_client = MagicMock()
        mock_client.is_available = True
        mock_client.complete.return_value = "null"
        r = extract_chart_data("x" * 50, client=mock_client)
        assert r is None

    def test_llm_valid_returns_chart_data(self):
        mock_client = MagicMock()
        mock_client.is_available = True
        mock_client.complete.return_value = json.dumps({
            "title": "AI Startup Funding 2024",
            "bars": [
                {"label": "OpenAI", "value": 6.6},
                {"label": "Anthropic", "value": 4.0},
                {"label": "xAI", "value": 6.0},
            ],
        })
        r = extract_chart_data(
            "OpenAI raised $6.6B, Anthropic $4B, xAI $6B in 2024",
            story_title="AI funding roundup",
            client=mock_client,
        )
        assert r is not None
        assert r.title == "AI Startup Funding 2024"
        assert len(r.bars) == 3

    def test_llm_temperature_zero(self):
        """Deterministic extraction: temperature must be 0.0 so the
        same summary produces the same chart across re-runs."""
        mock_client = MagicMock()
        mock_client.is_available = True
        mock_client.complete.return_value = "null"
        extract_chart_data("x" * 50, client=mock_client)
        call_kwargs = mock_client.complete.call_args.kwargs
        assert call_kwargs.get("temperature") == 0.0
