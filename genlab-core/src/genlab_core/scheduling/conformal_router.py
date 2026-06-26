"""Conformal selective prediction router (Engine 1.2).

Today the auto-approval gate returns a binary verdict (approved/rejected)
with a heuristic confidence in [0,1]. The operator reviews every
VISUAL_READY blueprint (~17/day) and clicks approve/reject. Across the
past ~13 days the table ``auto_approval_calibration`` has accumulated
~263 (gate verdict, operator action) pairs.

This module adds a THIRD verdict — **ABSTAIN** — when the model is
uncertain. The mechanism is *split conformal classification*
(Angelopoulos & Bates, "A Gentle Intro to Conformal Prediction and
Distribution-Free Uncertainty Quantification", 2023):

  1. Train a base classifier ``f(x) → P(approved | x)`` on the
     proper-training half of the calibration table.
  2. On the held-out calibration half, compute the non-conformity
     score for each row: ``s_i = 1 - f(x_i)[y_i]``.
  3. Take ``q̂`` as the ``⌈(n+1)(1-α)⌉/n`` quantile of those scores.
  4. At inference: return the prediction set
     ``{y : 1 - f(x)[y] ≤ q̂}``. Singletons ({approved} or
     {rejected}) get auto-decided; doubletons ({approved, rejected})
     route to the operator (ABSTAIN).

The marginal coverage guarantee is distribution-free:
``P(y* ∈ Ĉ(x*)) ≥ 1 − α`` for the chosen α (default 0.10 → 90%
coverage). We stratify by ``niche_id`` (Mondrian conformal) so each
niche gets its own ``q̂`` — marginal coverage doesn't promise
per-niche coverage on its own.

## Safety defaults

- **Env-flag gated**: ``GENLAB_CONFORMAL_ROUTER_ENABLED`` must be set
  to ``"1"``. Default OFF → always returns ``route_to_operator``
  (no change to current behavior).
- **Per-niche cold-start guard**: niches with fewer than
  ``MIN_NICHE_SAMPLES`` (50) calibration rows return
  ``route_to_operator`` regardless of the prediction set — small-n
  guarantees are too noisy to act on.
- **Fail-OPEN**: any DB / sklearn / numpy failure returns
  ``route_to_operator``. The operator's queue is the safe default;
  the worst-case outcome of a router failure is "operator does the
  same work they did yesterday".

## Public surface

  route_blueprint(blueprint) -> ConformalRouteResult
    Inference entry point. Reads cached per-niche state if present,
    otherwise returns ``route_to_operator`` (cold-start).

  refit_from_calibration(*, niche_id=None, state_path=None) -> bool
    Trains the base model + computes the calibration quantile, then
    persists state to ``state_path`` (default
    ``$GENLAB_CONFORMAL_STATE_PATH`` or
    ``./conformal_router_state.json``). Intended to be called from
    a nightly systemd timer.

## References

Angelopoulos & Bates 2023 — "A Gentle Intro to Conformal Prediction
and Distribution-Free Uncertainty Quantification" (arXiv:2107.07511).
See ``docs/AGENT-LEARNING-ENGINES.md`` Engine 1.2 for the rollout
plan and the 30-50% operator-click-reduction prediction.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────
# Coverage parameter — α=0.10 means 90% marginal coverage. Conservative
# default; an operator can lower α (tighter coverage, more abstentions)
# or raise it (looser coverage, fewer abstentions) per-niche later.
DEFAULT_ALPHA: Final[float] = 0.10

# Below this row count we refuse to auto-decide for a niche. The
# 1/(n+1) coverage gap at n=50 is ~2% — tolerable. At n=10 it's
# ~9% — too noisy for an autonomous decision. 50 is the floor under
# which we hand back to the operator regardless of the prediction set.
MIN_NICHE_SAMPLES: Final[int] = 50

# State file path. Per-niche serialized state lives under
# ``state[<niche_id>]`` so a single file holds all 5 niches.
_DEFAULT_STATE_FILENAME: Final[str] = "conformal_router_state.json"

# Env flag — opt-in activation. Unset / not "1" → no-op.
_ENABLE_ENV_VAR: Final[str] = "GENLAB_CONFORMAL_ROUTER_ENABLED"
_STATE_PATH_ENV_VAR: Final[str] = "GENLAB_CONFORMAL_STATE_PATH"

# In-memory cache so the inference path doesn't re-parse the JSON file
# on every blueprint. The nightly refit invalidates the cache.
_STATE_CACHE: dict[str, Any] | None = None
_STATE_CACHE_PATH: str | None = None
_STATE_LOCK = threading.Lock()


# ── Public dataclasses ────────────────────────────────────────────────


@dataclass(frozen=True)
class ConformalRouteResult:
    """Outcome of routing a single blueprint through the conformal layer.

    Fields:
        action: One of ``"auto_approve"``, ``"auto_reject"``,
            ``"route_to_operator"``. The downstream auto_approver
            consumes this to decide whether to skip the focus-review
            queue.
        prediction_set: The labels admitted at the chosen α. A
            singleton means the model is confident; a doubleton
            means abstain.
        q_hat: The non-conformity quantile from the niche's
            calibration set — useful for the dashboard "model is
            X% confident" badge.
        alpha: The α used for this decision. Echoes the configured
            default unless an override is plumbed in a later PR.
        reason: Human-readable trace for operator inspection.
    """

    action: str
    prediction_set: list[str]
    q_hat: float
    alpha: float
    reason: str = ""


# ── Inference path ────────────────────────────────────────────────────


def _is_enabled() -> bool:
    """Return True iff the env flag is explicitly set to "1".

    Mirrors the conservative pattern from
    ``auto_approver.AUTO_APPROVER_ENABLED_ENV`` — only the literal
    string "1" enables. "true"/"yes"/"on" are intentionally NOT
    accepted so a typo in the runbook can't flip the switch.
    """
    return os.environ.get(_ENABLE_ENV_VAR, "0").strip() == "1"


def _state_path() -> str:
    """Resolve the state JSON path.

    Order: ``$GENLAB_CONFORMAL_STATE_PATH`` env var > module default
    in the current working directory. The default keeps the file
    discoverable for the nightly timer without needing operator
    config; production deployments should set the env var to a
    persistent path under e.g. ``/var/lib/genlab/``.
    """
    return os.environ.get(_STATE_PATH_ENV_VAR) or _DEFAULT_STATE_FILENAME


def _load_state(force: bool = False) -> dict[str, Any]:
    """Return the per-niche state dict from disk (cached).

    Shape: ``{"alpha": <float>, "niches": {<niche_id>: {<niche_state>}}}``.
    Empty dict on any error (file missing, JSON broken, etc.).
    """
    global _STATE_CACHE, _STATE_CACHE_PATH

    path = _state_path()
    with _STATE_LOCK:
        if not force and _STATE_CACHE is not None and _STATE_CACHE_PATH == path:
            return _STATE_CACHE
        try:
            with open(path) as f:
                state = json.load(f)
            if not isinstance(state, dict):
                state = {}
        except (OSError, ValueError, TypeError):
            state = {}
        _STATE_CACHE = state
        _STATE_CACHE_PATH = path
        return state


def _invalidate_state_cache() -> None:
    """Forget the cached state — used after a fresh refit."""
    global _STATE_CACHE, _STATE_CACHE_PATH
    with _STATE_LOCK:
        _STATE_CACHE = None
        _STATE_CACHE_PATH = None


def _route_to_operator(
    reason: str, q_hat: float = 0.0, alpha: float = DEFAULT_ALPHA
) -> ConformalRouteResult:
    """Helper: build a safe fall-through result."""
    return ConformalRouteResult(
        action="route_to_operator",
        prediction_set=["approved", "rejected"],
        q_hat=q_hat,
        alpha=alpha,
        reason=reason,
    )


def _extract_features(blueprint: dict) -> list[float] | None:
    """Pull the 7-feature vector from a blueprint dict.

    Returns None if any non-defaultable feature is missing in a way
    we can't tolerate. Missing numeric scores get sensible defaults
    so the cold-start case (missing virality / composite) still
    flows through; missing hook gets length 0.
    """
    try:
        extra = blueprint.get("extra") or {}
        if not isinstance(extra, dict):
            extra = {}
        composite = _safe_float(extra.get("composite_score"), default=0.0)
        virality = _safe_float(extra.get("virality_score"), default=0.0)
        hook_clf = _safe_float(extra.get("hook_classifier_score"), default=0.5)
        hook_text = blueprint.get("hook_text") or ""
        if not isinstance(hook_text, str):
            hook_text = str(hook_text)
        hook_len = float(len(hook_text))
        # gate_confidence is passed in by upstream callers OR derived
        # from a fresh evaluate() in real wiring. Tests inject it via
        # extra["gate_confidence"]. Default 0.5 = neutral prior.
        gate_conf = _safe_float(extra.get("gate_confidence"), default=0.5)
        n_passed = float(len(extra.get("gate_passed_checks") or []))
        n_failed = float(len(extra.get("gate_failed_checks") or []))
        return [composite, virality, hook_clf, hook_len, n_passed, n_failed, gate_conf]
    except Exception as exc:  # noqa: BLE001
        logger.debug("[conformal] feature extraction failed: %s", exc)
        return None


def _safe_float(value: Any, *, default: float) -> float:
    """Coerce to float; return default on any failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _sigmoid(z: float) -> float:
    """Numerically-stable logistic function for scoring at inference."""
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def _predict_proba_approved(weights: list[float], intercept: float, features: list[float]) -> float:
    """Logistic regression P(y=approved | x) using stored weights.

    Implemented in pure Python so the inference hot path doesn't
    need sklearn loaded — sklearn is only required at refit time.
    Weights and features must be the same length.
    """
    if len(weights) != len(features):
        raise ValueError(f"weights len {len(weights)} != features len {len(features)}")
    z = intercept + sum(w * x for w, x in zip(weights, features, strict=True))
    return _sigmoid(z)


def route_blueprint(blueprint: dict) -> ConformalRouteResult:
    """Route one blueprint through the conformal layer.

    Returns a :class:`ConformalRouteResult`. Pure function (modulo the
    state-cache read on first call) — safe to call from hot paths.

    Safety layers (in order):
      1. Env flag unset → always route_to_operator.
      2. Cold-start (missing state file or niche not trained) →
         route_to_operator.
      3. Niche has fewer than ``MIN_NICHE_SAMPLES`` rows → route_to_operator.
      4. Any feature extraction / scoring error → route_to_operator
         (fail-OPEN).
    """
    if not _is_enabled():
        return _route_to_operator("disabled: GENLAB_CONFORMAL_ROUTER_ENABLED != 1")

    niche_id = (blueprint.get("niche_id") or "").strip()
    if not niche_id:
        return _route_to_operator("missing niche_id")

    state = _load_state()
    alpha = _safe_float(state.get("alpha"), default=DEFAULT_ALPHA)
    niches = state.get("niches") or {}
    niche_state = niches.get(niche_id) if isinstance(niches, dict) else None
    if not isinstance(niche_state, dict):
        return _route_to_operator(f"cold-start: no trained state for niche={niche_id}", alpha=alpha)

    sample_count = int(niche_state.get("sample_count") or 0)
    if sample_count < MIN_NICHE_SAMPLES:
        return _route_to_operator(
            f"insufficient calibration data: niche={niche_id} has {sample_count} rows "
            f"(min {MIN_NICHE_SAMPLES})",
            alpha=alpha,
        )

    try:
        weights = niche_state.get("weights")
        intercept = float(niche_state.get("intercept") or 0.0)
        q_hat = float(niche_state.get("q_hat") or 0.0)
        if not isinstance(weights, list) or not weights:
            return _route_to_operator(f"malformed state for niche={niche_id}", alpha=alpha)

        features = _extract_features(blueprint)
        if features is None:
            return _route_to_operator(
                "feature extraction failed (fail-open)", q_hat=q_hat, alpha=alpha
            )

        p_approved = _predict_proba_approved(list(weights), intercept, features)
        p_rejected = 1.0 - p_approved

        # Non-conformity scores: s(x, y) = 1 - f(x)[y]
        # Prediction set: { y : s(x, y) <= q_hat }
        admitted: list[str] = []
        if (1.0 - p_approved) <= q_hat:
            admitted.append("approved")
        if (1.0 - p_rejected) <= q_hat:
            admitted.append("rejected")
        # Guarantee non-emptiness: when q_hat is so small that neither
        # label is admitted, admit the higher-probability label so the
        # set is never empty (degenerate edge case at extreme alpha).
        if not admitted:
            admitted.append("approved" if p_approved >= 0.5 else "rejected")

        if len(admitted) == 2:
            return ConformalRouteResult(
                action="route_to_operator",
                prediction_set=admitted,
                q_hat=q_hat,
                alpha=alpha,
                reason=(
                    f"ABSTAIN: prediction set is doubleton at α={alpha:.2f} "
                    f"(p_approved={p_approved:.3f}, q̂={q_hat:.3f})"
                ),
            )
        if admitted == ["approved"]:
            return ConformalRouteResult(
                action="auto_approve",
                prediction_set=admitted,
                q_hat=q_hat,
                alpha=alpha,
                reason=(
                    f"singleton {{approved}} at α={alpha:.2f} "
                    f"(p_approved={p_approved:.3f}, q̂={q_hat:.3f})"
                ),
            )
        # admitted == ["rejected"]
        return ConformalRouteResult(
            action="auto_reject",
            prediction_set=admitted,
            q_hat=q_hat,
            alpha=alpha,
            reason=(
                f"singleton {{rejected}} at α={alpha:.2f} "
                f"(p_approved={p_approved:.3f}, q̂={q_hat:.3f})"
            ),
        )
    except Exception as exc:  # noqa: BLE001 — fail-OPEN by contract
        logger.warning("[conformal] inference failed for niche=%s: %s", niche_id, exc)
        return _route_to_operator(f"inference error: {exc}")


# ── Refit path ────────────────────────────────────────────────────────


def _fetch_training_rows(*, niche_id: str | None = None) -> list[dict]:
    """Read calibration + blueprint feature rows from Postgres.

    Returns a list of dicts shaped like:
        {
          "niche_id": <str>,
          "label": 1 | 0,   # 1 = operator approved
          "features": [composite, virality, hook_clf, hook_len,
                       n_passed, n_failed, gate_confidence],
        }

    Empty list on any failure (no DB, table missing, network error).
    The refit caller treats empty as "no work to do; skip this niche".
    """
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.info("[conformal] DATABASE_URL not set — refit skipped")
        return []

    try:
        import psycopg  # noqa: F401
    except ImportError:
        logger.warning("[conformal] psycopg not installed — refit skipped")
        return []

    from genlab_core.storage.tenant_context import pg_connect

    # Admin-mode for the cross-niche query; per-niche refits set
    # niche_id to scope RLS to that tenant.
    rls_niche = niche_id or "all"

    sql = """
        SELECT
            c.niche_id,
            c.operator_action,
            c.gate_confidence,
            c.gate_passed_checks,
            c.gate_failed_checks,
            b.extra
        FROM auto_approval_calibration c
        LEFT JOIN blueprints b ON b.id::text = c.blueprint_id
        WHERE c.gate_approved IS NOT NULL
          AND c.blueprint_id LIKE '________-____-____-____-____________'
    """
    params: tuple
    if niche_id:
        sql += " AND c.niche_id = %s"
        params = (niche_id,)
    else:
        params = ()

    rows: list[dict] = []
    try:
        with pg_connect(dsn, niche_id=rls_niche, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                for row in cur.fetchall():
                    (
                        n_id,
                        op_action,
                        gate_conf,
                        passed_checks,
                        failed_checks,
                        extra,
                    ) = row

                    if not isinstance(extra, dict):
                        # JSONB normally arrives as dict via psycopg's
                        # default decoder; if it's a string (older
                        # backends) attempt to decode.
                        try:
                            extra = json.loads(extra) if extra else {}
                        except (TypeError, ValueError):
                            extra = {}

                    # Build feature vector with the same shape as
                    # _extract_features so train + infer are aligned.
                    composite = _safe_float(extra.get("composite_score"), default=0.0)
                    virality = _safe_float(extra.get("virality_score"), default=0.0)
                    hook_clf = _safe_float(extra.get("hook_classifier_score"), default=0.5)
                    hook_text = extra.get("hook_text") or ""
                    hook_len = float(len(hook_text) if isinstance(hook_text, str) else 0)
                    n_passed = float(len(passed_checks or []))
                    n_failed = float(len(failed_checks or []))
                    gate_conf_f = _safe_float(gate_conf, default=0.5)

                    label = 1 if op_action == "approved" else 0

                    rows.append(
                        {
                            "niche_id": n_id,
                            "label": label,
                            "features": [
                                composite,
                                virality,
                                hook_clf,
                                hook_len,
                                n_passed,
                                n_failed,
                                gate_conf_f,
                            ],
                        }
                    )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[conformal] training-row fetch failed: %s", exc)
        return []

    return rows


def fit_niche_state(
    rows: list[dict],
    *,
    alpha: float = DEFAULT_ALPHA,
    random_state: int = 0,
) -> dict[str, Any] | None:
    """Train one niche's base model + compute its conformal quantile.

    ``rows`` is a list of ``{"label": 0|1, "features": [...]}`` dicts
    (the ``niche_id`` is implied by the caller's grouping). Returns
    the serializable niche-state dict on success, None when the input
    is too small or degenerate to fit.

    Implementation details:
      - 50/50 split between proper-training and calibration set
        (per the standard split-conformal setup).
      - Logistic regression with L2 regularization (sklearn default
        ``C=1.0``). At ~130 training rows this fits in <50ms.
      - Non-conformity score ``s_i = 1 − f(x_i)[y_i]``.
      - Quantile level ``⌈(n+1)(1-α)⌉/n``; on degenerate small n we
        clamp to 1.0 (the most-permissive quantile).
    """
    if len(rows) < MIN_NICHE_SAMPLES:
        logger.info(
            "[conformal] niche has %d rows < min %d — skipping fit",
            len(rows),
            MIN_NICHE_SAMPLES,
        )
        return None

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        logger.warning("[conformal] sklearn/numpy not installed — refit skipped")
        return None

    X = np.array([r["features"] for r in rows], dtype=float)
    y = np.array([r["label"] for r in rows], dtype=int)

    # Both classes must be present in the proper-training half OR the
    # logistic regression refuses to fit. Reject degenerate inputs.
    if len(set(y)) < 2:
        logger.info(
            "[conformal] only one class present — skipping fit (need both approved + rejected)"
        )
        return None

    rng = np.random.default_rng(random_state)
    n = len(rows)
    idx = rng.permutation(n)
    split = n // 2
    train_idx = idx[:split]
    calib_idx = idx[split:]

    X_train, y_train = X[train_idx], y[train_idx]
    X_calib, y_calib = X[calib_idx], y[calib_idx]

    if len(set(y_train)) < 2:
        # Resample: if the random split produced a degenerate training
        # set, fall back to using all rows for training (no held-out
        # calibration → can't compute q_hat). Refuse rather than ship
        # a meaningless quantile.
        logger.info("[conformal] degenerate split (training set single-class) — skipping fit")
        return None

    model = LogisticRegression(max_iter=1000, random_state=random_state)
    model.fit(X_train, y_train)

    # P(y=1 | x) on the calibration set
    # sklearn LR exposes classes_ — find index of label==1.
    classes = list(model.classes_)
    one_idx = classes.index(1)
    proba_calib = model.predict_proba(X_calib)[:, one_idx]
    # Non-conformity for each calibration point: 1 - f(x)[y_true]
    p_true = np.where(y_calib == 1, proba_calib, 1.0 - proba_calib)
    nonconformity = 1.0 - p_true

    n_calib = len(nonconformity)
    if n_calib < 1:
        return None

    # Quantile level per the standard split-conformal formula:
    # ⌈(n+1)(1-α)⌉ / n. Clamp to [0, 1] for the very-small-n edge case.
    q_level = math.ceil((n_calib + 1) * (1 - alpha)) / n_calib
    q_level = min(1.0, max(0.0, q_level))
    if q_level >= 1.0:
        # All calibration scores admitted — use the max non-conformity.
        q_hat = float(nonconformity.max())
    else:
        q_hat = float(np.quantile(nonconformity, q_level, method="higher"))

    # sklearn LR is binary → single row of coefficients. Convert to
    # a list[float] for JSON-serializable storage.
    coef = model.coef_[0].tolist()
    intercept = float(model.intercept_[0])

    return {
        "sample_count": n,
        "n_train": int(split),
        "n_calib": int(n_calib),
        "alpha": alpha,
        "q_hat": q_hat,
        "weights": coef,
        "intercept": intercept,
        "feature_names": [
            "composite_score",
            "virality_score",
            "hook_classifier_score",
            "hook_text_len",
            "n_passed_checks",
            "n_failed_checks",
            "gate_confidence",
        ],
    }


def refit_from_calibration(
    *,
    state_path: str | None = None,
    alpha: float = DEFAULT_ALPHA,
    random_state: int = 0,
) -> bool:
    """Refit per-niche state from the calibration table; persist to disk.

    Intended to run nightly as a systemd timer (analogous to
    ``niche_pause_sweeper``). Returns True iff at least one niche was
    successfully fit + the state file was written. False on any
    upstream failure (no DB, no qualifying data, write error).

    The state file holds all niches in a single JSON blob (~5KB per
    niche) so the inference path opens one file regardless of how many
    niches exist. The path is resolved via:
        explicit ``state_path`` kwarg > ``$GENLAB_CONFORMAL_STATE_PATH``
        > module default (``conformal_router_state.json`` in CWD).
    """
    target = state_path or _state_path()

    all_rows = _fetch_training_rows()
    if not all_rows:
        logger.info("[conformal] no training rows available — refit aborted")
        return False

    # Group rows by niche_id.
    by_niche: dict[str, list[dict]] = {}
    for r in all_rows:
        by_niche.setdefault(r["niche_id"], []).append(r)

    niches_state: dict[str, dict[str, Any]] = {}
    for niche_id, rows in by_niche.items():
        fit = fit_niche_state(rows, alpha=alpha, random_state=random_state)
        if fit is not None:
            niches_state[niche_id] = fit
            logger.info(
                "[conformal] fit niche=%s n=%d q_hat=%.4f",
                niche_id,
                fit["sample_count"],
                fit["q_hat"],
            )

    if not niches_state:
        logger.info("[conformal] no niches met the minimum sample size — nothing written")
        return False

    payload = {"alpha": alpha, "niches": niches_state}
    try:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
    except OSError as exc:
        logger.warning("[conformal] failed to write state file %s: %s", target, exc)
        return False

    _invalidate_state_cache()
    logger.info("[conformal] state file written: %s (niches=%d)", target, len(niches_state))
    return True
