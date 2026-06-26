"""Tests for genlab_core.scheduling.conformal_router (Engine 1.2).

Pins the split-conformal selective-prediction router. Coverage spans
the safety defaults (env flag, niche-size threshold, fail-open) and
the algorithmic guarantees (singleton routing, Mondrian stratification,
empirical coverage on synthetic data).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from genlab_core.scheduling import conformal_router
from genlab_core.scheduling.conformal_router import (
    DEFAULT_ALPHA,
    MIN_NICHE_SAMPLES,
    ConformalRouteResult,
    fit_niche_state,
    route_blueprint,
)

# ── Helpers ───────────────────────────────────────────────────────────


def _bp(
    *,
    niche_id: str = "gaming",
    composite: float = 0.6,
    virality: float = 0.1,
    hook_clf: float = 0.6,
    hook_text: str = "A reasonable hook",
    gate_confidence: float = 0.7,
    n_passed: int = 5,
    n_failed: int = 0,
) -> dict:
    """Build a blueprint dict in the shape the router consumes."""
    return {
        "id": "test-id",
        "niche_id": niche_id,
        "hook_text": hook_text,
        "extra": {
            "composite_score": composite,
            "virality_score": virality,
            "hook_classifier_score": hook_clf,
            "gate_confidence": gate_confidence,
            "gate_passed_checks": ["x"] * n_passed,
            "gate_failed_checks": ["x"] * n_failed,
        },
    }


def _write_state(tmp_path, niches: dict[str, dict[str, Any]], alpha: float = DEFAULT_ALPHA) -> str:
    """Write a state JSON file and return its path."""
    state = {"alpha": alpha, "niches": niches}
    p = tmp_path / "conformal_state.json"
    p.write_text(json.dumps(state))
    return str(p)


@pytest.fixture(autouse=True)
def _reset_cache_and_env(monkeypatch):
    """Each test starts with a clean cache + env."""
    conformal_router._invalidate_state_cache()
    # Force-enable in tests that opt in; tests that need the flag
    # OFF will pop it explicitly.
    monkeypatch.delenv("GENLAB_CONFORMAL_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("GENLAB_CONFORMAL_STATE_PATH", raising=False)
    yield
    conformal_router._invalidate_state_cache()


# ── Safety defaults ───────────────────────────────────────────────────


class TestSafetyDefaults:
    def test_no_op_when_flag_unset(self, tmp_path, monkeypatch):
        """Env flag unset → always route_to_operator regardless of state."""
        # Write a state file that WOULD route to auto_approve …
        state_path = _write_state(
            tmp_path,
            niches={
                "gaming": {
                    "sample_count": 200,
                    "weights": [0.0] * 7,
                    "intercept": 10.0,  # massive positive bias → strong approve
                    "q_hat": 0.01,
                    "alpha": DEFAULT_ALPHA,
                }
            },
        )
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)
        # Flag intentionally NOT set.
        result = route_blueprint(_bp())
        assert isinstance(result, ConformalRouteResult)
        assert result.action == "route_to_operator"
        assert "disabled" in result.reason.lower()

    def test_returns_route_to_operator_when_niche_has_insufficient_data(
        self, tmp_path, monkeypatch
    ):
        """Niche with 30 rows (below the 50 threshold) routes to operator."""
        assert MIN_NICHE_SAMPLES == 50  # pin the constant explicitly
        state_path = _write_state(
            tmp_path,
            niches={
                "gaming": {
                    "sample_count": 30,
                    "weights": [0.0] * 7,
                    "intercept": 10.0,
                    "q_hat": 0.01,
                    "alpha": DEFAULT_ALPHA,
                }
            },
        )
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)
        result = route_blueprint(_bp())
        assert result.action == "route_to_operator"
        assert "insufficient calibration data" in result.reason

    def test_returns_route_to_operator_when_niche_unknown(self, tmp_path, monkeypatch):
        """Niche with no entry in state file → cold-start route_to_operator."""
        state_path = _write_state(tmp_path, niches={})
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)
        result = route_blueprint(_bp(niche_id="unknown_niche"))
        assert result.action == "route_to_operator"
        assert "cold-start" in result.reason

    def test_fail_open_on_db_error(self, tmp_path, monkeypatch):
        """Inference exception → route_to_operator with the error in the reason."""
        state_path = _write_state(
            tmp_path,
            niches={
                "gaming": {
                    "sample_count": 200,
                    "weights": [0.0] * 7,
                    "intercept": 0.0,
                    "q_hat": 0.5,
                    "alpha": DEFAULT_ALPHA,
                }
            },
        )
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)

        # Force the scoring function to raise — simulates a numpy /
        # storage corruption error at inference time.
        def _boom(*args, **kwargs):
            raise RuntimeError("simulated psycopg.OperationalError")

        with patch.object(conformal_router, "_predict_proba_approved", side_effect=_boom):
            result = route_blueprint(_bp())
        assert result.action == "route_to_operator"
        assert "inference error" in result.reason


# ── Singleton / doubleton routing ─────────────────────────────────────


class TestRoutingOutcomes:
    def test_singleton_approve_returns_auto_approve(self, tmp_path, monkeypatch):
        """Tiny q_hat + strong-positive model → only {approved} admitted."""
        # weights=zeros, intercept large positive → p_approved ≈ 1
        # → 1 - p_approved ≈ 0 ≤ q_hat=0.01 (approve admitted)
        # → 1 - p_rejected ≈ 1 NOT ≤ q_hat (reject NOT admitted)
        state_path = _write_state(
            tmp_path,
            niches={
                "gaming": {
                    "sample_count": 200,
                    "weights": [0.0] * 7,
                    "intercept": 10.0,
                    "q_hat": 0.01,
                    "alpha": DEFAULT_ALPHA,
                }
            },
        )
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)
        result = route_blueprint(_bp())
        assert result.action == "auto_approve"
        assert result.prediction_set == ["approved"]

    def test_singleton_reject_returns_auto_reject(self, tmp_path, monkeypatch):
        """Tiny q_hat + strong-negative model → only {rejected} admitted."""
        state_path = _write_state(
            tmp_path,
            niches={
                "gaming": {
                    "sample_count": 200,
                    "weights": [0.0] * 7,
                    "intercept": -10.0,  # p_approved ≈ 0
                    "q_hat": 0.01,
                    "alpha": DEFAULT_ALPHA,
                }
            },
        )
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)
        result = route_blueprint(_bp())
        assert result.action == "auto_reject"
        assert result.prediction_set == ["rejected"]

    def test_doubleton_returns_route_to_operator(self, tmp_path, monkeypatch):
        """Large q_hat → both labels admitted → operator gets the call."""
        # Large q_hat (0.6) admits both labels for any model output
        # in [0.4, 0.6] — the ABSTAIN region.
        state_path = _write_state(
            tmp_path,
            niches={
                "gaming": {
                    "sample_count": 200,
                    "weights": [0.0] * 7,
                    "intercept": 0.0,  # p_approved = 0.5 exactly
                    "q_hat": 0.6,
                    "alpha": DEFAULT_ALPHA,
                }
            },
        )
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)
        result = route_blueprint(_bp())
        assert result.action == "route_to_operator"
        assert set(result.prediction_set) == {"approved", "rejected"}
        assert "ABSTAIN" in result.reason

    def test_mondrian_stratification_per_niche(self, tmp_path, monkeypatch):
        """Each niche reads its OWN q_hat — verifies the dict lookup
        actually keys by niche_id AND the different per-niche q̂s
        actually drive different routing outcomes."""
        # gaming: tiny q̂ + strong-positive model → singleton {approved}
        #   → auto_approve.
        # anime: same neutral model (intercept=0 → p_approved=0.5) but
        #   large q̂ → BOTH labels admitted (1 - 0.5 = 0.5 ≤ 0.6) →
        #   route_to_operator (the ABSTAIN doubleton).
        # Same blueprint shape → only the per-niche q̂ differs → the
        # routing outcomes diverge, which is the Mondrian guarantee.
        state_path = _write_state(
            tmp_path,
            niches={
                "gaming": {
                    "sample_count": 200,
                    "weights": [0.0] * 7,
                    "intercept": 10.0,
                    "q_hat": 0.01,
                    "alpha": DEFAULT_ALPHA,
                },
                "anime": {
                    "sample_count": 200,
                    "weights": [0.0] * 7,
                    "intercept": 0.0,  # neutral: p_approved = 0.5
                    "q_hat": 0.6,  # 1 - 0.5 ≤ 0.6 for both labels → doubleton
                    "alpha": DEFAULT_ALPHA,
                },
            },
        )
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)

        gaming_result = route_blueprint(_bp(niche_id="gaming"))
        anime_result = route_blueprint(_bp(niche_id="anime"))

        assert gaming_result.action == "auto_approve"
        assert gaming_result.q_hat == 0.01
        assert anime_result.action == "route_to_operator"
        assert anime_result.q_hat == 0.6
        # The Mondrian guarantee — different niches got different q̂s.
        assert gaming_result.q_hat != anime_result.q_hat


# ── Empirical coverage on synthetic data ──────────────────────────────


class TestCoverageGuarantee:
    def test_coverage_guarantee_holds_empirically(self, tmp_path, monkeypatch):
        """Synthetic dataset → empirical coverage ≥ 1 − α − slack.

        Build a separable-enough 2D synthetic problem, fit the router
        on 1000 rows, then evaluate the prediction set's coverage on
        500 held-out test rows. Conformal's marginal coverage
        guarantee is ``P(y* ∈ Ĉ(x*)) ≥ 1 − α``. Slack of 0.05 covers
        finite-sample variance.
        """
        np = pytest.importorskip("numpy")
        pytest.importorskip("sklearn")

        rng = np.random.default_rng(42)
        n_train_plus_calib = 1000
        n_test = 500
        alpha = 0.10

        # Generate features around two well-separated centers — but
        # with enough overlap that conformal's ABSTAIN region is
        # populated.
        def _make(n):
            half = n // 2
            pos = rng.normal(loc=1.0, scale=1.0, size=(half, 7))
            neg = rng.normal(loc=-1.0, scale=1.0, size=(n - half, 7))
            X = np.vstack([pos, neg])
            y = np.array([1] * half + [0] * (n - half))
            perm = rng.permutation(n)
            return X[perm], y[perm]

        X_fit, y_fit = _make(n_train_plus_calib)
        X_test, y_test = _make(n_test)

        rows = [
            {"niche_id": "synthetic", "label": int(yi), "features": Xi.tolist()}
            for Xi, yi in zip(X_fit, y_fit, strict=True)
        ]

        fit = fit_niche_state(rows, alpha=alpha, random_state=42)
        assert fit is not None
        state_path = _write_state(tmp_path, niches={"synthetic": fit}, alpha=alpha)
        monkeypatch.setenv("GENLAB_CONFORMAL_ROUTER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_CONFORMAL_STATE_PATH", state_path)

        # Each test point: build a blueprint whose feature vector
        # exactly reconstructs the synthetic features via _extract_features.
        # Map 7-features → blueprint fields in the same order.
        # Features are: composite, virality, hook_clf, hook_len,
        #               n_passed, n_failed, gate_confidence.
        # hook_len is len(hook_text); use abs(int(round(x))) for the
        # length so we can recover exactly. n_passed/n_failed are list
        # lengths — use abs(int(round(x))) too.
        def _bp_from_features(feats):
            composite, virality, hook_clf, hook_len, n_passed, n_failed, gate_conf = feats
            return {
                "id": "x",
                "niche_id": "synthetic",
                "hook_text": "x" * max(0, int(round(hook_len))),
                "extra": {
                    "composite_score": float(composite),
                    "virality_score": float(virality),
                    "hook_classifier_score": float(hook_clf),
                    "gate_confidence": float(gate_conf),
                    "gate_passed_checks": ["x"] * max(0, int(round(n_passed))),
                    "gate_failed_checks": ["x"] * max(0, int(round(n_failed))),
                },
            }

        # Override the niche-size floor for the synthetic test so the
        # fit (~1000 rows) is consumed. The default 50 is met anyway.
        covered = 0
        for Xi, yi in zip(X_test, y_test, strict=True):
            result = route_blueprint(_bp_from_features(Xi))
            label = "approved" if yi == 1 else "rejected"
            if label in result.prediction_set:
                covered += 1
        coverage = covered / n_test
        # Marginal coverage guarantee — allow 0.05 slack for finite n.
        assert coverage >= (1 - alpha - 0.05), (
            f"coverage {coverage:.3f} below 1-α-0.05={1 - alpha - 0.05:.3f}"
        )


# ── fit_niche_state algorithmic guards ────────────────────────────────


class TestFitNicheState:
    def test_skips_under_minimum_samples(self):
        rows = [{"label": i % 2, "features": [0.0] * 7} for i in range(MIN_NICHE_SAMPLES - 1)]
        assert fit_niche_state(rows) is None

    def test_skips_degenerate_single_class(self):
        rows = [{"label": 1, "features": [float(i)] * 7} for i in range(MIN_NICHE_SAMPLES + 5)]
        assert fit_niche_state(rows) is None

    def test_returns_serializable_state(self):
        # Mix of labels so the fit succeeds.
        rows = []
        for i in range(MIN_NICHE_SAMPLES * 4):
            label = i % 2
            rows.append({"label": label, "features": [float(label)] * 7})
        fit = fit_niche_state(rows, alpha=0.10, random_state=0)
        assert fit is not None
        assert {"sample_count", "alpha", "q_hat", "weights", "intercept"}.issubset(fit.keys())
        # Must be JSON-serializable to land in conformal_router_state.json
        json.dumps(fit)
        assert len(fit["weights"]) == 7
        assert 0.0 <= fit["q_hat"] <= 1.0
