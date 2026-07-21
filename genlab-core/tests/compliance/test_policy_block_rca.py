"""Pin tests for compliance/policy_block_rca.py — L2 of the
policy-block learning loop.

Isolates LLM + DB dependencies so the module's contract is
verifiable without network / prod state:

  * flag OFF   → returns [] without touching DB or LLM
  * flag ON + <min_samples rows → returns [] without LLM call
  * flag ON + LLM raises        → returns [] (fail-open)
  * flag ON + LLM returns garbage → returns [] (JSON guard)
  * flag ON + LLM returns valid JSON → structured RCAVerdicts
  * RCAVerdict dataclass rejects invalid category + confidence
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.compliance.policy_block_rca import (
    RCAVerdict,
    VALID_CATEGORIES,
    analyze_recent_policy_blocks,
)

_MOD = "genlab_core.compliance.policy_block_rca"


# ---------------------------------------------------------------------------
# RCAVerdict dataclass — enum + confidence range guards
# ---------------------------------------------------------------------------


class TestRCAVerdictValidation:
    def test_valid_verdict_constructs(self) -> None:
        v = RCAVerdict(
            violation_category="spam_signals",
            confidence=0.85,
            avoid_patterns=["avoid more than 4 hashtags"],
            sample_blueprint_ids=["bp1", "bp2"],
        )
        assert v.confidence == 0.85
        assert v.avoid_patterns == ["avoid more than 4 hashtags"]

    def test_unknown_category_rejected(self) -> None:
        with pytest.raises(ValueError, match="violation_category"):
            RCAVerdict(violation_category="made_up_category", confidence=0.5)

    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            RCAVerdict(violation_category="unknown", confidence=1.5)
        with pytest.raises(ValueError, match="confidence"):
            RCAVerdict(violation_category="unknown", confidence=-0.1)

    def test_all_categories_are_valid(self) -> None:
        # Each documented category must construct without error —
        # catches typos in VALID_CATEGORIES vs prompt vs consumer.
        for cat in VALID_CATEGORIES:
            RCAVerdict(violation_category=cat, confidence=0.5)


# ---------------------------------------------------------------------------
# Flag gate — RCA must NEVER burn tokens without explicit opt-in
# ---------------------------------------------------------------------------


class TestFlagGate:
    def test_flag_off_returns_empty_without_db_call(self, monkeypatch) -> None:
        monkeypatch.delenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", raising=False)
        with patch(f"{_MOD}._load_recent_events") as load:
            result = analyze_recent_policy_blocks("gaming")
        assert result == []
        # Critical: no DB read either — the flag guards ALL side
        # effects, not just the LLM call.
        load.assert_not_called()

    def test_flag_zero_string_is_off(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "0")
        with patch(f"{_MOD}._load_recent_events") as load:
            result = analyze_recent_policy_blocks("gaming")
        assert result == []
        load.assert_not_called()

    def test_empty_niche_id_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        with patch(f"{_MOD}._load_recent_events") as load:
            result = analyze_recent_policy_blocks("")
        assert result == []
        load.assert_not_called()


# ---------------------------------------------------------------------------
# Cold-start floor — fewer than min_samples must NOT call the LLM
# ---------------------------------------------------------------------------


class TestColdStartFloor:
    def test_below_min_samples_skips_llm(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        with (
            patch(f"{_MOD}._load_recent_events", return_value=[{"blueprint_id": "b1"}]),
            patch(f"{_MOD}._parse_llm_response") as parse,
            patch("genlab_core.intelligence.anthropic_client.AnthropicStrategistClient") as client_cls,
        ):
            result = analyze_recent_policy_blocks("gaming", min_samples=3)
        assert result == []
        parse.assert_not_called()
        client_cls.assert_not_called()

    def test_at_min_samples_calls_llm(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        events = [{"blueprint_id": f"b{i}", "platform": "facebook",
                   "hook": "h", "caption_fragment": "c",
                   "hashtag_count": 5, "has_video_url": True,
                   "error_snippet": "code=368"} for i in range(3)]
        fake_result = SimpleNamespace(text="[]", cost_usd=0.001)
        with (
            patch(f"{_MOD}._load_recent_events", return_value=events),
            patch(
                "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient"
            ) as client_cls,
        ):
            client_cls.return_value.generate_report.return_value = fake_result
            result = analyze_recent_policy_blocks("gaming", min_samples=3)
        # LLM was called; empty array response → empty verdict list
        assert result == []
        client_cls.return_value.generate_report.assert_called_once()


# ---------------------------------------------------------------------------
# LLM failure paths — fail-OPEN in every case
# ---------------------------------------------------------------------------


class TestLLMFailureFailsOpen:
    def _events(self, n: int = 3):
        return [
            {"blueprint_id": f"b{i}", "platform": "facebook", "hook": "h",
             "caption_fragment": "c", "hashtag_count": 5,
             "has_video_url": True, "error_snippet": "code=368"}
            for i in range(n)
        ]

    def test_llm_client_raises_returns_empty(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        with (
            patch(f"{_MOD}._load_recent_events", return_value=self._events()),
            patch(
                "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient",
                side_effect=RuntimeError("provider exhausted"),
            ),
        ):
            result = analyze_recent_policy_blocks("gaming")
        assert result == []

    def test_llm_returns_prose_not_json(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        fake_result = SimpleNamespace(text="I think it's spam", cost_usd=0.0)
        with (
            patch(f"{_MOD}._load_recent_events", return_value=self._events()),
            patch(
                "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient"
            ) as client_cls,
        ):
            client_cls.return_value.generate_report.return_value = fake_result
            result = analyze_recent_policy_blocks("gaming")
        assert result == []


# ---------------------------------------------------------------------------
# Happy path — well-formed LLM JSON parses to structured verdicts
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_valid_json_parses_to_verdicts(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        events = [
            {"blueprint_id": f"b{i}", "platform": "facebook", "hook": "h",
             "caption_fragment": "c", "hashtag_count": 8,
             "has_video_url": True, "error_snippet": "code=368"}
            for i in range(3)
        ]
        llm_output = """[
          {
            "violation_category": "spam_signals",
            "confidence": 0.82,
            "avoid_patterns": [
              "avoid more than 4 hashtags",
              "avoid stacking CTAs at end of caption"
            ],
            "sample_blueprint_ids": ["b0", "b1", "b2"]
          }
        ]"""
        fake_result = SimpleNamespace(text=llm_output, cost_usd=0.002)
        with (
            patch(f"{_MOD}._load_recent_events", return_value=events),
            patch(
                "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient"
            ) as client_cls,
        ):
            client_cls.return_value.generate_report.return_value = fake_result
            verdicts = analyze_recent_policy_blocks("gaming")
        assert len(verdicts) == 1
        v = verdicts[0]
        assert v.violation_category == "spam_signals"
        assert v.confidence == pytest.approx(0.82)
        assert "avoid more than 4 hashtags" in v.avoid_patterns
        assert v.sample_blueprint_ids == ["b0", "b1", "b2"]

    def test_json_fenced_in_backticks_still_parses(self, monkeypatch) -> None:
        """Some LLM outputs wrap JSON in ```json ... ``` fences even
        when the prompt asks for raw JSON. The parser must strip them."""
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        events = [{"blueprint_id": f"b{i}", "platform": "facebook",
                   "hook": "h", "caption_fragment": "c",
                   "hashtag_count": 5, "has_video_url": True,
                   "error_snippet": "code=368"} for i in range(3)]
        fenced = '```json\n[{"violation_category":"unknown","confidence":0.3}]\n```'
        fake_result = SimpleNamespace(text=fenced, cost_usd=0.001)
        with (
            patch(f"{_MOD}._load_recent_events", return_value=events),
            patch(
                "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient"
            ) as client_cls,
        ):
            client_cls.return_value.generate_report.return_value = fake_result
            verdicts = analyze_recent_policy_blocks("gaming")
        assert len(verdicts) == 1
        assert verdicts[0].violation_category == "unknown"

    def test_unknown_category_coerced_not_dropped(self, monkeypatch) -> None:
        """LLM emits a category not in VALID_CATEGORIES — the verdict
        should be RETAINED with category coerced to 'unknown'. Dropping
        the row would lose the avoid_patterns which are still useful."""
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        events = [{"blueprint_id": f"b{i}", "platform": "facebook",
                   "hook": "h", "caption_fragment": "c",
                   "hashtag_count": 5, "has_video_url": True,
                   "error_snippet": "code=368"} for i in range(3)]
        llm_output = """[
          {"violation_category": "made_up_by_llm",
           "confidence": 0.7,
           "avoid_patterns": ["avoid X"],
           "sample_blueprint_ids": ["b0"]}
        ]"""
        fake_result = SimpleNamespace(text=llm_output, cost_usd=0.0)
        with (
            patch(f"{_MOD}._load_recent_events", return_value=events),
            patch(
                "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient"
            ) as client_cls,
        ):
            client_cls.return_value.generate_report.return_value = fake_result
            verdicts = analyze_recent_policy_blocks("gaming")
        assert len(verdicts) == 1
        assert verdicts[0].violation_category == "unknown"
        assert verdicts[0].avoid_patterns == ["avoid X"]

    def test_confidence_clamped_to_valid_range(self, monkeypatch) -> None:
        monkeypatch.setenv("GENLAB_POLICY_BLOCK_RCA_ENABLED", "1")
        events = [{"blueprint_id": f"b{i}", "platform": "facebook",
                   "hook": "h", "caption_fragment": "c",
                   "hashtag_count": 5, "has_video_url": True,
                   "error_snippet": "code=368"} for i in range(3)]
        llm_output = """[
          {"violation_category": "spam_signals", "confidence": 1.7,
           "avoid_patterns": ["x"], "sample_blueprint_ids": ["b0"]},
          {"violation_category": "spam_signals", "confidence": -0.5,
           "avoid_patterns": ["y"], "sample_blueprint_ids": ["b1"]}
        ]"""
        fake_result = SimpleNamespace(text=llm_output, cost_usd=0.0)
        with (
            patch(f"{_MOD}._load_recent_events", return_value=events),
            patch(
                "genlab_core.intelligence.anthropic_client.AnthropicStrategistClient"
            ) as client_cls,
        ):
            client_cls.return_value.generate_report.return_value = fake_result
            verdicts = analyze_recent_policy_blocks("gaming")
        assert len(verdicts) == 2
        assert verdicts[0].confidence == 1.0  # clamped from 1.7
        assert verdicts[1].confidence == 0.0  # clamped from -0.5
