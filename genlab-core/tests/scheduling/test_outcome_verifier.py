"""Pin the outcome_verifier scaffold interface + classification logic.

The runner (48h scan + auto-rollback) is deferred; these tests pin
the pure-function classification + metric inference + register/
evaluate flow so the follow-up runner can be added without
re-litigating semantics.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from genlab_core.scheduling.outcome_verifier import (
    Verdict, VerificationRecord, Verifier,
)


class _MockMetrics:
    def __init__(self, values: dict[str, float | None] | None = None):
        self.values = values or {}

    def snapshot(self, niche_id: str, metric_name: str) -> float | None:
        return self.values.get(f"{niche_id}:{metric_name}")


class _MockStore:
    def __init__(self):
        self.records: list[VerificationRecord] = []
        self.verdicts: list[tuple[str, float | None, Verdict, bool]] = []

    def insert(self, record):
        self.records.append(record)

    def update_verdict(self, proposal_id, val, verdict, rollback):
        self.verdicts.append((proposal_id, val, verdict, rollback))

    def list_pending(self, older_than):
        return [r for r in self.records if r.applied_at < older_than]


class TestMetricInference:
    def test_arm_add_yields_arm_reward_metric(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        m = v._infer_metric_name(
            {"type": "arm_add",
             "proposed": {"arm_id": "hook_type:anime:character_debate"}},
            "anime",
        )
        assert m == "arm_reward:anime:hook_type:anime:character_debate"

    def test_reward_weight_yields_platform_reward(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        m = v._infer_metric_name(
            {"type": "reward_weight",
             "target": "anime.reward_weight.facebook.shares"},
            "anime",
        )
        assert m == "platform_reward:anime:facebook"

    def test_novelty_rate_yields_coverage(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        m = v._infer_metric_name(
            {"type": "novelty_rate"}, "gaming",
        )
        assert m == "bandit_coverage:gaming"

    def test_gate_threshold_returns_none(self):
        """Too coupled to full-system metrics for register-time
        verification. Handled by phase 2 runner."""
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        m = v._infer_metric_name(
            {"type": "gate_threshold", "target": "anime.auto_approval"},
            "anime",
        )
        assert m is None

    def test_arm_add_missing_arm_id_returns_none(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        assert v._infer_metric_name({"type": "arm_add", "proposed": {}}, "x") is None


class TestClassify:
    def test_improved_when_current_higher_by_10pct(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        verdict, rollback = v._classify(0.20, 0.24)  # +20%
        assert verdict == Verdict.IMPROVED
        assert rollback is False

    def test_regressed_when_current_lower_by_10pct(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        verdict, rollback = v._classify(0.20, 0.16)  # -20%
        assert verdict == Verdict.REGRESSED
        assert rollback is True

    def test_unchanged_within_tolerance(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        verdict, rollback = v._classify(0.20, 0.203)  # +1.5%
        assert verdict == Verdict.UNCHANGED
        assert rollback is False

    def test_no_baseline_high_current_improved(self):
        """New arm case — baseline=None, current > 0.1 = improved."""
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        verdict, rollback = v._classify(None, 0.15)
        assert verdict == Verdict.IMPROVED

    def test_no_baseline_low_current_regressed(self):
        """New arm case — baseline=None, current < 0.05 = regressed."""
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        verdict, rollback = v._classify(None, 0.02)
        assert verdict == Verdict.REGRESSED
        assert rollback is True

    def test_no_current_data_unchanged_no_rollback(self):
        """Metric can't be resolved — don't rollback on missing data."""
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        verdict, rollback = v._classify(0.2, None)
        assert verdict == Verdict.UNCHANGED
        assert rollback is False


class TestRegister:
    def test_register_snapshots_baseline_and_inserts(self):
        metrics = _MockMetrics({
            "anime:platform_reward:anime:facebook": 0.20,
        })
        store = _MockStore()
        v = Verifier(metrics=metrics, store=store)
        rec = v.register(
            proposal_id="uuid:1",
            proposal={
                "type": "reward_weight",
                "target": "anime.reward_weight.facebook.shares",
            },
            niche_id="anime",
            applied_at=datetime.now(UTC),
        )
        assert rec is not None
        assert rec.baseline_value == 0.20
        assert rec.verdict == Verdict.PENDING
        assert len(store.records) == 1

    def test_register_returns_none_for_unregisterable(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        rec = v.register(
            proposal_id="uuid:2",
            proposal={"type": "manual_action", "target": "operator.attention"},
            niche_id="anime",
            applied_at=datetime.now(UTC),
        )
        assert rec is None


class TestEvaluate:
    def test_evaluate_writes_verdict_to_store(self):
        metrics = _MockMetrics({
            "anime:platform_reward:anime:facebook": 0.30,  # improved from 0.20
        })
        store = _MockStore()
        v = Verifier(metrics=metrics, store=store)
        rec = VerificationRecord(
            proposal_id="uuid:3",
            proposal_type="reward_weight",
            proposal_target="anime.reward_weight.facebook.shares",
            niche_id="anime",
            applied_at=datetime.now(UTC) - timedelta(hours=49),
            metric_name="platform_reward:anime:facebook",
            baseline_value=0.20,
            t_plus_48h_value=None,
            verdict=Verdict.PENDING,
            rollback_recommended=False,
        )
        verdict = v.evaluate(rec)
        assert verdict == Verdict.IMPROVED
        assert store.verdicts == [("uuid:3", 0.30, Verdict.IMPROVED, False)]


class TestListPending:
    def test_pending_older_than_48h_returned(self):
        v = Verifier(metrics=_MockMetrics(), store=_MockStore())
        old = VerificationRecord(
            proposal_id="uuid:a",
            proposal_type="reward_weight",
            proposal_target="anime.reward_weight.facebook.shares",
            niche_id="anime",
            applied_at=datetime.now(UTC) - timedelta(hours=50),
            metric_name="x",
            baseline_value=0.2,
            t_plus_48h_value=None,
            verdict=Verdict.PENDING,
            rollback_recommended=False,
        )
        young = VerificationRecord(
            proposal_id="uuid:b",
            proposal_type="reward_weight",
            proposal_target="anime.reward_weight.facebook.shares",
            niche_id="anime",
            applied_at=datetime.now(UTC),
            metric_name="x",
            baseline_value=0.2,
            t_plus_48h_value=None,
            verdict=Verdict.PENDING,
            rollback_recommended=False,
        )
        v._store.records.extend([old, young])
        pending = v.list_pending()
        assert len(pending) == 1
        assert pending[0].proposal_id == "uuid:a"
