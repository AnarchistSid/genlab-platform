"""Conformal router state observability endpoint (L4, 2026-07-08 audit).

Read-only endpoint that surfaces the conformal router's per-niche
state: sample counts, q_hat quantile, alpha, feature-weight vector.
Sibling to ``bayesian_gate.py`` — same "observe before you rely on
it" pattern the intelligence-stack cards use.

Why an endpoint at all
----------------------
The conformal router is a shipped-but-observation-only capability.
Its state file lives on disk and operators can't inspect what the
nightly refit produced without SSH. Surfacing the per-niche q_hat +
sample count on the dashboard means:

* Operators can eyeball whether the calibration set has enough
  data (``sample_count >= MIN_NICHE_SAMPLES=50`` = auto-decides;
  under that = fail-open to operator regardless of prediction set).
* When the router does start voting, its verdict has a checkable
  provenance — "why is anime routed to operator so often?" traces
  back to a small sample_count visible on the card.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "conformal_router_api",
    __name__,
    url_prefix="/api/v1/conformal-router",
)

# Mirrors ``conformal_router.MIN_NICHE_SAMPLES`` — duplicated here to
# keep the endpoint import-free of the module. If the module ever
# changes this threshold, update BOTH sites; the pin test enforces
# the value.
_MIN_NICHE_SAMPLES: int = 50


def _state_path() -> Path:
    """Resolve the state-file path.

    Mirrors ``conformal_router._state_path`` so this endpoint reads
    the SAME file the router writes. Duplicating the rule (rather
    than importing) keeps the endpoint free of the module's numpy
    dependency — the same discipline the bayesian-gate endpoint uses.
    """
    custom = os.environ.get("GENLAB_CONFORMAL_STATE_PATH")
    if custom:
        return Path(custom)
    return Path.cwd() / "conformal_router_state.json"


def _flag_enabled() -> bool:
    """Read the run-time enable flag.

    Uses the same strict ``"1"`` comparison the module does. Any
    other value (``"true"``, ``"yes"``, empty) reads as disabled.
    Matches the audit's H2 case-sensitivity guard.
    """
    return (
        os.environ.get("GENLAB_CONFORMAL_ROUTER_ENABLED", "0").strip() == "1"
    )


def _summarize_niche(niche_id: str, blob: Any) -> dict[str, Any] | None:
    """Reduce one niche's persisted state to a card-friendly summary.

    Keeps ``sample_count``, ``n_train``, ``n_calib``, ``alpha``,
    ``q_hat``, ``feature_names``, plus a boolean ``ready`` derived
    from the sample threshold. Discards ``weights`` + ``intercept``
    to keep the payload small — those are only useful for a
    detailed drill-down that isn't in this PR's scope.

    Returns None for malformed entries.
    """
    if not isinstance(blob, dict):
        return None
    try:
        sample_count = int(blob.get("sample_count") or 0)
        return {
            "niche_id": niche_id,
            "sample_count": sample_count,
            "n_train": int(blob.get("n_train") or 0),
            "n_calib": int(blob.get("n_calib") or 0),
            "alpha": float(blob.get("alpha") or 0.0),
            "q_hat": float(blob.get("q_hat") or 0.0),
            "feature_names": [
                str(x) for x in blob.get("feature_names") or []
            ],
            # ``ready`` mirrors the module's decision — under this
            # threshold the router fails open regardless of q_hat.
            # Surfacing this makes the card badge unambiguous.
            "ready": sample_count >= _MIN_NICHE_SAMPLES,
        }
    except (TypeError, ValueError) as exc:
        logger.debug(
            "conformal_router: niche %s summary skipped: %s", niche_id, exc
        )
        return None


@bp.route("/state", methods=["GET"])
def get_state():
    """Return the current conformal router state per niche.

    Response shape::

        {
          "status": "success",
          "data": {
            "flag_enabled": false,
            "state_path": "/opt/genlab/conformal_router_state.json",
            "min_niche_samples": 50,
            "niches": [
              {
                "niche_id": "gaming",
                "sample_count": 87,
                "n_train": 45,
                "n_calib": 42,
                "alpha": 0.10,
                "q_hat": 0.234,
                "feature_names": ["composite_score", ...],
                "ready": true
              },
              ...
            ]
          }
        }

    ``data.niches`` is empty when the state file is missing.
    """
    path = _state_path()
    flag = _flag_enabled()

    if not path.exists():
        return jsonify(
            {
                "status": "success",
                "data": {
                    "flag_enabled": flag,
                    "state_path": str(path),
                    "min_niche_samples": _MIN_NICHE_SAMPLES,
                    "niches": [],
                },
                "message": "No conformal router state yet",
            }
        )

    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("conformal_router: state file malformed: %s", exc)
        return jsonify(
            {
                "status": "success",
                "data": {
                    "flag_enabled": flag,
                    "state_path": str(path),
                    "min_niche_samples": _MIN_NICHE_SAMPLES,
                    "niches": [],
                },
                "message": "Conformal router state artifact unreadable",
            }
        )

    if not isinstance(raw, dict):
        return jsonify(
            {
                "status": "success",
                "data": {
                    "flag_enabled": flag,
                    "state_path": str(path),
                    "min_niche_samples": _MIN_NICHE_SAMPLES,
                    "niches": [],
                },
                "message": "Conformal router state shape unexpected",
            }
        )

    # The router's state has shape:
    #   {"alpha": <float>, "niches": {<niche_id>: {...}}}
    # `alpha` at the top level is the DEFAULT; individual niches may
    # override. The per-niche shape lives under "niches".
    niches_dict = raw.get("niches") or {}
    if not isinstance(niches_dict, dict):
        niches_dict = {}

    niches: list[dict[str, Any]] = []
    for niche_id, blob in niches_dict.items():
        summary = _summarize_niche(niche_id, blob)
        if summary is not None:
            niches.append(summary)

    niches.sort(key=lambda x: x["niche_id"])

    return jsonify(
        {
            "status": "success",
            "data": {
                "flag_enabled": flag,
                "state_path": str(path),
                "min_niche_samples": _MIN_NICHE_SAMPLES,
                "niches": niches,
            },
        }
    )
