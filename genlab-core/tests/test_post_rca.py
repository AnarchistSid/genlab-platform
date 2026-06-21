"""Pin: post-bomb RCA layer catches structural failure patterns (Lever O).

Pre-2026-06-21 when a post bombed (engagement <30% of niche baseline)
the bandit got negative reward and moved on. No analysis of WHY the
post failed: weak hook, off-niche, bad timing, algorithm suppression?
Operators learned nothing actionable from each failure.

Lever O ships an LLM-as-judge that compares the bombed post against
niche winners + baseline and returns a structured RCA verdict.

These pins lock in:

  Pure-logic (3):
  1. is_underperforming threshold math correct
  2. is_underperforming respects MIN_BASELINE floor
  3. build_rca_context shape stable + truncates long fields

  LLM layer (7):
  4. Default disabled (env unset) → None
  5. Enabled + no API key → None
  6. Enabled + API exception → None (fail open)
  7. Enabled + malformed JSON → None (fail open)
  8. Enabled + valid response → RCAResult with whitelisted cause
  9. Unknown likely_cause from LLM coerces to "unknown" (whitelist)
  10. Confidence clamped to [0, 1] even when LLM emits 1.5
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _bombed_post() -> dict:
    return {
        "blueprint_id": "bp_test_001",
        "niche_id": "gaming",
        "hook_text": "Some generic clickbait hook",
        "title": "Random gaming clip",
        "platform": "instagram",
        "published_at": "2026-06-20T12:00:00Z",
        "engagement_rate": 0.012,
    }


def _winners() -> list[dict]:
    return [
        {"hook_text": "Insane 1v5 clutch from ESL One", "engagement_rate": 0.08},
        {"hook_text": "Pro player reacts to new patch", "engagement_rate": 0.07},
        {"hook_text": "World record speedrun lands today", "engagement_rate": 0.09},
    ]


class TestIsUnderperforming:
    def test_clear_bomb_returns_true(self):
        from genlab_core.learning.post_rca import is_underperforming

        # 40% miss vs baseline=0.05
        assert is_underperforming(0.030, 0.050) is True

    def test_minor_miss_below_threshold_returns_false(self):
        from genlab_core.learning.post_rca import is_underperforming

        # 20% miss vs baseline=0.05 — below default 30% threshold
        assert is_underperforming(0.040, 0.050) is False

    def test_baseline_too_low_returns_false(self):
        """Baselines below _MIN_BASELINE_FOR_RCA (0.01) → don't RCA,
        the comparison is noise."""
        from genlab_core.learning.post_rca import is_underperforming

        # Even a 40% miss isn't meaningful at baseline=0.005
        assert is_underperforming(0.003, 0.005) is False


class TestBuildRCAContext:
    def test_shape_is_stable(self):
        from genlab_core.learning.post_rca import build_rca_context

        ctx = build_rca_context(_bombed_post(), _winners(), 0.05)
        assert "bombed" in ctx
        assert "baseline_rate" in ctx
        assert "winners" in ctx
        assert ctx["baseline_rate"] == 0.05
        assert ctx["bombed"]["blueprint_id"] == "bp_test_001"
        assert ctx["bombed"]["niche_id"] == "gaming"
        assert len(ctx["winners"]) == 3

    def test_winners_capped_at_5(self):
        """Even if 10 winners are passed, only the top 5 reach the LLM
        — cost discipline + prompt length budget."""
        from genlab_core.learning.post_rca import build_rca_context

        many_winners = [{"hook_text": f"winner_{i}", "engagement_rate": 0.05} for i in range(10)]
        ctx = build_rca_context(_bombed_post(), many_winners, 0.05)
        assert len(ctx["winners"]) == 5

    def test_long_fields_truncated(self):
        """Hook + title fields capped at 200 chars so a runaway field
        from a corrupt blueprint can't blow up the prompt budget."""
        from genlab_core.learning.post_rca import build_rca_context

        bombed = _bombed_post()
        bombed["hook_text"] = "x" * 500
        bombed["title"] = "y" * 500
        ctx = build_rca_context(bombed, _winners(), 0.05)
        assert len(ctx["bombed"]["hook"]) <= 200
        assert len(ctx["bombed"]["title"]) <= 200


class TestAnalyzePostDisabled:
    def test_returns_none_when_env_unset(self, monkeypatch):
        from genlab_core.learning.post_rca import analyze_post

        monkeypatch.delenv("GENLAB_POST_RCA_ENABLED", raising=False)
        result = analyze_post(_bombed_post(), _winners(), 0.05)
        assert result is None

    def test_returns_none_when_env_is_zero(self, monkeypatch):
        from genlab_core.learning.post_rca import analyze_post

        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "0")
        assert analyze_post(_bombed_post(), _winners(), 0.05) is None


class TestAnalyzePostFailOpen:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")

    def test_no_api_key_returns_none(self, monkeypatch):
        from genlab_core.learning.post_rca import analyze_post

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert analyze_post(_bombed_post(), _winners(), 0.05) is None

    def test_api_exception_returns_none(self, monkeypatch):
        from genlab_core.learning.post_rca import analyze_post

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        with patch("anthropic.Anthropic", side_effect=RuntimeError("simulated outage")):
            result = analyze_post(_bombed_post(), _winners(), 0.05)
        assert result is None

    def test_malformed_json_returns_none(self, monkeypatch):
        from genlab_core.learning.post_rca import analyze_post

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="not valid json {[}")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = analyze_post(_bombed_post(), _winners(), 0.05)
        assert result is None


class TestAnalyzePostVerdicts:
    @pytest.fixture(autouse=True)
    def _enable(self, monkeypatch):
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

    def test_valid_response_returns_rca_result(self):
        from genlab_core.learning.post_rca import analyze_post

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='{"likely_cause": "weak_hook", "confidence": 0.85, '
                '"reasoning": "Hook is generic template, no specific entity", '
                '"recommendation": "Use story-specific entities in hook"}'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = analyze_post(_bombed_post(), _winners(), 0.05)

        assert result is not None
        assert result.likely_cause == "weak_hook"
        assert result.confidence == 0.85
        assert "generic template" in result.reasoning
        assert "specific entities" in result.recommendation
        assert result.blueprint_id == "bp_test_001"
        assert result.niche_id == "gaming"

    def test_unknown_cause_coerced_to_unknown(self):
        """If the LLM responds with a category not in the whitelist
        (free-text creativity), it's coerced to 'unknown' so downstream
        aggregations stay clean."""
        from genlab_core.learning.post_rca import analyze_post

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='{"likely_cause": "user_demographic_mismatch", '
                '"confidence": 0.7, "reasoning": "x", "recommendation": "y"}'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = analyze_post(_bombed_post(), _winners(), 0.05)

        assert result is not None
        assert result.likely_cause == "unknown"

    def test_confidence_clamped_to_unit_interval(self):
        """LLM emits 1.5 → clamped to 1.0. Negative → clamped to 0.0.
        Downstream consumers can rely on the [0, 1] invariant."""
        from genlab_core.learning.post_rca import analyze_post

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='{"likely_cause": "weak_hook", "confidence": 1.5, '
                '"reasoning": "x", "recommendation": "y"}'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = analyze_post(_bombed_post(), _winners(), 0.05)

        assert result is not None
        assert result.confidence == 1.0  # Clamped from 1.5

    def test_tolerates_code_fence_wrapped_json(self):
        from genlab_core.learning.post_rca import analyze_post

        fake_resp = MagicMock()
        fake_resp.content = [
            MagicMock(
                text='```json\n{"likely_cause": "off_niche", "confidence": 0.6, '
                '"reasoning": "ok", "recommendation": "ok"}\n```'
            )
        ]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        with patch("anthropic.Anthropic", return_value=fake_client):
            result = analyze_post(_bombed_post(), _winners(), 0.05)

        assert result is not None
        assert result.likely_cause == "off_niche"
