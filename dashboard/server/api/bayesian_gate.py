"""Bayesian gate state observability endpoint (L4, 2026-07-08 audit).

Read-only endpoint that surfaces the Bayesian LR gate's per-niche
posterior state (mean vector, training size, feature names). Sibling
to ``cross_niche_transfer.py`` — same "observe before you rely on it"
discipline that the intelligence-stack cards use.

Why an endpoint at all
----------------------
The bayesian gate is a shipped-but-observation-only capability. Its
state is a JSON blob on disk that operators can't inspect without
SSH. Surfacing the mean vector + n_rows + feature names on the
dashboard means:

* Operators can eyeball whether the fit looks reasonable before
  enabling the gate (weights too large → likely overfit; weights all
  zero → no training data).
* When the gate does start voting, its verdict has a checkable
  provenance — "why did it approve this?" traces back to the same
  mean vector visible on the card.

We deliberately DO NOT expose the covariance matrix. It's an N x N
nested list per niche that quickly bloats the response, and the mean
is what actually drives decisions. If a future card needs uncertainty
bounds, we add a separate endpoint keyed on niche_id.
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
    "bayesian_gate_api",
    __name__,
    url_prefix="/api/v1/bayesian-gate",
)


def _state_path() -> Path:
    """Resolve the state-file path.

    Mirrors ``genlab_core.learning.bayesian_gate._state_path`` so this
    endpoint reads the SAME file the gate writes. Duplicating the
    resolve rule (rather than importing) keeps the endpoint free of
    the module's numpy dependency — a cold dashboard boot shouldn't
    pull ~40 MB of BLAS.
    """
    custom = os.getenv("BAYESIAN_GATE_STATE_PATH")
    if custom:
        return Path(custom)
    # Walk from this file up to the GenLab root, matching how the
    # module resolves it: src/genlab_core/learning/bayesian_gate.py
    # is 4 parents up from the package's __file__. Dashboard is 2
    # parents up from THIS file's server/api/ location. Adjust
    # accordingly.
    here = Path(__file__).resolve()
    # server/api/bayesian_gate.py → server/api/ → server/ →
    # dashboard/ → GenLab/
    genlab_root = here.parents[3]
    return genlab_root / ".tmp" / "bayesian_gate_state.json"


def _flag_enabled() -> bool:
    """Read the run-time enable flag.

    Uses the SAME comparison pattern as
    ``genlab_core.learning.bayesian_gate`` — literal ``"1"`` only.
    A UX quirk to preserve: the audit doc's H2 explicitly calls out
    the case-sensitivity footgun where ``"true"`` accidentally
    disables features. We mirror the strict check so the badge on
    the dashboard tells the truth about what the gate actually does.
    """
    return os.environ.get("GENLAB_BAYESIAN_GATE_ENABLED", "0").strip() == "1"


def _summarize_niche(niche_id: str, blob: Any) -> dict[str, Any] | None:
    """Reduce one niche's persisted state to a card-friendly summary.

    Discards the covariance matrix (see module docstring), keeps only:
    ``n_rows`` (training set size), ``feature_names`` (so operator can
    read the mean vector without needing to know the layout), and
    ``mean`` (weight per feature).

    Returns None for malformed entries — the endpoint's outer wrap
    filters those out rather than exposing broken shapes.
    """
    if not isinstance(blob, dict):
        return None
    try:
        mean = blob.get("mean")
        feature_names = blob.get("feature_names")
        n_rows = blob.get("n_rows")
        if not isinstance(mean, list) or not isinstance(feature_names, list):
            return None
        # Pin the length-match invariant here so a mismatch surfaces
        # as "no summary" rather than a misleading render.
        if len(mean) != len(feature_names):
            return None
        return {
            "niche_id": niche_id,
            "n_rows": int(n_rows) if isinstance(n_rows, (int, float)) else 0,
            "feature_names": [str(x) for x in feature_names],
            "mean": [float(x) for x in mean],
        }
    except (TypeError, ValueError) as exc:
        logger.debug("bayesian_gate: niche %s summary skipped: %s", niche_id, exc)
        return None


@bp.route("/state", methods=["GET"])
def get_state():
    """Return the current Bayesian gate state per niche.

    Response shape::

        {
          "status": "success",
          "data": {
            "flag_enabled": false,
            "state_path": "/opt/genlab/.tmp/bayesian_gate_state.json",
            "niches": [
              {
                "niche_id": "gaming",
                "n_rows": 47,
                "feature_names": ["composite_score", ...],
                "mean": [0.42, ...]
              },
              ...
            ]
          }
        }

    ``data.niches`` is an empty list when the state file is missing
    or empty (cold-start). Frontend renders "No posterior yet"
    consistent with the intelligence-stack card pattern.
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
                    "niches": [],
                },
                "message": "No Bayesian gate posterior yet",
            }
        )

    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("bayesian_gate: state file malformed: %s", exc)
        return jsonify(
            {
                "status": "success",
                "data": {
                    "flag_enabled": flag,
                    "state_path": str(path),
                    "niches": [],
                },
                "message": "Bayesian gate state artifact unreadable",
            }
        )

    if not isinstance(raw, dict):
        return jsonify(
            {
                "status": "success",
                "data": {
                    "flag_enabled": flag,
                    "state_path": str(path),
                    "niches": [],
                },
                "message": "Bayesian gate state shape unexpected",
            }
        )

    niches: list[dict[str, Any]] = []
    for niche_id, blob in raw.items():
        summary = _summarize_niche(niche_id, blob)
        if summary is not None:
            niches.append(summary)

    # Stable ordering so the frontend doesn't rearrange rows on each
    # poll. Alphabetical by niche_id matches sibling cards
    # (SponsorshipReadinessCard, AutoApprovalCalibrationCard).
    niches.sort(key=lambda x: x["niche_id"])

    return jsonify(
        {
            "status": "success",
            "data": {
                "flag_enabled": flag,
                "state_path": str(path),
                "niches": niches,
            },
        }
    )
