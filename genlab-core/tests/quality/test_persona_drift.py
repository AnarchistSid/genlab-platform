"""Pin Phase 4.D persona drift detector:

  * _persona_hash is deterministic + changes with content
  * _parse_response handles JSON / code-fenced JSON / bad input
  * _parse_response clips score to [0, 1]
  * _parse_response caps reasons to 5
  * compute_drift: empty hook → ok=False reason=empty_hook
  * compute_drift: no persona → ok=False reason=no_persona
  * compute_drift: happy path with fake client returns DriftResult
  * compute_drift: LLM crash → ok=False llm_failed
  * compute_drift: empty LLM text → ok=False llm_empty
  * ALERT_THRESHOLD == 0.6 (roadmap constant)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genlab_core.quality.persona_drift import (
    ALERT_THRESHOLD,
    DriftResult,
    _parse_response,
    _persona_hash,
    compute_drift,
    load_persona,
)


class TestConstants:
    def test_alert_threshold_matches_roadmap(self):
        assert ALERT_THRESHOLD == 0.6


class TestPersonaHash:
    def test_deterministic(self):
        p = {"name": "test", "voice": {"formality": 0.5}}
        assert _persona_hash(p) == _persona_hash(p)

    def test_key_order_invariant(self):
        p1 = {"a": 1, "b": 2}
        p2 = {"b": 2, "a": 1}
        assert _persona_hash(p1) == _persona_hash(p2)

    def test_changes_with_content(self):
        p1 = {"voice": {"formality": 0.5}}
        p2 = {"voice": {"formality": 0.8}}
        assert _persona_hash(p1) != _persona_hash(p2)

    def test_returns_16_hex_chars(self):
        p = {"name": "test"}
        h = _persona_hash(p)
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)


class TestParseResponse:
    def test_valid_json(self):
        raw = '{"drift_score": 0.75, "reasons": ["good tone"]}'
        score, reasons = _parse_response(raw)
        assert score == 0.75
        assert reasons == ["good tone"]

    def test_code_fenced_json(self):
        raw = '```json\n{"drift_score": 0.5, "reasons": []}\n```'
        score, reasons = _parse_response(raw)
        assert score == 0.5
        assert reasons == []

    def test_bad_json_returns_none(self):
        score, reasons = _parse_response("not json {{")
        assert score is None
        assert reasons == []

    def test_score_out_of_range_returns_none(self):
        """Scores outside [0, 1] indicate confused LLM output —
        reject rather than clip so downstream can retry."""
        for raw in ('{"drift_score": -0.1}', '{"drift_score": 1.5}'):
            score, _ = _parse_response(raw)
            assert score is None

    def test_missing_score_returns_none(self):
        score, _ = _parse_response('{"reasons": []}')
        assert score is None

    def test_reasons_capped_at_5(self):
        raw = '{"drift_score": 0.5, "reasons": ["r1", "r2", "r3", "r4", "r5", "r6", "r7"]}'
        _, reasons = _parse_response(raw)
        assert len(reasons) == 5

    def test_non_list_reasons_becomes_empty(self):
        raw = '{"drift_score": 0.5, "reasons": "not a list"}'
        _, reasons = _parse_response(raw)
        assert reasons == []


class TestComputeDrift:
    def _fake_client(self, text: str, cost: float = 0.001):
        client = MagicMock()
        result = MagicMock()
        result.text = text
        result.cost_usd = cost
        client.generate_report.return_value = result
        return client

    @patch("genlab_core.quality.persona_drift.load_persona")
    def test_empty_hook_ok_false(self, mock_load):
        result = compute_drift("", "gaming")
        assert result.ok is False
        assert result.reason_code == "empty_hook"
        mock_load.assert_not_called()

    @patch("genlab_core.quality.persona_drift.load_persona")
    def test_no_persona_ok_false(self, mock_load):
        mock_load.return_value = None
        result = compute_drift("some hook", "gaming")
        assert result.ok is False
        assert result.reason_code == "no_persona"

    @patch("genlab_core.quality.persona_drift.load_persona")
    def test_happy_path_returns_score(self, mock_load):
        mock_load.return_value = {"name": "TestBrand", "voice": {}}
        client = self._fake_client(
            '{"drift_score": 0.82, "reasons": ["matches formality"]}'
        )
        result = compute_drift("Great hook", "gaming", _client=client)
        assert result.ok is True
        assert result.drift_score == 0.82
        assert result.reasons == ["matches formality"]
        assert result.persona_hash  # populated
        assert result.llm_cost_usd == 0.001

    @patch("genlab_core.quality.persona_drift.load_persona")
    def test_llm_crash_ok_false(self, mock_load):
        mock_load.return_value = {"name": "TestBrand"}
        client = MagicMock()
        client.generate_report.side_effect = RuntimeError("network down")
        result = compute_drift("hook", "gaming", _client=client)
        assert result.ok is False
        assert result.reason_code.startswith("llm_failed:")

    @patch("genlab_core.quality.persona_drift.load_persona")
    def test_empty_llm_response_ok_false(self, mock_load):
        """Budget gate produces empty result — treated as skip
        not crash."""
        mock_load.return_value = {"name": "TestBrand"}
        client = self._fake_client("")
        result = compute_drift("hook", "gaming", _client=client)
        assert result.ok is False
        assert result.reason_code == "llm_empty"

    @patch("genlab_core.quality.persona_drift.load_persona")
    def test_unparseable_llm_response_ok_false(self, mock_load):
        mock_load.return_value = {"name": "TestBrand"}
        client = self._fake_client("this is prose not JSON")
        result = compute_drift("hook", "gaming", _client=client)
        assert result.ok is False
        assert result.reason_code == "llm_unparseable"
