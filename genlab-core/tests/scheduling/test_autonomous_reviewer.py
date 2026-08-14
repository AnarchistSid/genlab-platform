"""Pin Phase 5.B session 1 autonomous reviewer augmenter:

  * OutcomeHistory.is_mature / success_rate math
  * fetch_outcome_history fail-opens
  * fetch_meta_grade parses per_type_grades JSONB
  * fetch_meta_grade rejects malformed grades
  * augment: accept + high-success history → confidence up
  * augment: accept + low-success history → confidence down
  * augment: reject + high-success history → contradiction dampen
  * augment: meta A → +0.10; meta F → -0.25
  * augment: immature history → should_escalate=True
  * augment: novel → should_escalate=True regardless of confidence
  * augment: augmented confidence clamped [0, 1]
  * escalation rules per roadmap
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from genlab_core.scheduling.autonomous_reviewer import (
    AugmentedVerdict,
    OutcomeHistory,
    _BUMP_META_A_GRADE,
    _BUMP_OUTCOME_ALIGNED,
    _DAMPEN_META_F_GRADE,
    _DAMPEN_OUTCOME_CONTRADICTS,
    _MIN_OUTCOME_HISTORY_MATURE,
    augment,
    fetch_meta_grade,
    fetch_outcome_history,
)


class TestOutcomeHistoryShape:
    def test_mature_at_min_threshold(self):
        h = OutcomeHistory("arm_add", n_verdicts=_MIN_OUTCOME_HISTORY_MATURE,
                           n_improved=2, n_unchanged=0, n_regressed=1)
        assert h.is_mature is True

    def test_immature_below_min(self):
        h = OutcomeHistory("arm_add", n_verdicts=2,
                           n_improved=1, n_unchanged=0, n_regressed=1)
        assert h.is_mature is False

    def test_success_rate_math(self):
        # 6 improved / (6 improved + 4 regressed) = 0.6
        h = OutcomeHistory("arm_add", n_verdicts=15,
                           n_improved=6, n_unchanged=5, n_regressed=4)
        assert h.success_rate == pytest.approx(0.6)

    def test_success_rate_zero_when_all_unchanged(self):
        h = OutcomeHistory("x", n_verdicts=5,
                           n_improved=0, n_unchanged=5, n_regressed=0)
        assert h.success_rate == 0.0


class TestFetchOutcomeHistory:
    def test_db_error_returns_empty(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        h = fetch_outcome_history(conn, "arm_add")
        assert h.n_verdicts == 0
        assert h.is_mature is False

    def test_row_parsed(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "n": 12, "n_imp": 7, "n_unc": 3, "n_reg": 2,
        }
        h = fetch_outcome_history(conn, "arm_add")
        assert h.n_verdicts == 12
        assert h.n_improved == 7
        assert h.success_rate == pytest.approx(7 / 9)


class TestFetchMetaGrade:
    def test_no_row_returns_none(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = None
        assert fetch_meta_grade(conn, "arm_add") is None

    def test_db_error_returns_none(self):
        conn = MagicMock()
        conn.execute.side_effect = Exception("no DB")
        assert fetch_meta_grade(conn, "arm_add") is None

    def test_dict_jsonb_parsed(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "per_type_grades": {"arm_add": "A", "reward_weight": "C"},
        }
        assert fetch_meta_grade(conn, "arm_add") == "A"

    def test_string_jsonb_parsed(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "per_type_grades": json.dumps({"arm_add": "F"}),
        }
        assert fetch_meta_grade(conn, "arm_add") == "F"

    def test_missing_type_returns_none(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "per_type_grades": {"other_type": "A"},
        }
        assert fetch_meta_grade(conn, "arm_add") is None

    def test_invalid_grade_returns_none(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "per_type_grades": {"arm_add": "Z"},
        }
        assert fetch_meta_grade(conn, "arm_add") is None

    def test_grade_normalized_uppercase(self):
        conn = MagicMock()
        conn.execute.return_value.fetchone.return_value = {
            "per_type_grades": {"arm_add": "a"},
        }
        assert fetch_meta_grade(conn, "arm_add") == "A"


class TestAugment:
    def _conn_with(self, history=None, grade=None):
        """Build a mock conn returning canned outcome + grade."""
        conn = MagicMock()

        def _execute(sql, *args):
            result = MagicMock()
            if "strategist_outcome_verification" in sql:
                if history is None:
                    result.fetchone.return_value = None
                else:
                    result.fetchone.return_value = {
                        "n": history["n"], "n_imp": history["n_imp"],
                        "n_unc": history["n_unc"], "n_reg": history["n_reg"],
                    }
            elif "meta_strategist_reports" in sql:
                if grade is None:
                    result.fetchone.return_value = None
                else:
                    result.fetchone.return_value = {
                        "per_type_grades": {"arm_add": grade},
                    }
            else:
                result.fetchone.return_value = None
            return result

        conn.execute.side_effect = _execute
        return conn

    def test_accept_with_high_success_history_boosts(self):
        history = {"n": 10, "n_imp": 8, "n_unc": 1, "n_reg": 1}  # 89% success
        conn = self._conn_with(history=history)
        v = augment("accept", 0.60, "test", "arm_add", conn)
        assert v.augmented_confidence == pytest.approx(0.60 + _BUMP_OUTCOME_ALIGNED)
        assert any("aligned" in t for t in v.augmentation_trail)

    def test_accept_with_low_success_history_dampens(self):
        history = {"n": 10, "n_imp": 2, "n_unc": 1, "n_reg": 7}  # 22% success
        conn = self._conn_with(history=history)
        v = augment("accept", 0.60, "test", "arm_add", conn)
        assert v.augmented_confidence == pytest.approx(0.60 - _DAMPEN_OUTCOME_CONTRADICTS)
        assert any("contradicts" in t for t in v.augmentation_trail)

    def test_reject_with_high_success_history_dampens(self):
        """Rejecting a proposal type that historically succeeds is
        suspicious — dampen confidence to force operator review."""
        history = {"n": 10, "n_imp": 8, "n_unc": 1, "n_reg": 1}  # 89%
        conn = self._conn_with(history=history)
        v = augment("reject", 0.70, "test", "arm_add", conn)
        assert v.augmented_confidence < 0.70

    def test_meta_grade_a_boosts(self):
        conn = self._conn_with(grade="A")
        v = augment("accept", 0.60, "test", "arm_add", conn)
        assert v.augmented_confidence >= 0.60 + _BUMP_META_A_GRADE - 0.01

    def test_meta_grade_f_dampens(self):
        conn = self._conn_with(grade="F")
        v = augment("accept", 0.80, "test", "arm_add", conn)
        assert v.augmented_confidence == pytest.approx(0.80 - _DAMPEN_META_F_GRADE)

    def test_immature_history_should_escalate(self):
        history = {"n": 2, "n_imp": 1, "n_unc": 0, "n_reg": 1}  # < 3
        conn = self._conn_with(history=history)
        v = augment("accept", 0.90, "test", "arm_add", conn)
        assert v.should_escalate is True

    def test_novel_proposal_should_escalate(self):
        """Even high-confidence + mature history — novel type
        always escalates."""
        history = {"n": 10, "n_imp": 8, "n_unc": 1, "n_reg": 1}
        conn = self._conn_with(history=history)
        v = augment("accept", 0.95, "test", "arm_add", conn, is_novel=True)
        assert v.should_escalate is True

    def test_low_confidence_should_escalate(self):
        """success_rate = 2/(2+8) = 0.20 which hits the ≤0.3
        contradict branch → dampen -0.20 → 0.35."""
        history = {"n": 10, "n_imp": 2, "n_unc": 0, "n_reg": 8}
        conn = self._conn_with(history=history)
        v = augment("accept", 0.55, "test", "arm_add", conn)
        assert v.augmented_confidence < 0.5
        assert v.should_escalate is True

    def test_mature_high_confidence_no_escalate(self):
        history = {"n": 10, "n_imp": 8, "n_unc": 1, "n_reg": 1}
        conn = self._conn_with(history=history, grade="A")
        v = augment("accept", 0.80, "test", "arm_add", conn)
        # Boosted by outcome + meta → well above 0.5, mature, not novel
        assert v.augmented_confidence >= 0.9
        assert v.should_escalate is False

    def test_augmented_confidence_clamped_high(self):
        history = {"n": 10, "n_imp": 10, "n_unc": 0, "n_reg": 0}
        conn = self._conn_with(history=history, grade="A")
        v = augment("accept", 0.95, "test", "arm_add", conn)
        assert v.augmented_confidence <= 1.0

    def test_augmented_confidence_clamped_low(self):
        history = {"n": 10, "n_imp": 0, "n_unc": 0, "n_reg": 10}
        conn = self._conn_with(history=history, grade="F")
        v = augment("accept", 0.10, "test", "arm_add", conn)
        assert v.augmented_confidence >= 0.0


class TestAugmentationConstants:
    def test_outcome_bump_smaller_than_dampen(self):
        """Deliberate asymmetry — a supporting signal shouldn't
        override the base LLM verdict as much as a contradicting
        one dampens it (fail-safe toward operator review)."""
        assert _BUMP_OUTCOME_ALIGNED < _DAMPEN_OUTCOME_CONTRADICTS

    def test_meta_f_dampens_more_than_meta_a_boosts(self):
        """Same asymmetry — F is a strong negative signal from the
        meta-strategist (proposals of this type have been harmful
        overall) so it should override more."""
        assert _DAMPEN_META_F_GRADE > _BUMP_META_A_GRADE
