"""Bayesian logistic regression gate with Thompson sampling — Engine 1.5.

Replaces the hand-tuned ``rollout_pct`` knob (0.1 → 0.25 → 0.5 → 1.0
ramp the operator currently moves via YAML PRs) with a data-driven,
posterior-sampled p(approve|blueprint). Wide posteriors (few rows)
naturally explore conservatively; tight posteriors (lots of rows)
naturally exploit aggressively. The exploration-exploitation curve
shapes itself from the calibration data instead of from an operator's
manual schedule.

Algorithm (Bayesian LR with Laplace approximation):

1. **Prior**: ``w ~ N(0, σ² I)`` with σ = 10 (weakly informative — lets
   the data speak while keeping the optimization well-posed).
2. **Likelihood**: per calibration row ``(x_i, y_i)``,
   ``p(y_i | x_i, w) = σ(w·x_i)^y_i (1 - σ(w·x_i))^{1-y_i}``.
3. **MAP estimate**: ``ŵ = argmax_w log p(w) + Σ log p(y_i | x_i, w)``
   via ``scipy.optimize.minimize`` (L-BFGS-B; analytic gradient).
4. **Laplace covariance**: ``Σ ≈ H⁻¹`` where ``H`` is the Hessian of
   the negative log-posterior at ``ŵ``. Closed-form for LR — no MCMC.
5. **Decision (Thompson sampling)**: sample ``w_t ~ N(ŵ, Σ)``, return
   ``p_approve = σ(w_t · x_new)``. Caller thresholds at 0.5.

Per-niche posteriors fit nightly from ``auto_approval_calibration`` ⨝
``blueprints``. Persisted to ``bayesian_gate_state.json`` per niche
(~5 KB). Reads in ``sample_preference()`` lazy-load on first call.

Safety defaults (every one is opt-in / fail-open):

- ``GENLAB_BAYESIAN_GATE_ENABLED`` env flag — unset or "0" returns
  ``(0.5, 0.5)`` always (no behavioral change from pre-shipped state).
- Niche needs ≥ ``_MIN_ROWS_PER_NICHE`` (50) calibration rows before
  the posterior is sampled. Below: deterministic ``(0.5, 0.5)``.
- ε-greedy floor: first ``_EPSILON_DECISION_FLOOR`` (100) decisions per
  niche, with probability ``_EPSILON_PROB`` (0.10) the sample is a
  uniform random draw — prevents Thompson lock-on during cold start.
- Fail-OPEN: any DB / optimization / scipy / load error returns
  ``(0.5, 0.5)`` (the neutral prior). Logged at WARNING.

Cost: $0/month. Fit ~100 ms on ≤500 rows; inference sub-ms.

References:
- PG-TS Logistic Bandits, Dumitrascu et al. NeurIPS 2018
- ``docs/AGENT-LEARNING-ENGINES.md`` § Engine 1.5
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# ── Feature contract ───────────────────────────────────────────────
# Order matters: training and inference MUST agree.
# The first column is the intercept (constant 1.0) — gives the LR a
# bias term without adding a bookkeeping feature elsewhere.
FEATURE_NAMES: Final[tuple[str, ...]] = (
    "intercept",
    "composite_score",
    "virality_score",
    "hook_classifier_score",
    "n_passed_checks",
    "n_failed_checks",
    "gate_confidence",
)
N_FEATURES: Final[int] = len(FEATURE_NAMES)

# ── Prior ──────────────────────────────────────────────────────────
# σ = 10 is weakly informative — wide enough that the data dominates
# at n ≥ 30, tight enough that the optimizer stays well-posed at n < 30.
# 1 / σ² is the L2 penalty applied to non-intercept weights.
_PRIOR_SIGMA: Final[float] = 10.0
_PRIOR_LAMBDA: Final[float] = 1.0 / (_PRIOR_SIGMA**2)

# ── Cold-start gates ───────────────────────────────────────────────
# Below this row count, return neutral (0.5, 0.5) — the deterministic
# gate handles the decision. Matches `calibration_logger.stats`'s 30-
# row "ready_for_enforcement" floor with a 20-row safety margin.
_MIN_ROWS_PER_NICHE: Final[int] = 50

# ε-greedy floor: for the first N decisions per niche, with prob ε
# emit a uniform random sample instead of the posterior sample.
# Prevents Thompson from locking onto the wrong arm on a tiny dataset.
_EPSILON_DECISION_FLOOR: Final[int] = 100
_EPSILON_PROB: Final[float] = 0.10

# ── Optimization ──────────────────────────────────────────────────
_OPTIMIZER_MAXITER: Final[int] = 200
_OPTIMIZER_TOL: Final[float] = 1e-6


# ── Optional-dep probes (mirrors hook_classifier.py pattern) ──────
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

try:
    from scipy.optimize import minimize  # type: ignore[import-untyped]

    _HAS_SCIPY = True
except ImportError:
    minimize = None  # type: ignore[assignment]
    _HAS_SCIPY = False


# ── Persistence path ──────────────────────────────────────────────
def _state_path() -> Path:
    """Resolve the per-process state-file path for the Bayesian gate.

    Defaults to ``<GenLab-root>/.tmp/bayesian_gate_state.json``. Override
    via ``BAYESIAN_GATE_STATE_PATH`` for tests (the test fixture uses
    ``tmp_path`` so the production file is never touched). Mirrors
    ``gate_tuner._overrides_path`` so operators have one mental model
    for "where do nightly-tuned learning artifacts live".
    """
    custom = os.getenv("BAYESIAN_GATE_STATE_PATH")
    if custom:
        return Path(custom)
    # Walk: src/genlab_core/learning/bayesian_gate.py → ... → GenLab root
    here = Path(__file__).resolve()
    genlab_root = here.parents[4]
    return genlab_root / ".tmp" / "bayesian_gate_state.json"


# ── Per-niche decision counter (used by ε-greedy floor) ───────────
# Process-local — survives only for the lifetime of the
# ``auto_approver`` worker. That's the intended scope: the ε-greedy
# floor is a "warm up" safeguard for the first 100 in-process
# decisions; resetting across restarts (always-explore-a-bit) is
# acceptable behavior. Persisting it would add complexity without
# meaningful safety gain.
_decision_counts: dict[str, int] = {}
_decision_counts_lock = threading.Lock()


def _bump_decision_count(niche_id: str) -> int:
    with _decision_counts_lock:
        n = _decision_counts.get(niche_id, 0) + 1
        _decision_counts[niche_id] = n
        return n


def reset_decision_counts() -> None:
    """Reset the per-niche decision counter — used by tests."""
    with _decision_counts_lock:
        _decision_counts.clear()


# ── Posterior dataclass ───────────────────────────────────────────
@dataclass(frozen=True)
class NichePosterior:
    """Persisted state for one niche's Bayesian LR posterior.

    Stored as JSON: ``mean`` is a list of N_FEATURES floats, ``cov`` is
    an N_FEATURES x N_FEATURES nested list. ``n_rows`` records the
    training size so the load path can re-apply the niche-size
    threshold without a DB round-trip.
    """

    niche_id: str
    mean: list[float]
    cov: list[list[float]]
    n_rows: int
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))


# ── Math primitives ───────────────────────────────────────────────
def _sigmoid(z: Any) -> Any:
    """Numerically-stable sigmoid. Accepts scalar or np.ndarray.

    Standard ``1/(1+exp(-z))`` overflows for very negative ``z`` and
    underflows for very positive ``z``; the piecewise formulation here
    is stable in both tails. (Identical formula sklearn uses internally.)
    """
    if not _HAS_NUMPY:
        # Pure-python fallback — only used by the no-op paths so
        # scalar-only is fine.
        import math

        if z >= 0:
            ez = math.exp(-z)
            return 1.0 / (1.0 + ez)
        ez = math.exp(z)
        return ez / (1.0 + ez)
    # Vectorized numpy version with two-sided stability.
    z = np.asarray(z, dtype=np.float64)
    out = np.empty_like(z)
    pos = z >= 0
    out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
    out[~pos] = np.exp(z[~pos]) / (1.0 + np.exp(z[~pos]))
    return out


def _build_feature_vector(blueprint: dict, decision_dict: dict | None = None) -> Any:
    """Extract the N_FEATURES-dim feature vector from a blueprint dict.

    The 6 model features mirror the signals the deterministic
    AutoApprovalGate uses:

    - ``composite_score``, ``virality_score``, ``hook_classifier_score``
      live in ``blueprint["extra"]`` (JSONB).
    - ``n_passed_checks`` / ``n_failed_checks`` / ``gate_confidence``
      come from a precomputed AutoApprovalDecision if the caller passes
      one in ``decision_dict``; otherwise default to neutral (0 / 0 / 0.5).

    All missing values default to neutral midpoints — keeps inference
    well-defined even when the upstream JSON didn't carry one of the
    scores. Same cold-start tolerance as ``auto_approval_gate.evaluate``.
    """
    extra = blueprint.get("extra") or {}
    if not isinstance(extra, dict):
        extra = {}

    def _f(value: Any, default: float = 0.0) -> float:
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    composite = _f(extra.get("composite_score"), 0.0)
    virality = _f(extra.get("virality_score"), 0.0)
    hook_clf = _f(extra.get("hook_classifier_score"), 0.5)

    if decision_dict is not None:
        n_passed = float(len(decision_dict.get("passed_checks") or []))
        n_failed = float(len(decision_dict.get("failed_checks") or []))
        gate_conf = _f(decision_dict.get("confidence"), 0.5)
    else:
        n_passed = 0.0
        n_failed = 0.0
        gate_conf = 0.5

    raw = [1.0, composite, virality, hook_clf, n_passed, n_failed, gate_conf]
    if _HAS_NUMPY:
        return np.asarray(raw, dtype=np.float64)
    return raw


# ── Training (MAP estimation + Laplace covariance) ────────────────
def _neg_log_posterior(w: Any, X: Any, y: Any) -> float:
    """Negative log-posterior = NLL + L2 prior (excluding intercept).

    The intercept (w[0]) is intentionally NOT regularized — penalizing
    a class-balance offset gives a biased model on imbalanced data.
    """
    z = X @ w
    # log-likelihood term — numerically stable via log1p(exp(-|z|)).
    # The naive ``y * log σ + (1-y) * log(1-σ)`` form underflows when σ
    # saturates at 0 or 1. The standard rewrite below is well-conditioned.
    abs_z = np.abs(z)
    nll = np.sum(np.maximum(z, 0) - z * y + np.log1p(np.exp(-abs_z)))
    # L2 prior on non-intercept weights. lambda = 1/σ².
    reg = 0.5 * _PRIOR_LAMBDA * float(w[1:] @ w[1:])
    return float(nll + reg)


def _grad_neg_log_posterior(w: Any, X: Any, y: Any) -> Any:
    """Analytic gradient — speeds up L-BFGS by ~10x vs. finite differences."""
    p = _sigmoid(X @ w)
    grad = X.T @ (p - y)
    # L2 prior gradient (intercept excluded).
    grad_reg = _PRIOR_LAMBDA * w
    grad_reg[0] = 0.0
    return grad + grad_reg


def _hessian_neg_log_posterior(w: Any, X: Any) -> Any:
    """Hessian of the NLL + prior at w.

    For LR, ``∂²NLL/∂w∂wᵀ = Xᵀ diag(p(1-p)) X``. Adding the prior's
    diagonal ``λI`` (with intercept entry zeroed) gives the full
    Hessian. We invert this to get the Laplace covariance.
    """
    p = _sigmoid(X @ w)
    W_diag = p * (1.0 - p)
    H = (X.T * W_diag) @ X
    # Add prior precision (intercept excluded).
    n = H.shape[0]
    H[np.arange(n), np.arange(n)] += _PRIOR_LAMBDA
    H[0, 0] -= _PRIOR_LAMBDA  # restore intercept (no prior)
    return H


def fit_niche_posterior(X: Any, y: Any) -> tuple[Any, Any]:
    """Fit (mean, cov) of the Laplace-approximated posterior.

    Returns numpy arrays. ``X`` must be an n×N_FEATURES design matrix
    with column 0 already set to 1.0 (intercept). ``y`` must be a
    length-n binary vector in {0, 1}. Caller is responsible for
    constructing both.

    Raises RuntimeError when scipy or numpy are missing — caller wraps
    in try/except and degrades to neutral.
    """
    if not _HAS_NUMPY or not _HAS_SCIPY:
        raise RuntimeError("numpy + scipy required for fit_niche_posterior")

    n, d = X.shape
    if d != N_FEATURES:
        raise ValueError(f"X has {d} cols, expected {N_FEATURES}")
    if y.shape[0] != n:
        raise ValueError(f"X has {n} rows, y has {y.shape[0]}")

    # Initial guess: zeros (no prior bias). MAP optimization converges
    # in <50 iterations for any well-formed calibration dataset.
    w0 = np.zeros(N_FEATURES, dtype=np.float64)
    result = minimize(
        _neg_log_posterior,
        w0,
        args=(X, y),
        jac=_grad_neg_log_posterior,
        method="L-BFGS-B",
        options={"maxiter": _OPTIMIZER_MAXITER, "gtol": _OPTIMIZER_TOL},
    )
    if not result.success:
        # Don't raise — return the best-effort guess. The posterior
        # may be slightly biased but the calling sample_preference()
        # fail-open path will still emit a defensible decision.
        logger.warning(
            "[bayesian_gate] L-BFGS did not converge (%s) — using best-effort MAP",
            result.message,
        )
    w_map = np.asarray(result.x, dtype=np.float64)

    # Laplace covariance: Σ ≈ H(ŵ)⁻¹. Jitter on the diagonal handles
    # the rare singular-Hessian case (e.g. all-zero feature for a niche).
    H = _hessian_neg_log_posterior(w_map, X)
    try:
        cov = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        H_jittered = H + 1e-6 * np.eye(N_FEATURES)
        cov = np.linalg.inv(H_jittered)

    # Symmetrize — numerical noise can produce tiny asymmetries that
    # confuse downstream multivariate_normal sampling.
    cov = 0.5 * (cov + cov.T)
    return w_map, cov


# ── State persistence (JSON, mirrors hook_classifier sidecar pattern) ─
def _load_state_file() -> dict[str, NichePosterior]:
    """Load ALL niche posteriors from the state JSON. Returns {} on miss."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        out: dict[str, NichePosterior] = {}
        for niche_id, payload in raw.items():
            try:
                out[niche_id] = NichePosterior(
                    niche_id=niche_id,
                    mean=list(payload["mean"]),
                    cov=[list(row) for row in payload["cov"]],
                    n_rows=int(payload.get("n_rows", 0)),
                    feature_names=list(payload.get("feature_names", list(FEATURE_NAMES))),
                )
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning(
                    "[bayesian_gate] dropping malformed posterior for niche=%s: %s",
                    niche_id,
                    exc,
                )
        return out
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[bayesian_gate] failed to load state from %s: %s", path, exc)
        return {}


def save_posterior(posterior: NichePosterior) -> bool:
    """Persist (mean, cov) for one niche — merges into the state file.

    Returns True on success. Best-effort: caller (the nightly refit
    worker) treats False as "skip this niche, try again tomorrow".
    """
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state_file()
    state[posterior.niche_id] = posterior
    payload = {
        niche_id: {
            "mean": p.mean,
            "cov": p.cov,
            "n_rows": p.n_rows,
            "feature_names": p.feature_names,
        }
        for niche_id, p in state.items()
    }
    try:
        # Atomic write: temp file + rename so a crash mid-write can't
        # corrupt the state file all niches share.
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        tmp.replace(path)
        return True
    except OSError as exc:
        logger.warning("[bayesian_gate] failed to write state to %s: %s", path, exc)
        return False


def load_posterior(niche_id: str) -> NichePosterior | None:
    """Convenience wrapper — returns one niche's posterior or None."""
    return _load_state_file().get(niche_id)


# ── Nightly refit (DB join + per-niche fit + persist) ────────────
def _fetch_training_rows(niche_id: str) -> tuple[Any, Any, int]:
    """Pull (X, y, n_rows) for one niche from the calibration table.

    Returns (zeros, zeros, 0) when no DB / no rows / DB error — the
    caller (refit_all_niches) then skips the niche. Fail-OPEN: errors
    are warnings, never exceptions, so a transient DB blip doesn't
    crash the nightly worker.
    """
    if not _HAS_NUMPY:
        return None, None, 0

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return None, None, 0
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return None, None, 0

    from genlab_core.storage.tenant_context import pg_connect

    try:
        with pg_connect(dsn, niche_id=niche_id, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                # JOIN calibration ⨝ blueprints. Pull each row's:
                #   - extra->>composite_score / virality_score /
                #     hook_classifier_score (the 3 numeric model
                #     features the gate reads at decision time)
                #   - gate_passed_checks / gate_failed_checks /
                #     gate_confidence (the gate's own meta-signals
                #     captured at calibration-write time)
                #   - operator_action (the label)
                #
                # PR #519 UUID-shape guard reproduced here so synthetic
                # test rows don't pollute training (matches the read-
                # side filter in calibration_logger.stats).
                cur.execute(
                    """
                    SELECT
                        COALESCE((b.extra->>'composite_score')::float, 0.0),
                        COALESCE((b.extra->>'virality_score')::float, 0.0),
                        COALESCE((b.extra->>'hook_classifier_score')::float, 0.5),
                        c.gate_passed_checks,
                        c.gate_failed_checks,
                        COALESCE(c.gate_confidence, 0.5),
                        c.operator_action
                    FROM auto_approval_calibration c
                    -- 2026-06-26 fix: blueprints.id is UUID, but
                    -- auto_approval_calibration.blueprint_id is TEXT
                    -- (per migration o5j6k7l8m9n0). Without the cast
                    -- Postgres errors with "operator does not exist:
                    -- uuid = text" and the refit silently falls back to
                    -- 0 rows for every niche.
                    LEFT JOIN blueprints b ON b.id::text = c.blueprint_id
                    WHERE c.niche_id = %s
                      AND c.gate_approved IS NOT NULL
                      AND c.blueprint_id LIKE '________-____-____-____-____________'
                    """,
                    (niche_id,),
                )
                rows = cur.fetchall() or []
    except Exception as exc:
        logger.warning("[bayesian_gate] DB fetch failed for niche=%s: %s", niche_id, exc)
        return None, None, 0

    if not rows:
        return None, None, 0

    X_list = []
    y_list = []
    for composite, virality, hook_clf, passed_json, failed_json, gate_conf, op in rows:
        try:
            n_passed = float(len(json.loads(passed_json) if passed_json else []))
        except (TypeError, ValueError, json.JSONDecodeError):
            n_passed = 0.0
        try:
            n_failed = float(len(json.loads(failed_json) if failed_json else []))
        except (TypeError, ValueError, json.JSONDecodeError):
            n_failed = 0.0
        X_list.append(
            [
                1.0,
                float(composite),
                float(virality),
                float(hook_clf),
                n_passed,
                n_failed,
                float(gate_conf),
            ]
        )
        y_list.append(1.0 if op == "approved" else 0.0)

    X = np.asarray(X_list, dtype=np.float64)
    y = np.asarray(y_list, dtype=np.float64)
    return X, y, len(rows)


def refit_niche(niche_id: str) -> NichePosterior | None:
    """Fit + persist one niche's posterior from current calibration data.

    Returns the persisted posterior on success, None when training
    data is insufficient or any fitting step fails. Designed to be
    called from a nightly cron — fail-OPEN so a single niche's
    failure doesn't abort the batch.
    """
    if not _HAS_NUMPY or not _HAS_SCIPY:
        logger.warning(
            "[bayesian_gate] numpy/scipy unavailable; cannot refit niche=%s",
            niche_id,
        )
        return None

    X, y, n_rows = _fetch_training_rows(niche_id)
    if n_rows < _MIN_ROWS_PER_NICHE:
        logger.info(
            "[bayesian_gate] niche=%s has only %d rows (<%d) — skipping refit",
            niche_id,
            n_rows,
            _MIN_ROWS_PER_NICHE,
        )
        return None

    try:
        w_map, cov = fit_niche_posterior(X, y)
    except Exception as exc:
        logger.warning("[bayesian_gate] fit failed for niche=%s: %s", niche_id, exc)
        return None

    posterior = NichePosterior(
        niche_id=niche_id,
        mean=w_map.tolist(),
        cov=cov.tolist(),
        n_rows=int(n_rows),
    )
    if not save_posterior(posterior):
        return None
    logger.info(
        "[bayesian_gate] refit niche=%s n_rows=%d w_mean_norm=%.3f",
        niche_id,
        n_rows,
        float(np.linalg.norm(w_map)),
    )
    return posterior


def refit_all_niches(
    niche_ids: list[str] | None = None,
) -> dict[str, NichePosterior | None]:
    """Refit every niche's posterior — entry point for the nightly cron."""
    if niche_ids is None:
        niche_ids = ["ai_creators", "gaming", "sports", "movies", "anime"]
    return {nid: refit_niche(nid) for nid in niche_ids}


# ── Inference (the hot path called from auto_approver) ───────────
def sample_preference(
    blueprint: dict,
    niche_id: str,
    *,
    decision_dict: dict | None = None,
) -> tuple[float, float]:
    """Sample Thompson + posterior-mean p(approve) for one blueprint.

    Returns ``(p_approve_sampled, p_approve_mean)``:
    - ``p_approve_sampled``: one draw from the Thompson posterior (the
      caller thresholds at 0.5 for the actual gate decision).
    - ``p_approve_mean``: σ(mean · x), useful for telemetry — lets the
      operator see "what would the posterior mean say?" alongside the
      sampled value.

    Returns ``(0.5, 0.5)`` (neutral) in any of these conditions:
    - ``GENLAB_BAYESIAN_GATE_ENABLED`` not set to "1"
    - numpy or scipy not installed
    - no posterior persisted for ``niche_id``
    - posterior's n_rows < _MIN_ROWS_PER_NICHE
    - any unexpected exception in fit/sample/load

    The ε-greedy floor: for the first ``_EPSILON_DECISION_FLOOR`` (100)
    in-process decisions per niche, with probability ``_EPSILON_PROB``
    (0.10), the sampled value is uniform on [0, 1] instead of from the
    posterior. Prevents Thompson lock-on during cold start.

    Determinism note: this function uses ``random.random`` (epsilon
    coin flip) and ``np.random.multivariate_normal`` (Thompson sample),
    so it is intentionally stochastic. Tests that need reproducibility
    should seed both RNGs.
    """
    # ── Hard env-flag gate (default OFF) ──────────────────────────
    if os.environ.get("GENLAB_BAYESIAN_GATE_ENABLED", "0") != "1":
        return 0.5, 0.5

    # ── Dep guards ────────────────────────────────────────────────
    if not _HAS_NUMPY or not _HAS_SCIPY:
        return 0.5, 0.5

    # ── ε-greedy floor for warm-up ────────────────────────────────
    decision_n = _bump_decision_count(niche_id)
    if decision_n <= _EPSILON_DECISION_FLOOR and random.random() < _EPSILON_PROB:
        # Uniform on [0, 1] — explores broadly. Mean is reported as
        # 0.5 (the prior mean) so telemetry stays interpretable.
        return float(random.random()), 0.5

    # ── Load posterior + sanity check ─────────────────────────────
    try:
        posterior = load_posterior(niche_id)
    except Exception as exc:
        logger.warning("[bayesian_gate] load failed for niche=%s: %s", niche_id, exc)
        return 0.5, 0.5

    if posterior is None or posterior.n_rows < _MIN_ROWS_PER_NICHE:
        return 0.5, 0.5

    # ── Build feature vector + Thompson sample ────────────────────
    try:
        x = _build_feature_vector(blueprint, decision_dict=decision_dict)
        if not _HAS_NUMPY:
            return 0.5, 0.5
        x = np.asarray(x, dtype=np.float64)
        mean = np.asarray(posterior.mean, dtype=np.float64)
        cov = np.asarray(posterior.cov, dtype=np.float64)

        # Thompson sample: one draw from N(mean, cov)
        w_sample = np.random.multivariate_normal(mean, cov)
        p_sampled = float(_sigmoid(float(w_sample @ x)))
        p_mean = float(_sigmoid(float(mean @ x)))
        # Clamp to [0, 1] — defends against floating-point edge cases.
        p_sampled = max(0.0, min(1.0, p_sampled))
        p_mean = max(0.0, min(1.0, p_mean))
        return p_sampled, p_mean
    except Exception as exc:
        logger.warning("[bayesian_gate] sample failed for niche=%s: %s", niche_id, exc)
        return 0.5, 0.5
