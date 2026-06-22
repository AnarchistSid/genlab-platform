"""Pin: post-bomb RCA FULL wire (analyze_post invocation, 2026-06-22).

PR #444 shipped the trigger-only wire (detects underperformance,
logs a WARN). PR #449 (this) adds the FULL wire: when underperformance
fires, fetch niche baseline + winners + bombed-post fields from
Postgres, then invoke ``post_rca.analyze_post`` for the LLM verdict.

These pins lock in:

  Full-wire behavior (4):
  1. env=1 + bomb detected → ``analyze_post`` called with the fetched
     context
  2. RCA returning a verdict → INFO log with cause / confidence / rec
  3. RCA returning None (LLM fail-open) → no INFO log; bomb signal
     still True; pipeline continues
  4. ``_fetch_rca_context`` raising → bomb signal still True; pipeline
     continues; RCA skipped

  Cost / no-op guarantees (3):
  5. env=0 → no ``_fetch_rca_context`` call, no ``analyze_post`` call
  6. reward above threshold (no bomb) → no context fetch, no LLM call
  7. ``analyze_post`` raising → fail-open; bomb signal still True
"""

from __future__ import annotations

import logging
from unittest.mock import patch

from genlab_core.learning.metric_collector import _check_post_bomb_signal


class _FakeRCAResult:
    """Stand-in for genlab_core.learning.post_rca.RCAResult."""

    def __init__(
        self,
        *,
        likely_cause="weak_hook",
        confidence=0.85,
        reasoning="Generic hook, no specific entity",
        recommendation="Use story-specific entities",
    ):
        self.likely_cause = likely_cause
        self.confidence = confidence
        self.reasoning = reasoning
        self.recommendation = recommendation


class TestFullWireInvocation:
    def test_bomb_detected_invokes_analyze_post(self, monkeypatch, caplog):
        """Pin: env on + bomb signal fires → analyze_post called with the
        fetched context. Pre-PR-449 this just logged the WARN and
        returned; now it ALSO triggers the LLM verdict."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")

        # Stub _fetch_rca_context to return synthetic baseline + winners
        fake_context = (
            0.08,  # baseline_rate
            [{"engagement_rate": 0.15, "hook_text": "winner1", "title": "t1"}],
            {"hook_text": "bombed_hook", "title": "bombed_title"},
        )
        with patch(
            "genlab_core.learning.metric_collector._fetch_rca_context",
            return_value=fake_context,
        ):
            with patch(
                "genlab_core.learning.post_rca.analyze_post",
                return_value=_FakeRCAResult(),
            ) as mock_analyze:
                with caplog.at_level(logging.INFO):
                    result = _check_post_bomb_signal(
                        niche_id="gaming",
                        blueprint_id="bp_bombed",
                        platform="instagram",
                        reward_48h=0.03,  # gaming threshold 0.10, 70% below → bomb
                    )

        assert result is True
        mock_analyze.assert_called_once()
        # Verdict logged at INFO level with cause + confidence
        verdict_logs = [r for r in caplog.records if "RCA verdict" in r.message]
        assert len(verdict_logs) == 1
        assert "cause=weak_hook" in verdict_logs[0].message
        assert "conf=0.85" in verdict_logs[0].message

    def test_rca_returns_none_no_verdict_log_but_bomb_still_logged(self, monkeypatch, caplog):
        """Pin: analyze_post fail-opens to None (no API key, network
        error, etc.) → no verdict log emitted, but the WARN bomb-signal
        log still fires. Caller still sees True; bandit update proceeds."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")
        with patch(
            "genlab_core.learning.metric_collector._fetch_rca_context",
            return_value=(0.08, [], {}),
        ):
            with patch("genlab_core.learning.post_rca.analyze_post", return_value=None):
                with caplog.at_level(logging.INFO):
                    result = _check_post_bomb_signal(
                        niche_id="gaming",
                        blueprint_id="bp_bombed",
                        platform="instagram",
                        reward_48h=0.03,
                    )
        assert result is True  # bomb still True
        # WARN bomb signal still emitted
        assert any("bomb signal" in r.message for r in caplog.records)
        # No verdict log (RCA returned None)
        verdict_logs = [r for r in caplog.records if "RCA verdict" in r.message]
        assert verdict_logs == []

    def test_context_fetch_raises_does_not_block_bomb_signal(self, monkeypatch, caplog):
        """Pin: _fetch_rca_context raising (DB outage, etc.) MUST NOT
        downgrade the bomb detection. The bandit update path runs
        immediately after this function — it cannot fail because of
        an RCA hiccup."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")
        with patch(
            "genlab_core.learning.metric_collector._fetch_rca_context",
            side_effect=RuntimeError("DB outage"),
        ):
            result = _check_post_bomb_signal(
                niche_id="gaming",
                blueprint_id="bp_bombed",
                platform="instagram",
                reward_48h=0.03,
            )
        # Bomb still detected + signal returned, even though RCA failed
        assert result is True


class TestNoOpGuarantees:
    def test_env_disabled_skips_all_lookups(self, monkeypatch):
        """Pin: env unset → no _fetch_rca_context call, no analyze_post.
        Zero LLM cost when the flag is off."""
        monkeypatch.delenv("GENLAB_POST_RCA_ENABLED", raising=False)
        with patch("genlab_core.learning.metric_collector._fetch_rca_context") as mock_fetch:
            with patch("genlab_core.learning.post_rca.analyze_post") as mock_analyze:
                result = _check_post_bomb_signal(
                    niche_id="gaming",
                    blueprint_id="bp_001",
                    platform="instagram",
                    reward_48h=0.001,  # would trigger if env were on
                )
        assert result is False
        mock_fetch.assert_not_called()
        mock_analyze.assert_not_called()

    def test_reward_above_threshold_skips_lookups(self, monkeypatch):
        """Pin: reward above threshold (no bomb) → no context fetch,
        no LLM call. Healthy posts don't pay RCA cost."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")
        with patch("genlab_core.learning.metric_collector._fetch_rca_context") as mock_fetch:
            with patch("genlab_core.learning.post_rca.analyze_post") as mock_analyze:
                result = _check_post_bomb_signal(
                    niche_id="gaming",
                    blueprint_id="bp_healthy",
                    platform="youtube",
                    reward_48h=0.15,  # gaming threshold 0.10, above
                )
        assert result is False
        mock_fetch.assert_not_called()
        mock_analyze.assert_not_called()

    def test_analyze_post_raises_does_not_block(self, monkeypatch):
        """Pin: analyze_post raising mid-call (unexpected SDK error,
        OOM, etc.) MUST NOT downgrade the bomb detection. Caller still
        sees True; bandit update proceeds."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")
        with patch(
            "genlab_core.learning.metric_collector._fetch_rca_context",
            return_value=(0.08, [], {}),
        ):
            with patch(
                "genlab_core.learning.post_rca.analyze_post",
                side_effect=RuntimeError("simulated SDK error"),
            ):
                result = _check_post_bomb_signal(
                    niche_id="gaming",
                    blueprint_id="bp_bombed",
                    platform="instagram",
                    reward_48h=0.03,
                )
        assert result is True


class TestFetchRcaContextNoConnection:
    def test_no_pg_pool_returns_safe_defaults(self):
        """Pin: when DATABASE_URL is unset / pool init failed,
        _fetch_rca_context returns (0.0, [], {}) — caller can still
        proceed with empty context."""
        from genlab_core.learning.metric_collector import _fetch_rca_context

        with patch(
            "genlab_core.learning.metric_collector._get_pg_pool",
            return_value=None,
        ):
            baseline, winners, bombed = _fetch_rca_context("gaming", "bp_001")
        assert baseline == 0.0
        assert winners == []
        assert bombed == {}
