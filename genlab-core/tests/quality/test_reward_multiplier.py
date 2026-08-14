"""Pin Phase 4.A session 4 reward-multiplier wire:

  * Flag off → reward passes through unchanged
  * Flag off + None reward → None passes through
  * Flag on + no quality row → passes through
  * Flag on + NULL joint_score → passes through
  * Flag on + joint=0.5 → multiplier 1.0 (unchanged)
  * Flag on + joint=1.0 → multiplier 1.5
  * Flag on + joint=0.0 → multiplier 0.5 (floor, not zero)
  * Flag on + joint out of [0,1] → clipped
  * DB error → fail-open pass-through
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from genlab_core.quality.reward_multiplier import (
    _MAX_MULTIPLIER,
    _MIN_MULTIPLIER,
    apply_quality_multiplier,
    is_enabled,
)


class TestFlagSemantics:
    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", raising=False,
        )
        assert is_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "True", "YES", "yes"])
    def test_truthy_variants(self, monkeypatch, val):
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", val,
        )
        assert is_enabled() is True

    def test_string_zero_still_off(self, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "0",
        )
        assert is_enabled() is False


class TestApplyMultiplier:
    def _conn_with_score(self, score):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "joint_score": score,
        }
        return conn

    def _conn_no_row(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        return conn

    def test_flag_off_passes_through(self, monkeypatch):
        monkeypatch.delenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", raising=False,
        )
        conn = self._conn_with_score(1.0)  # would 1.5x if flag on
        result = apply_quality_multiplier(0.4, "bp-1", conn)
        assert result == 0.4
        # DB never queried when flag off
        conn.execute.assert_not_called()

    def test_none_reward_passes_through(self, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        conn = self._conn_with_score(1.0)
        assert apply_quality_multiplier(None, "bp-1", conn) is None

    def test_no_quality_row_passes_through(self, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        conn = self._conn_no_row()
        result = apply_quality_multiplier(0.4, "bp-1", conn)
        assert result == 0.4

    def test_null_joint_passes_through(self, monkeypatch):
        """When joint_score is NULL (every extractor failed),
        row exists but joint is None. Must not multiply by 0."""
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        conn = self._conn_with_score(None)
        result = apply_quality_multiplier(0.4, "bp-1", conn)
        assert result == 0.4

    def test_joint_0p5_no_change(self, monkeypatch):
        """joint=0.5 → multiplier 1.0 → reward unchanged. This is
        the neutral point of the [0.5, 1.5] mapping."""
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        conn = self._conn_with_score(0.5)
        assert apply_quality_multiplier(0.4, "bp-1", conn) == pytest.approx(0.4)

    def test_joint_1p0_maxes_multiplier(self, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        conn = self._conn_with_score(1.0)
        assert apply_quality_multiplier(0.4, "bp-1", conn) == pytest.approx(0.6)

    def test_joint_0p0_floors_multiplier(self, monkeypatch):
        """joint=0.0 gets FLOORED to 0.5x — critical safety pin.
        Without the floor, a broken scorer would zero out reward
        entirely and poison the bandit posterior with synthetic
        zeros (same class-of-bug as reward_shaper.py:400)."""
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        conn = self._conn_with_score(0.0)
        assert apply_quality_multiplier(0.4, "bp-1", conn) == pytest.approx(0.2)
        # Never zero even with joint=0
        assert apply_quality_multiplier(0.4, "bp-1", conn) > 0

    def test_joint_out_of_range_clipped(self, monkeypatch):
        """Guard against a runaway score outside [0,1] — mult
        clamped to [0.5, 1.5]."""
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        conn_big = self._conn_with_score(2.0)
        assert (
            apply_quality_multiplier(0.4, "bp-1", conn_big)
            == pytest.approx(0.4 * _MAX_MULTIPLIER)
        )
        conn_neg = self._conn_with_score(-0.5)
        assert (
            apply_quality_multiplier(0.4, "bp-1", conn_neg)
            == pytest.approx(0.4 * _MIN_MULTIPLIER)
        )

    def test_db_error_fail_opens(self, monkeypatch):
        monkeypatch.setenv(
            "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED", "1",
        )
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert apply_quality_multiplier(0.4, "bp-1", conn) == 0.4

    def test_multiplier_range_constants(self):
        """Pin the range constants so a drift here is caught."""
        assert _MIN_MULTIPLIER == 0.5
        assert _MAX_MULTIPLIER == 1.5
