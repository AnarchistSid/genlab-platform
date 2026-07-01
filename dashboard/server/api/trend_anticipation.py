"""Trend anticipation API (Intervention 5 Session 3, 2026-07-01).

Read-only endpoint that surfaces today's anticipation artifact per
niche. The artifact is written by ``scripts/anticipate_trends.py``
(fired daily at 03:30 UTC by the ``genlab-anticipate-trends.timer``
systemd unit); this endpoint just parses and returns it.

Endpoints (all under ``/api/v1/trend-anticipation``)
====================================================

* ``GET /latest?niche_id=<X>`` — today's ranking for one niche.
  Returns ``{data: null}`` when today's artifact doesn't exist yet
  (runner hasn't fired, first-run day, or DB-side issue). Frontend
  renders this as "No ranking yet" — same defensive shape as the
  Strategist reports endpoint from PR Strategist-2.

Cold-start / observability semantics
====================================

The artifact is written whether or not
``GENLAB_TREND_ANTICIPATION_ENABLED`` is on — the endpoint just
mirrors what's on disk. The frontend uses the ``flag_enabled`` field
inside the artifact to differentiate "observation-only shipped
scores" from "shipped scores that the pipeline is actually steering
on." That lets the operator watch the score history accumulate
without committing to pipeline steering.

No POST / DELETE — this is a read-only surface. Runner state is
managed by the systemd timer, not the operator.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint("trend_anticipation_api", __name__, url_prefix="/api/v1/trend-anticipation")

_VALID_NICHES = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})


def _artifact_root() -> Path:
    """Same resolve rule the runner + tests use — matches the trend
    anticipation module's persistence path so the read side finds
    what the write side dropped."""
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "trend-anticipation"


def _read_todays_artifact(niche_id: str) -> dict[str, Any] | None:
    """Load today's artifact for one niche. Returns None when the
    file doesn't exist or the JSON is malformed."""
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    path = _artifact_root() / f"{stamp}-{niche_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "trend_anticipation: artifact for %s/%s malformed: %s",
            stamp,
            niche_id,
            exc,
        )
        return None


@bp.route("/latest", methods=["GET"])
def get_latest():
    """Return today's anticipation ranking for one niche.

    Response shape:

        {
          "status": "success",
          "data": {
            "generated_at": "2026-07-01T03:30:00+00:00",
            "niche_id": "gaming",
            "flag_enabled": false,
            "ranking": [
              {"topic": "..", "composite_score": 0.82, "confidence": 0.75,
               "anticipated_peak_hours_ahead": 12, "signals": {...},
               "reasons": [...]},
              ...
            ]
          }
        }

    ``data`` is ``null`` when today's artifact doesn't exist —
    frontend renders "No ranking yet" without special-casing errors.
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if niche_id not in _VALID_NICHES:
        return (
            jsonify(
                {
                    "status": "error",
                    "message": f"Invalid niche_id (allowed: {sorted(_VALID_NICHES)})",
                }
            ),
            400,
        )

    artifact = _read_todays_artifact(niche_id)
    if artifact is None:
        return jsonify(
            {
                "status": "success",
                "data": None,
                "message": f"No trend anticipation artifact for {niche_id} today",
            }
        )
    return jsonify({"status": "success", "data": artifact})
