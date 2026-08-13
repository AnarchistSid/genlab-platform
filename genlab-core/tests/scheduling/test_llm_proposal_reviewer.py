"""Pin the LLM proposal reviewer scaffold.

Tests cover:
  * is_enabled() env-flag semantics
  * build_user_prompt shape (includes required context sections)
  * parse_verdict handles well-formed + malformed + fenced JSON
  * Reviewer.review() abstains on LLM errors + parses successful
    verdicts through the pipeline
  * Confidence thresholds are exposed at module boundary
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from genlab_core.scheduling.llm_proposal_reviewer import (
    CONFIDENCE_THRESHOLD_ACCEPT, CONFIDENCE_THRESHOLD_REJECT, ReviewVerdict,
    Reviewer, build_user_prompt, is_enabled, parse_verdict,
)


class TestFlagGate:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_LLM_REVIEWER_ENABLED", raising=False)
        assert not is_enabled()

    def test_enabled_when_flag_set(self, monkeypatch):
        for val in ("1", "true", "True", "yes", "ON"):
            monkeypatch.setenv("GENLAB_LLM_REVIEWER_ENABLED", val)
            assert is_enabled()

    def test_random_string_disabled(self, monkeypatch):
        monkeypatch.setenv("GENLAB_LLM_REVIEWER_ENABLED", "maybe")
        assert not is_enabled()


class TestBuildUserPrompt:
    def test_includes_all_context_sections(self):
        prompt = build_user_prompt(
            {
                "type": "arm_add",
                "target": "anime.bandit.arms",
                "urgency": "this_week",
                "risk": "low",
                "current": "no character_debate arm",
                "proposed": {"arm_id": "hook_type:anime:character_debate"},
                "reasoning": "consensus across 10 weeks",
            },
            "anime",
            {"total_arms": 42, "avg_reward_fb_7d": 0.484},
        )
        assert "NICHE: anime" in prompt
        assert "PROPOSAL TYPE: arm_add" in prompt
        assert "TARGET: anime.bandit.arms" in prompt
        assert "URGENCY: this_week" in prompt
        assert "consensus across 10 weeks" in prompt
        assert "total_arms" in prompt
        assert "Decide: accept, reject, or abstain" in prompt

    def test_truncates_long_fields(self):
        long = "A" * 5000
        prompt = build_user_prompt(
            {"type": "manual_action", "current": long, "proposed": long},
            "anime", {},
        )
        # Prompt should be bounded — combined length ~2-3k for reasonable
        # Haiku cost. Give some slack.
        assert len(prompt) < 4_000


class TestParseVerdict:
    def test_well_formed_json(self):
        v = parse_verdict(json.dumps({
            "decision": "accept",
            "confidence": 0.85,
            "reason": "high consensus, low risk",
        }))
        assert v.decision == "accept"
        assert v.confidence == 0.85
        assert v.reason == "high consensus, low risk"

    def test_markdown_fenced_json(self):
        """LLM often wraps output in ```json ... ``` blocks."""
        raw = "```json\n" + json.dumps({
            "decision": "reject",
            "confidence": 0.9,
            "reason": "scope violation",
        }) + "\n```"
        v = parse_verdict(raw)
        assert v.decision == "reject"
        assert v.confidence == 0.9

    def test_malformed_json_abstains(self):
        v = parse_verdict("this is not json")
        assert v.decision == "abstain"
        assert v.confidence == 0.0

    def test_unknown_decision_abstains(self):
        v = parse_verdict(json.dumps({
            "decision": "yolo", "confidence": 0.9,
        }))
        assert v.decision == "abstain"

    def test_confidence_clamped_to_range(self):
        v = parse_verdict(json.dumps({
            "decision": "accept", "confidence": 5.0,
        }))
        assert v.confidence == 1.0
        v = parse_verdict(json.dumps({
            "decision": "accept", "confidence": -0.5,
        }))
        assert v.confidence == 0.0

    def test_non_numeric_confidence_defaults_zero(self):
        v = parse_verdict(json.dumps({
            "decision": "accept", "confidence": "high",
        }))
        assert v.confidence == 0.0

    def test_risk_flags_parsed(self):
        v = parse_verdict(json.dumps({
            "decision": "accept", "confidence": 0.8,
            "risk_flags": ["small_sample", "recent_arm"],
        }))
        assert v.risk_flags == ("small_sample", "recent_arm")

    def test_missing_reason_ok(self):
        v = parse_verdict(json.dumps({
            "decision": "accept", "confidence": 0.8,
        }))
        assert v.reason == ""


class TestReviewer:
    def test_review_returns_verdict_on_success(self):
        client = MagicMock()
        client.review.return_value = json.dumps({
            "decision": "accept", "confidence": 0.9, "reason": "ok",
        })
        r = Reviewer(client)
        v = r.review({"type": "arm_add"}, "anime", {})
        assert v.decision == "accept"
        assert v.confidence == 0.9

    def test_review_abstains_on_llm_error(self):
        client = MagicMock()
        client.review.side_effect = RuntimeError("anthropic 429")
        r = Reviewer(client)
        v = r.review({"type": "arm_add"}, "anime", {})
        assert v.decision == "abstain"
        assert "llm_call_failed" in v.reason

    def test_review_abstains_on_malformed_output(self):
        client = MagicMock()
        client.review.return_value = "not valid json"
        r = Reviewer(client)
        v = r.review({"type": "arm_add"}, "anime", {})
        assert v.decision == "abstain"


class TestModuleContract:
    def test_thresholds_exposed(self):
        assert 0.5 <= CONFIDENCE_THRESHOLD_ACCEPT <= 0.95
        assert 0.5 <= CONFIDENCE_THRESHOLD_REJECT <= 0.95

    def test_system_prompt_mentions_rule_23(self):
        from genlab_core.scheduling.llm_proposal_reviewer import SYSTEM_PROMPT
        assert "TikTok" in SYSTEM_PROMPT
        assert "rule #23" in SYSTEM_PROMPT

    def test_system_prompt_forbids_paid_boost(self):
        from genlab_core.scheduling.llm_proposal_reviewer import SYSTEM_PROMPT
        assert "paid boost" in SYSTEM_PROMPT.lower()

    def test_system_prompt_requires_json_output(self):
        from genlab_core.scheduling.llm_proposal_reviewer import SYSTEM_PROMPT
        assert "JSON" in SYSTEM_PROMPT
