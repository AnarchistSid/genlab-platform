"""Counterfactual-replay observability API (Intervention 7, 2026-07-01).

Read-only endpoint that surfaces the latest DR-replay artifact per
niche. The runner (``scripts/run_counterfactual_replay.py``) fires
monthly on the 1st at 04:30 UTC via
``genlab-counterfactual-replay.timer``; this endpoint just parses
the JSON on disk.

Artifact discovery
==================

The runner writes to
``$GENLAB_TMP/counterfactual-replay/replay-YYYYMMDD-HHMMSS-<niche>.json``.
Multiple artifacts accumulate over time. The endpoint globs by
niche suffix, sorts by mtime, and returns the newest.

We deliberately don't hydrate the artifact into a typed
``DREstimate`` here — the frontend renders whatever JSON the runner
dropped, so schema evolution on the write side is backward
compatible on the read side.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint(
    "counterfactual_replay_api",
    __name__,
    url_prefix="/api/v1/counterfactual-replay",
)

_VALID_NICHES = frozenset({"all", "ai_creators", "gaming", "sports", "movies", "anime"})


def _replay_root() -> Path:
    tmp = os.environ.get("GENLAB_TMP")
    root = Path(tmp) if tmp else Path.cwd() / ".tmp"
    return root / "counterfactual-replay"


def _latest_artifact_for(niche: str) -> Path | None:
    """Return the newest ``replay-*-<niche>.json`` file, or None."""
    root = _replay_root()
    if not root.exists():
        return None
    # Glob pattern deliberately anchored on the niche suffix so
    # cross-niche runs (``replay-*-all.json``) don't leak into a
    # specific-niche query.
    candidates = list(root.glob(f"replay-*-{niche}.json"))
    if not candidates:
        return None
    # Sort by mtime — the runner overwrites within the same second
    # only under artificial conditions; mtime is a monotonic-enough
    # proxy for "most recent run."
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


@bp.route("/latest", methods=["GET"])
def get_latest():
    """Return the latest replay artifact for one niche.

    Query params:

      * ``niche_id`` — one of the 5 shipping niches OR ``"all"`` for
        the cross-niche aggregate the runner produces on
        ``--niche all`` invocations.

    Response shape mirrors the runner's ``_build_report`` output
    verbatim; the frontend renders whatever JSON is on disk. When
    no artifact exists, ``data`` is null.
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

    path = _latest_artifact_for(niche_id)
    if path is None:
        return jsonify(
            {
                "status": "success",
                "data": None,
                "message": f"No counterfactual-replay artifact for {niche_id}",
            }
        )

    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("counterfactual_replay: %s malformed: %s", path.name, exc)
        return jsonify({"status": "success", "data": None})
    return jsonify({"status": "success", "data": payload})
