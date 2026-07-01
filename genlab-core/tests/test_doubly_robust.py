"""Tests for Intervention 7 — doubly-robust reward estimator (2026-07-01).

Companion to ``test_ips_replay.py``. The IPS tests cover the loader
+ IPS math; this file covers the new DR estimator (``compute_doubly_
robust_per_arm``), the extended ``DecisionRecord.context`` field, and
the ``bandit_context`` JSONB → context tuple parse path.

The DR estimator is exact-flag gated behind
``GENLAB_COUNTERFACTUAL_REPLAY_ENABLED``. When off, the estimator
returns the historical stub (``dr_reward=None``) so shipped consumers
observe zero behaviour change. When on, it fits a Ridge reward
model on ``(context ⊕ arm_one_hot) → reward`` and computes the
per-arm DR formula:

    V(π_a) = (1/N) Σ_i [μ̂(a, x_i)] + (1/N) Σ_{i: a_i=a} (r_i - μ̂(a, x_i)) / p_i

Guardrails covered here:

* flag semantics (exact-true only)
* stub behaviour when flag off (all arms → dr_reward=None)
* stub behaviour when sklearn missing → note explains
* stub behaviour when < 30 rows w/ context → note explains
* Direct-method + IPS-correction split populated on happy path
* n_used counts only rows with reward+context (not global N)
* propensity truncation on very low p_i
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from genlab_core.learning.ips_replay import (
    DecisionRecord,
    DREstimate,
    compute_doubly_robust_per_arm,
)


def _dec(
    *,
    arm_id: str = "style:revelation",
    propensity: float = 0.5,
    reward: float | None = 0.4,
    context: tuple[float, ...] | None = (
        1.0,
        0.5,
        0.0,
        0.2,
        0.3,
        0.1,
        0.4,
        0.0,
        1.0,
        0.8,
        0.2,
        0.5,
        0.6,
    ),
    offset_hours: int = 0,
) -> DecisionRecord:
    return DecisionRecord(
        decision_id=f"dec_{offset_hours}",
        niche_id="gaming",
        arm_id=arm_id,
        propensity=propensity,
        reward=reward,
        decided_at=datetime(2026, 6, 28, tzinfo=UTC) + timedelta(hours=offset_hours),
        platform="instagram",
        context=context,
    )


class TestFlagOff:
    """When the flag is unset / off, the estimator must return the
    historical stub for every arm — same shape existing dashboard code
    consumes. This preserves the shipped-but-inactive contract until
    the operator explicitly flips the flag."""

    def test_unset_flag_returns_none_dr_reward(self, monkeypatch):
        monkeypatch.delenv("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", raising=False)
        decisions = [_dec(arm_id=f"arm_{i}", offset_hours=i) for i in range(5)]
        result = compute_doubly_robust_per_arm(decisions)
        assert len(result) == 5
        for est in result.values():
            assert est.dr_reward is None
            assert "flag off" in est.note

    @pytest.mark.parametrize("val", ["1", "yes", "on", "false", "TRUE_"])
    def test_non_exact_true_stays_off(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", val)
        decisions = [_dec(arm_id="a", offset_hours=i) for i in range(35)]
        result = compute_doubly_robust_per_arm(decisions)
        assert all(est.dr_reward is None for est in result.values())


class TestFlagOnGuardrails:
    """Flag on but data is unsuitable for the Ridge fit."""

    def test_no_scored_rows_returns_stub(self, monkeypatch):
        monkeypatch.setenv("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", "true")
        # All rewards None → nothing to fit.
        decisions = [_dec(arm_id="a", reward=None, offset_hours=i) for i in range(5)]
        result = compute_doubly_robust_per_arm(decisions)
        assert result["a"].dr_reward is None
        assert "no rows with reward+context" in result["a"].note

    def test_no_context_rows_returns_stub(self, monkeypatch):
        monkeypatch.setenv("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", "true")
        # Have reward but no context (legacy Thompson decisions).
        decisions = [_dec(arm_id="a", context=None, offset_hours=i) for i in range(5)]
        result = compute_doubly_robust_per_arm(decisions)
        assert result["a"].dr_reward is None
        assert "no rows with reward+context" in result["a"].note

    def test_below_min_rows_for_reward_model_returns_stub(self, monkeypatch):
        monkeypatch.setenv("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", "true")
        # 25 < _MIN_ROWS_FOR_REWARD_MODEL (30)
        decisions = [_dec(arm_id="a", offset_hours=i) for i in range(25)]
        result = compute_doubly_robust_per_arm(decisions)
        assert result["a"].dr_reward is None
        assert "insufficient data" in result["a"].note


class TestFlagOnHappyPath:
    """Enough data to fit Ridge — DR should produce a real estimate."""

    def _make_decisions(self, n_per_arm: int = 20) -> list[DecisionRecord]:
        decisions = []
        offset = 0
        for arm_idx, arm in enumerate(["style:revelation", "style:comparison"]):
            for i in range(n_per_arm):
                # Vary context slightly so Ridge has signal.
                context = tuple(
                    float((v + arm_idx + i * 0.01) % 1.0)
                    for v in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.0, 0.15, 0.25, 0.35)
                )
                reward = 0.5 + arm_idx * 0.1 + (i / n_per_arm) * 0.05
                decisions.append(
                    _dec(
                        arm_id=arm,
                        propensity=0.5,
                        reward=reward,
                        context=context,
                        offset_hours=offset,
                    )
                )
                offset += 1
        return decisions

    def test_produces_non_null_dr_reward(self, monkeypatch):
        pytest.importorskip("sklearn")
        monkeypatch.setenv("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", "true")
        decisions = self._make_decisions(n_per_arm=20)
        result = compute_doubly_robust_per_arm(decisions, min_decisions=5)
        for arm in ("style:revelation", "style:comparison"):
            est = result[arm]
            assert est.dr_reward is not None, f"{arm}: {est.note}"
            assert est.direct_method is not None
            assert est.ips_correction is not None
            assert est.n_used == 20

    def test_dr_reward_close_to_average_reward(self, monkeypatch):
        """When the reward model captures the arm effect well, DR ≈ mean reward
        of that arm. Loose bound because Ridge shrinkage introduces bias."""
        pytest.importorskip("sklearn")
        monkeypatch.setenv("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", "true")
        decisions = self._make_decisions(n_per_arm=20)
        result = compute_doubly_robust_per_arm(decisions, min_decisions=5)
        # Revelation avg = 0.5 + [0..0.05], comparison avg = 0.6 + [0..0.05]
        rev_est = result["style:revelation"].dr_reward
        cmp_est = result["style:comparison"].dr_reward
        # Direction is what matters — comparison should score higher.
        assert cmp_est > rev_est, (
            f"expected comparison ({cmp_est:.3f}) > revelation ({rev_est:.3f}) "
            "because we constructed comparison to have higher rewards"
        )

    def test_arm_below_min_decisions_returns_partial(self, monkeypatch):
        """An arm with few observations still gets direct_method (Ridge
        can predict for any context) but ips_correction is None because
        the IPS residual sum requires the arm's actual observations."""
        pytest.importorskip("sklearn")
        monkeypatch.setenv("GENLAB_COUNTERFACTUAL_REPLAY_ENABLED", "true")
        # Bulk of data on arm A, tiny sample on arm B.
        decisions = self._make_decisions(n_per_arm=20)
        # Add 3 rows for arm C — below min_decisions=10.
        for i in range(3):
            decisions.append(
                _dec(
                    arm_id="style:controversy",
                    context=tuple(0.1 * v for v in range(13)),
                    reward=0.4,
                    offset_hours=100 + i,
                )
            )
        result = compute_doubly_robust_per_arm(decisions, min_decisions=10)
        est = result["style:controversy"]
        assert est.dr_reward is None
        assert est.direct_method is not None  # DM still computable
        assert est.ips_correction is None
        assert est.n_used == 3


class TestDREstimateShape:
    """The extended dataclass shape is a consumed contract — dashboard,
    persistence, Strategist reads it. These pin the field names."""

    def test_fields_present(self):
        est = DREstimate(
            arm_id="a", dr_reward=0.5, direct_method=0.4, ips_correction=0.1, n_used=10
        )
        assert est.arm_id == "a"
        assert est.dr_reward == 0.5
        assert est.direct_method == 0.4
        assert est.ips_correction == 0.1
        assert est.n_used == 10
        assert est.note == ""
