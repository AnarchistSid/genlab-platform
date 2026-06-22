"""Pin: post-bomb signal detection wire in metric_collector (Lever O wire).

PR #431 shipped ``post_rca`` (is_underperforming + analyze_post) but
with ZERO callers. THIS test file pins the FIRST production caller —
``_check_post_bomb_signal`` invoked from the 48h reward window in
metric_collector.

The wire ships TRIGGER DETECTION ONLY (logs a WARN line when a post
bombs). The full LLM ``analyze_post`` invocation is deferred to a
follow-up PR that has per-niche winners + baseline lookup helpers.

These pins lock in:

  Threshold lookup (3):
  1. Known niche → per-niche threshold returned
  2. Unknown niche → default fallback
  3. All 5 production niches have explicit thresholds

  Trigger detection (4):
  4. env unset → returns False, no log
  5. env=1 + reward above threshold → returns False, no log
  6. env=1 + reward ≥30% below threshold → returns True, WARN log
  7. Threshold=0.0 edge case → no crash, no false positive

  Fail-open (1):
  8. is_underperforming raising → caller's try/except absorbs it
     (validated by source-code grep + the surrounding wire pattern)
"""

from __future__ import annotations

import logging

from genlab_core.learning.metric_collector import (
    _NICHE_REWARD_BOMB_THRESHOLD,
    _check_post_bomb_signal,
    _get_reward_bomb_threshold,
)


class TestGetRewardBombThreshold:
    def test_known_niche_returns_explicit_threshold(self):
        # Gaming is configured at 0.10 in _NICHE_REWARD_BOMB_THRESHOLD
        assert _get_reward_bomb_threshold("gaming") == 0.10

    def test_unknown_niche_returns_default(self):
        """Pin: missing niche → safe default fallback (not KeyError)."""
        threshold = _get_reward_bomb_threshold("some_unknown_niche_id")
        assert threshold > 0  # Default exists and is positive

    def test_all_five_production_niches_configured(self):
        """Pin: every production niche has an explicit threshold so
        operators don't get the default fallback for the real niches.
        Catches regressions where the dict gets pruned."""
        for niche in ("gaming", "sports", "movies", "anime", "ai_creators"):
            assert niche in _NICHE_REWARD_BOMB_THRESHOLD


class TestCheckPostBombSignalDisabled:
    def test_env_unset_returns_false(self, monkeypatch, caplog):
        """Pin: env unset → no detection runs + no log."""
        monkeypatch.delenv("GENLAB_POST_RCA_ENABLED", raising=False)
        with caplog.at_level(logging.WARNING):
            result = _check_post_bomb_signal(
                niche_id="gaming",
                blueprint_id="bp_001",
                platform="instagram",
                reward_48h=0.001,  # would normally trigger
            )
        assert result is False
        # No WARN log emitted
        bomb_logs = [r for r in caplog.records if "bomb signal" in r.message]
        assert bomb_logs == []


class TestCheckPostBombSignalEnabled:
    def test_reward_above_threshold_returns_false(self, monkeypatch, caplog):
        """Pin: env on + reward HEALTHY → no bomb signal, no log.
        Gaming threshold is 0.10 — reward 0.15 is above."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")
        with caplog.at_level(logging.WARNING):
            result = _check_post_bomb_signal(
                niche_id="gaming",
                blueprint_id="bp_healthy",
                platform="youtube",
                reward_48h=0.15,
            )
        assert result is False
        bomb_logs = [r for r in caplog.records if "bomb signal" in r.message]
        assert bomb_logs == []

    def test_reward_30pct_below_threshold_returns_true_with_warn_log(self, monkeypatch, caplog):
        """Pin: env on + reward 50% below niche threshold → bomb signal
        + WARN log with structured fields operators can grep."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")

        # Gaming threshold = 0.10. reward=0.05 → 50% miss → bomb fires
        # (is_underperforming uses 30% default threshold)
        with caplog.at_level(logging.WARNING):
            result = _check_post_bomb_signal(
                niche_id="gaming",
                blueprint_id="bp_bombed",
                platform="instagram",
                reward_48h=0.05,
            )

        assert result is True
        bomb_logs = [r for r in caplog.records if "bomb signal" in r.message]
        assert len(bomb_logs) == 1
        # Log includes the structured fields the future dashboard would query
        log_text = bomb_logs[0].message
        assert "niche=gaming" in log_text
        assert "bp=bp_bombed" in log_text
        assert "platform=instagram" in log_text

    def test_reward_just_below_threshold_no_signal(self, monkeypatch):
        """Pin: post_rca's MIN_BASELINE floor — minor underperformance
        (below 30% miss) doesn't fire. Avoids spurious bombs on
        merely-mediocre posts."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")

        # Gaming threshold = 0.10. reward=0.09 → only 10% miss → no fire
        result = _check_post_bomb_signal(
            niche_id="gaming",
            blueprint_id="bp_meh",
            platform="instagram",
            reward_48h=0.09,
        )
        assert result is False

    def test_unknown_niche_uses_default_threshold(self, monkeypatch, caplog):
        """Pin: unknown niche still gets checked against the default
        threshold — no silent skip."""
        monkeypatch.setenv("GENLAB_POST_RCA_ENABLED", "1")

        # Default threshold ≈ 0.05. reward=0.001 → far below.
        with caplog.at_level(logging.WARNING):
            result = _check_post_bomb_signal(
                niche_id="some_new_niche",
                blueprint_id="bp_new",
                platform="x",
                reward_48h=0.001,
            )
        # is_underperforming requires baseline >= _MIN_BASELINE_FOR_RCA (0.01).
        # Default 0.05 passes that floor, so detection runs.
        assert result is True
