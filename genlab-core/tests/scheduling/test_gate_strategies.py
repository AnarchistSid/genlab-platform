"""Tests for genlab_core.scheduling.gate_strategies (D2.7a, AUTO #2)."""

from __future__ import annotations

import pytest

from genlab_core.scheduling.auto_approval_gate import AutoApprovalDecision
from genlab_core.scheduling.gate_strategies import (
    StrategyConfig,
    apply_strategies,
    apply_strategy_b,
    apply_strategy_e,
)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def approved_decision():
    """A baseline-approved decision with mid-range confidence."""
    return AutoApprovalDecision(
        approved=True,
        confidence=0.72,
        passed_checks=["has_video", "has_hook", "qc_passed", "composite_score"],
        failed_checks=[],
        reasons=["base reasons"],
    )


@pytest.fixture
def rejected_decision():
    return AutoApprovalDecision(
        approved=False,
        confidence=0.20,
        passed_checks=["has_video"],
        failed_checks=["composite_score"],
        reasons=["base rejected reasons"],
    )


@pytest.fixture
def blueprint():
    return {
        "id": "bp-test",
        "niche_id": "gaming",
        "arm_id": "gameplay_clip",
        "hook_text": "x",
        "extra": {"visual_paths": ["/x.mp4"]},
    }


# ── StrategyConfig parsing ────────────────────────────────────────────


class TestStrategyConfigFromYaml:
    def test_empty_dict_is_all_off(self):
        c = StrategyConfig.from_yaml_dict({})
        assert c.bandit_boost_enabled is False
        assert c.agreement_floor_enabled is False

    def test_none_block_is_all_off(self):
        c = StrategyConfig.from_yaml_dict(None)
        assert c.bandit_boost_enabled is False
        assert c.agreement_floor_enabled is False

    def test_bandit_boost_yaml(self):
        c = StrategyConfig.from_yaml_dict(
            {"bandit_boost": {"enabled": True, "min_plays": 30, "boost_max": 0.05}}
        )
        assert c.bandit_boost_enabled is True
        assert c.bandit_min_plays == 30
        assert c.bandit_boost_max == 0.05

    def test_agreement_floor_yaml(self):
        c = StrategyConfig.from_yaml_dict(
            {
                "agreement_floor": {
                    "enabled": True,
                    "min_agreement": 0.80,
                    "min_samples": 25,
                    "window_days": 14,
                }
            }
        )
        assert c.agreement_floor_enabled is True
        assert c.agreement_min == 0.80
        assert c.agreement_min_samples == 25
        assert c.agreement_window_days == 14

    def test_partial_keys_use_defaults(self):
        """Operator can override only what they want; rest use defaults."""
        c = StrategyConfig.from_yaml_dict(
            {"bandit_boost": {"enabled": True}}
        )
        assert c.bandit_boost_enabled is True
        assert c.bandit_min_plays == 20  # default
        assert c.bandit_boost_max == 0.10  # default


# ── Strategy B — bandit boost ────────────────────────────────────────


class TestStrategyB:
    def test_disabled_is_noop(self, approved_decision, blueprint):
        config = StrategyConfig()  # all off
        out = apply_strategy_b(
            approved_decision,
            blueprint=blueprint,
            config=config,
            bandit_lookup=lambda n, a: {"alpha": 99, "beta": 1, "n_plays": 100},
        )
        assert out == approved_decision

    def test_no_op_when_base_rejected(self, rejected_decision, blueprint):
        config = StrategyConfig(bandit_boost_enabled=True)
        out = apply_strategy_b(
            rejected_decision,
            blueprint=blueprint,
            config=config,
            bandit_lookup=lambda n, a: {"alpha": 99, "beta": 1, "n_plays": 100},
        )
        # Rejection stays
        assert out == rejected_decision

    def test_no_op_when_arm_missing(self, approved_decision, blueprint):
        config = StrategyConfig(bandit_boost_enabled=True)
        out = apply_strategy_b(
            approved_decision,
            blueprint=blueprint,
            config=config,
            bandit_lookup=lambda n, a: None,
        )
        assert out == approved_decision

    def test_no_op_below_min_plays(self, approved_decision, blueprint):
        """Cold-start arm — too noisy for a boost."""
        config = StrategyConfig(bandit_boost_enabled=True, bandit_min_plays=20)
        out = apply_strategy_b(
            approved_decision,
            blueprint=blueprint,
            config=config,
            # 5 plays with great performance — still too few
            bandit_lookup=lambda n, a: {"alpha": 6, "beta": 1, "n_plays": 5},
        )
        assert out.confidence == approved_decision.confidence

    def test_boost_only_when_observed_above_posterior(
        self, approved_decision, blueprint
    ):
        """Below-posterior performance shouldn't bump confidence."""
        config = StrategyConfig(bandit_boost_enabled=True)
        # alpha=1, beta=99, n_plays=100 → observed_avg=0.0, posterior=0.01
        # → observed < posterior → no boost
        out = apply_strategy_b(
            approved_decision,
            blueprint=blueprint,
            config=config,
            bandit_lookup=lambda n, a: {"alpha": 1.0, "beta": 99.0, "n_plays": 100},
        )
        assert out.confidence == approved_decision.confidence

    def test_boost_capped_at_max(self, approved_decision, blueprint):
        """A residual larger than boost_max is clamped to boost_max."""
        config = StrategyConfig(bandit_boost_enabled=True, bandit_boost_max=0.05)
        # alpha=2, beta=1, n_plays=1 normally cold-starts under min_plays;
        # use min_plays=1 + huge residual to exercise the cap path.
        config_with_loose_min = StrategyConfig(
            bandit_boost_enabled=True,
            bandit_min_plays=1,
            bandit_boost_max=0.05,
        )
        # alpha=10, beta=1, n_plays=9 → observed=(10-1)/9=1.0,
        # posterior=10/11=0.909, residual=0.091 → boost clamped to 0.05
        out = apply_strategy_b(
            approved_decision,
            blueprint=blueprint,
            config=config_with_loose_min,
            bandit_lookup=lambda n, a: {"alpha": 10.0, "beta": 1.0, "n_plays": 9},
        )
        # 0.72 + 0.05 = 0.77 (rounded to 3dp)
        assert pytest.approx(out.confidence, abs=1e-3) == 0.77

    def test_boost_uncapped_when_residual_small(self, approved_decision, blueprint):
        """When residual < boost_max, the full residual is applied."""
        config = StrategyConfig(
            bandit_boost_enabled=True,
            bandit_min_plays=1,
            bandit_boost_max=0.20,  # > any natural residual
        )
        out = apply_strategy_b(
            approved_decision,
            blueprint=blueprint,
            config=config,
            bandit_lookup=lambda n, a: {"alpha": 51.0, "beta": 1.0, "n_plays": 50},
        )
        # observed=(51-1)/50=1.0, posterior=51/52=0.9808, residual≈0.0192
        # boost = min(0.20, 0.0192) = 0.0192
        assert pytest.approx(out.confidence, abs=1e-3) == approved_decision.confidence + 0.019

    def test_boost_records_reason(self, approved_decision, blueprint):
        config = StrategyConfig(bandit_boost_enabled=True)
        out = apply_strategy_b(
            approved_decision,
            blueprint=blueprint,
            config=config,
            bandit_lookup=lambda n, a: {"alpha": 26.0, "beta": 1.0, "n_plays": 25},
        )
        assert any("Strategy B" in r for r in out.reasons)

    def test_lookup_exception_does_not_propagate(self, approved_decision, blueprint):
        config = StrategyConfig(bandit_boost_enabled=True)

        def boom(_n, _a):
            raise RuntimeError("DB down")

        out = apply_strategy_b(
            approved_decision,
            blueprint=blueprint,
            config=config,
            bandit_lookup=boom,
        )
        # Fail-open: base decision preserved (B is an enhancement, not a gate)
        assert out == approved_decision

    def test_confidence_never_exceeds_one(self, approved_decision, blueprint):
        """0.99 + 0.10 boost should clamp to 1.0, not 1.09."""
        d = AutoApprovalDecision(
            approved=True,
            confidence=0.99,
            passed_checks=["all"],
            failed_checks=[],
            reasons=[],
        )
        config = StrategyConfig(bandit_boost_enabled=True, bandit_boost_max=0.10)
        out = apply_strategy_b(
            d,
            blueprint=blueprint,
            config=config,
            bandit_lookup=lambda n, a: {"alpha": 31.0, "beta": 1.0, "n_plays": 30},
        )
        assert out.confidence <= 1.0


# ── Strategy E — empirical agreement floor ────────────────────────────


class TestStrategyE:
    def test_disabled_is_noop(self, approved_decision):
        config = StrategyConfig()  # all off
        out = apply_strategy_e(
            approved_decision,
            niche_id="gaming",
            config=config,
            calibration_lookup=lambda n, w: {
                "sample_count": 1,
                "agreement_rate": 0.0,
            },
        )
        assert out == approved_decision

    def test_no_op_when_base_rejected(self, rejected_decision):
        config = StrategyConfig(agreement_floor_enabled=True)
        out = apply_strategy_e(
            rejected_decision,
            niche_id="gaming",
            config=config,
            calibration_lookup=lambda n, w: {
                "sample_count": 1,
                "agreement_rate": 0.0,
            },
        )
        assert out == rejected_decision

    def test_fails_closed_when_sample_count_too_low(self, approved_decision):
        config = StrategyConfig(
            agreement_floor_enabled=True, agreement_min_samples=10
        )
        out = apply_strategy_e(
            approved_decision,
            niche_id="gaming",
            config=config,
            calibration_lookup=lambda n, w: {
                "sample_count": 5,
                "agreement_rate": 0.95,
            },
        )
        assert out.approved is False
        assert "empirical_agreement_floor" in out.failed_checks
        assert any("Strategy E" in r for r in out.reasons)

    def test_fails_closed_when_agreement_below_floor(self, approved_decision):
        config = StrategyConfig(
            agreement_floor_enabled=True,
            agreement_min=0.70,
            agreement_min_samples=10,
        )
        out = apply_strategy_e(
            approved_decision,
            niche_id="gaming",
            config=config,
            calibration_lookup=lambda n, w: {
                "sample_count": 30,
                "agreement_rate": 0.65,
            },
        )
        assert out.approved is False
        assert "empirical_agreement_floor" in out.failed_checks

    def test_passes_when_both_above_floor(self, approved_decision):
        config = StrategyConfig(agreement_floor_enabled=True)
        out = apply_strategy_e(
            approved_decision,
            niche_id="gaming",
            config=config,
            calibration_lookup=lambda n, w: {
                "sample_count": 50,
                "agreement_rate": 0.92,
            },
        )
        assert out.approved is True
        assert out.confidence == approved_decision.confidence
        assert any("Strategy E" in r and "above floor" in r for r in out.reasons)

    def test_fails_closed_when_calibration_missing(self, approved_decision):
        config = StrategyConfig(agreement_floor_enabled=True)
        out = apply_strategy_e(
            approved_decision,
            niche_id="gaming",
            config=config,
            calibration_lookup=lambda n, w: None,
        )
        assert out.approved is False

    def test_lookup_exception_fails_closed(self, approved_decision):
        """DB error → conservatively reject; operator manual path still runs."""
        config = StrategyConfig(agreement_floor_enabled=True)

        def boom(_n, _w):
            raise RuntimeError("DB down")

        out = apply_strategy_e(
            approved_decision,
            niche_id="gaming",
            config=config,
            calibration_lookup=boom,
        )
        assert out.approved is False

    def test_rejection_drops_confidence_to_zero(self, approved_decision):
        """Worker's min_confidence gate must NEVER let an E-rejected blueprint through."""
        config = StrategyConfig(
            agreement_floor_enabled=True, agreement_min_samples=10
        )
        out = apply_strategy_e(
            approved_decision,
            niche_id="gaming",
            config=config,
            calibration_lookup=lambda n, w: {
                "sample_count": 3,
                "agreement_rate": 0.99,
            },
        )
        assert out.confidence == 0.0


# ── apply_strategies (B + E together) ─────────────────────────────────


class TestApplyStrategies:
    def test_both_off_is_passthrough(self, approved_decision, blueprint):
        out = apply_strategies(
            approved_decision,
            blueprint=blueprint,
            niche_id="gaming",
            config=StrategyConfig(),
            bandit_lookup=lambda n, a: {"alpha": 99, "beta": 1, "n_plays": 100},
            calibration_lookup=lambda n, w: {
                "sample_count": 50,
                "agreement_rate": 0.92,
            },
        )
        assert out == approved_decision

    def test_e_overrides_b_boost(self, approved_decision, blueprint):
        """When E rejects, B's confidence bump must NOT win."""
        config = StrategyConfig(
            bandit_boost_enabled=True,
            agreement_floor_enabled=True,
            agreement_min_samples=10,
        )
        out = apply_strategies(
            approved_decision,
            blueprint=blueprint,
            niche_id="gaming",
            config=config,
            # B says boost
            bandit_lookup=lambda n, a: {"alpha": 26.0, "beta": 1.0, "n_plays": 25},
            # E says reject (only 3 samples)
            calibration_lookup=lambda n, w: {
                "sample_count": 3,
                "agreement_rate": 0.99,
            },
        )
        assert out.approved is False
        assert out.confidence == 0.0
        assert "empirical_agreement_floor" in out.failed_checks

    def test_b_runs_before_e(self, approved_decision, blueprint):
        """When both pass, B applies first (visible confidence bump),
        then E records its 'above floor' reason after."""
        config = StrategyConfig(
            bandit_boost_enabled=True,
            agreement_floor_enabled=True,
        )
        out = apply_strategies(
            approved_decision,
            blueprint=blueprint,
            niche_id="gaming",
            config=config,
            bandit_lookup=lambda n, a: {"alpha": 26.0, "beta": 1.0, "n_plays": 25},
            calibration_lookup=lambda n, w: {
                "sample_count": 50,
                "agreement_rate": 0.92,
            },
        )
        assert out.approved is True
        assert out.confidence > approved_decision.confidence
        # B reason then E reason in append order
        strat_b_idx = next(i for i, r in enumerate(out.reasons) if "Strategy B" in r)
        strat_e_idx = next(i for i, r in enumerate(out.reasons) if "Strategy E" in r)
        assert strat_b_idx < strat_e_idx

    def test_missing_lookup_short_circuits(self, approved_decision, blueprint):
        """If a lookup callback is omitted, the corresponding strategy
        becomes a no-op even when the flag is on."""
        config = StrategyConfig(
            bandit_boost_enabled=True,
            agreement_floor_enabled=True,
        )
        out = apply_strategies(
            approved_decision,
            blueprint=blueprint,
            niche_id="gaming",
            config=config,
            bandit_lookup=None,  # B becomes no-op
            calibration_lookup=None,  # E becomes no-op
        )
        assert out == approved_decision
