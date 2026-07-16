"""Tests for the ensemble decision-making layer (Intervention 6, 2026-07-01).

Coverage:
- Feature-flag semantics (exact-true only, historical stub otherwise)
- Component adapters degrade individually (missing model / DB error /
  disabled sub-component all abstain rather than crash the ensemble)
- Weight normalisation over the actually-voting subset
- Disagreement variance detected and routes to worth_your_look
- Recommendation thresholds map correctly for each corner
- LLM judge only fires in the borderline band AND when its own
  env flag is on
- Public dataclass shapes (EnsembleVote, EnsembleDecision) pinned as
  a contract for the dashboard / persistence layer

The component adapters are patched via monkeypatch to keep tests
hermetic — no live DB, no model files, no LLM calls.
"""

from __future__ import annotations

import pytest
from genlab_core.scheduling import ensemble_decide as ed
from genlab_core.scheduling.ensemble_decide import (
    EnsembleDecision,
    EnsembleVote,
    ensemble_decide,
)

# ── Feature flag ─────────────────────────────────────────────────────


class TestFeatureFlag:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("GENLAB_ENSEMBLE_DECISION_ENABLED", raising=False)
        result = ensemble_decide({"hook_text": "hi"}, "gaming")
        assert result.score is None
        assert result.recommendation == "disabled"
        assert result.n_voters == 0
        assert result.votes == []

    @pytest.mark.parametrize("val", ["true", "TRUE", "True", "1", "yes", "on", "y", "t"])
    def test_truthy_values_activate(self, monkeypatch, val):
        """2026-07-14: switched to env_true() semantics. Prior tests
        locked strict ``"true"``-only which was inconsistent with
        sibling flags. See ensemble_decide docstring for the class-of-
        bug context — two flags in the same module with incompatible
        activation values silently no-op'd for weeks."""
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", val)
        assert ed._integration_enabled()

    @pytest.mark.parametrize("val", ["", "false", "0", "no", "off", "TRUE_"])
    def test_falsy_values_stay_off(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", val)
        assert not ed._integration_enabled()


# ── Aggregation math ────────────────────────────────────────────────


class TestAggregation:
    """The math is small enough to unit-test directly without going
    through the full public entry point + adapter patching."""

    def test_two_agreeing_voters_get_averaged(self):
        votes = [
            EnsembleVote("bandit", 0.8, 0.25),
            EnsembleVote("hook_classifier", 0.8, 0.20),
        ]
        score, disagreement, n_voters = ed._aggregate(votes)
        assert n_voters == 2
        assert score == pytest.approx(0.8)
        # Identical scores → zero variance
        assert disagreement == pytest.approx(0.0)

    def test_two_disagreeing_voters_produce_variance(self):
        votes = [
            EnsembleVote("bandit", 0.2, 0.5),
            EnsembleVote("hook_classifier", 0.8, 0.5),
        ]
        score, disagreement, n_voters = ed._aggregate(votes)
        assert n_voters == 2
        assert score == pytest.approx(0.5)  # equal weights, symmetric
        assert disagreement > 0.05  # crosses worth_your_look threshold

    def test_one_voter_abstains(self):
        """Fewer than 2 non-None votes → ensemble abstains."""
        votes = [
            EnsembleVote("bandit", 0.7, 0.25),
            EnsembleVote("hook_classifier", None, 0.20, "no model"),
            EnsembleVote("bayesian_gate", None, 0.25, "disabled"),
            EnsembleVote("conformal_router", None, 0.20, "disabled"),
        ]
        score, disagreement, n_voters = ed._aggregate(votes)
        assert n_voters == 1
        assert score is None
        assert disagreement == 0.0

    def test_weight_renormalisation_when_component_abstains(self):
        """Missing weight redistributes across the voting subset — the
        two remaining voters share the full ensemble output."""
        votes = [
            EnsembleVote("bandit", 0.9, 0.25),
            EnsembleVote("hook_classifier", 0.7, 0.20),
            EnsembleVote("bayesian_gate", None, 0.25, "abstain"),
            EnsembleVote("conformal_router", None, 0.20, "abstain"),
        ]
        score, _, n_voters = ed._aggregate(votes)
        assert n_voters == 2
        # weighted: 0.9 * (0.25/0.45) + 0.7 * (0.20/0.45) = 0.811...
        assert score == pytest.approx(0.9 * (0.25 / 0.45) + 0.7 * (0.20 / 0.45))


# ── Recommendation thresholds ───────────────────────────────────────


class TestRecommend:
    def test_auto_approve_when_high_score_low_disagreement(self):
        assert ed._recommend(0.80, 0.01) == "auto_approve"

    def test_auto_reject_when_low_score_low_disagreement(self):
        assert ed._recommend(0.20, 0.01) == "auto_reject"

    def test_worth_your_look_when_disagreement_high(self):
        """Even with a "should auto-approve" score, high disagreement
        routes to the operator queue — this is the entire point of
        the ensemble."""
        assert ed._recommend(0.80, 0.10) == "worth_your_look"
        assert ed._recommend(0.20, 0.10) == "worth_your_look"

    def test_route_to_operator_in_middle_band(self):
        assert ed._recommend(0.50, 0.01) == "route_to_operator"

    def test_insufficient_data_when_score_none(self):
        assert ed._recommend(None, 0.0) == "insufficient_data"


# ── Component adapter degradation ──────────────────────────────────


class TestAdapterDegradation:
    """Each adapter must return an ``EnsembleVote(score=None)`` on any
    failure — never raise. This is the fail-open contract that keeps
    a broken sub-component from taking down the ensemble."""

    def test_bandit_abstains_when_no_arm_id(self):
        vote = ed._vote_bandit({"hook_text": "hi"}, "gaming")
        assert vote.component == "bandit"
        assert vote.score is None
        assert "no arm_id" in vote.reason

    def test_hook_classifier_abstains_when_no_hook(self):
        vote = ed._vote_hook_classifier({"arm_id": "x"}, "gaming")
        assert vote.component == "hook_classifier"
        assert vote.score is None
        assert "no hook_text" in vote.reason

    def test_bayesian_gate_abstains_when_env_flag_off(self, monkeypatch):
        """The gate returns (0.5, 0.5) as its "disabled/degenerate" sentinel;
        the adapter must translate that to an abstain vote."""
        monkeypatch.delenv("GENLAB_BAYESIAN_GATE_ENABLED", raising=False)
        vote = ed._vote_bayesian_gate({"hook_text": "hi"}, "gaming")
        assert vote.score is None
        assert "abstain" in vote.reason

    def test_conformal_abstains_when_env_flag_off(self, monkeypatch):
        """Conformal returns route_to_operator with a "disabled: ..."
        reason when flag off. Adapter maps that to an abstain."""
        monkeypatch.delenv("GENLAB_CONFORMAL_ROUTER_ENABLED", raising=False)
        vote = ed._vote_conformal_router({"hook_text": "hi"}, "gaming")
        assert vote.score is None
        assert "abstain" in vote.reason


# ── End-to-end with patched adapters ───────────────────────────────


class TestEnsembleE2E:
    """Full public entry point with adapters patched via monkeypatch."""

    def _patch_votes(self, monkeypatch, votes_by_component: dict[str, float | None]):
        def make_stub(component: str, score: float | None):
            weight = ed._DEFAULT_WEIGHTS[component]

            def stub(blueprint, niche_id):
                return EnsembleVote(component, score, weight, "stubbed")

            return stub

        for component, score in votes_by_component.items():
            monkeypatch.setattr(ed, f"_vote_{component}", make_stub(component, score))

    def test_auto_approve_when_all_high_and_agreeing(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", "true")
        self._patch_votes(
            monkeypatch,
            {
                "bandit": 0.80,
                "hook_classifier": 0.75,
                "bayesian_gate": 0.85,
                "conformal_router": 0.85,
            },
        )
        result = ensemble_decide({}, "gaming")
        assert result.score is not None
        assert result.score > 0.65
        assert result.recommendation == "auto_approve"
        assert result.n_voters == 4

    def test_worth_your_look_when_components_split(self, monkeypatch):
        """Two components confident-approve, two confident-reject. Weighted
        score sits in the middle band, but the DISAGREEMENT is what
        triggers the worth_your_look verdict — even if the score by itself
        looked like ordinary borderline."""
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", "true")
        self._patch_votes(
            monkeypatch,
            {
                "bandit": 0.90,
                "hook_classifier": 0.20,
                "bayesian_gate": 0.90,
                "conformal_router": 0.15,
            },
        )
        result = ensemble_decide({}, "gaming")
        assert result.recommendation == "worth_your_look"
        assert result.disagreement > ed._WORTH_YOUR_LOOK_DISAGREEMENT

    def test_insufficient_data_when_only_one_voter(self, monkeypatch):
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", "true")
        self._patch_votes(
            monkeypatch,
            {
                "bandit": 0.7,
                "hook_classifier": None,
                "bayesian_gate": None,
                "conformal_router": None,
            },
        )
        result = ensemble_decide({}, "gaming")
        assert result.n_voters == 1
        assert result.score is None
        assert result.recommendation == "insufficient_data"


# ── LLM judge escalation ───────────────────────────────────────────


class TestLLMJudgeEscalation:
    """The LLM judge is triple-gated: (1) caller opts in via kwarg,
    (2) env flag GENLAB_LLM_JUDGE_ENABLED=1, (3) interim ensemble
    score sits in the borderline band 0.4-0.6.

    Any of the three off → judge abstains + no LLM cost incurred."""

    def test_judge_skipped_when_not_borderline(self, monkeypatch):
        vote = ed._vote_llm_judge({"hook_text": "hi"}, "gaming", interim_score=0.85)
        assert vote.score is None
        assert "not borderline" in vote.reason

    def test_judge_skipped_when_env_flag_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_LLM_JUDGE_ENABLED", raising=False)
        # Borderline score → passes the range gate — but env flag off.
        vote = ed._vote_llm_judge({"hook_text": "hi"}, "gaming", interim_score=0.50)
        assert vote.score is None
        assert "GENLAB_LLM_JUDGE_ENABLED" in vote.reason

    def test_judge_not_invoked_when_caller_opts_out(self, monkeypatch):
        """Even with all env flags on, if the caller doesn't pass
        enable_llm_judge=True, the judge slot never appears in votes."""
        monkeypatch.setenv("GENLAB_ENSEMBLE_DECISION_ENABLED", "true")
        monkeypatch.setenv("GENLAB_LLM_JUDGE_ENABLED", "1")

        def stub(component, score):
            weight = ed._DEFAULT_WEIGHTS[component]

            def _(blueprint, niche_id):
                return EnsembleVote(component, score, weight, "stubbed")

            return _

        # Build interim score right at borderline 0.5
        for c, s in {
            "bandit": 0.5,
            "hook_classifier": 0.5,
            "bayesian_gate": 0.5,
            "conformal_router": 0.5,
        }.items():
            monkeypatch.setattr(ed, f"_vote_{c}", stub(c, s))

        result = ensemble_decide({}, "gaming", enable_llm_judge=False)
        components = [v.component for v in result.votes]
        assert "llm_judge" not in components


# ── Dataclass shape pins ───────────────────────────────────────────


class TestDataclassShape:
    """Pin the public fields so a rename doesn't silently break
    the dashboard / persistence consumers."""

    def test_ensemble_vote_shape(self):
        v = EnsembleVote(component="bandit", score=0.7, weight=0.25, reason="ok")
        assert v.component == "bandit"
        assert v.score == 0.7
        assert v.weight == 0.25
        assert v.reason == "ok"

    def test_ensemble_decision_shape(self):
        d = EnsembleDecision(
            score=0.7,
            disagreement=0.02,
            n_voters=3,
            votes=[],
            recommendation="auto_approve",
            reasons=[],
        )
        assert d.score == 0.7
        assert d.disagreement == 0.02
        assert d.n_voters == 3
        assert d.recommendation == "auto_approve"
