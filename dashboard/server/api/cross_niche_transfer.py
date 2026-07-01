"""Cross-niche prior transfer API (Intervention 2 observability, 2026-07-01).

Read-only endpoint that surfaces the priors artifact written by the
weekly ``genlab-cross-niche-transfer.timer`` runner. Gives the
operator a "validate before flip" surface: they can eyeball whether
the transferred (α, β) values look reasonable before flipping
``GENLAB_CROSS_NICHE_TRANSFER_ENABLED=true`` on prod.

The endpoint is intentionally decoupled from the module's read
function ``get_transferred_prior`` — the module's function respects
the flag (returns None when off), but the operator NEEDS to see the
priors during observation mode. So the endpoint always returns
whatever the runner wrote, regardless of flag state; the frontend
tells the operator via a badge whether the flag is on.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint(
    "cross_niche_transfer_api",
    __name__,
    url_prefix="/api/v1/cross-niche-transfer",
)


def _priors_path() -> Path:
    """Mirror the module's resolve rule so this endpoint finds
    exactly what the runner writes."""
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "cross-niche-transfer" / "priors.json"


@bp.route("/priors", methods=["GET"])
def get_priors():
    """Return the current cross-niche transferred priors.

    Response shape:

        {
          "status": "success",
          "data": {
            "generated_at": "2026-07-07T05:30:00+00:00",
            "flag_enabled": true,
            "priors": {
              "revelation": {
                "style": "revelation",
                "alpha": 2.4,
                "beta": 13.6,
                "observed_rate_mean": 0.15,
                "observed_rate_variance": 0.02,
                "n_niches": 3,
                "reasons": [...]
              },
              ...
            }
          }
        }

    ``data`` is null when the runner hasn't fired yet (cold-start /
    first Monday after enable). Frontend renders as "No priors yet".
    """
    path = _priors_path()
    if not path.exists():
        return jsonify(
            {
                "status": "success",
                "data": None,
                "message": "No cross-niche priors artifact yet",
            }
        )
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("cross_niche_transfer: priors artifact malformed: %s", exc)
        return jsonify({"status": "success", "data": None})

    # Enrich with the current flag state so the frontend can badge
    # "observation only" vs "active" without a separate endpoint.
    flag_on = os.environ.get("GENLAB_CROSS_NICHE_TRANSFER_ENABLED", "") in (
        "true",
        "TRUE",
        "True",
    )
    if isinstance(payload, dict):
        payload["flag_enabled"] = flag_on

    return jsonify({"status": "success", "data": payload})
