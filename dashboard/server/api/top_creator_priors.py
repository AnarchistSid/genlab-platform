"""Top-creator priors API (B.2 + B.3 observability, 2026-07-08).

Read-only endpoint that surfaces the A + B intelligence stack's
artifacts to Mission Control:

* B.2's per-niche correlations (Spearman r for each of B.1's 13
  structural features vs view velocity on the mostPopular chart)
* A.2's per-niche latest-uploads snapshots (which watchlisted
  creators posted about what, with recency)

Both artifacts are read directly from disk — the endpoint mirrors
what the runners wrote. The consumer wire (arm-instantiation) is
deferred, so this card is the operator's only visibility into
whether B.2 is producing coherent correlations week-over-week and
whether A.2's watchlists are catching relevant uploads.

Endpoints (all under ``/api/v1/top-creator-priors``)
====================================================

* ``GET /latest?niche_id=<X>`` — B.2's most recent correlations
  artifact for one niche. Returns today's artifact preferred; falls
  back to the 8-day window (matches ``top_creator_priors.load_
  correlations``). Returns ``{data: null}`` when nothing in window.

* ``GET /uploads?niche_id=<X>`` — A.2's most recent uploads artifact
  for one niche. Prefers today's; falls back yesterday's.
  ``{data: null}`` when nothing.

Both endpoints return ``flag_enabled`` bit so the frontend can
render the "active" vs "observation only" badge. This is the same
pattern established by ``trend_anticipation.py`` (Intervention 5)
and ``cross_niche_transfer.py`` (Intervention 2).

Cold-start / observability semantics
====================================

Artifacts are written whether or not the associated flag
(``GENLAB_TOP_CREATOR_PRIORS_ENABLED``,
``GENLAB_TOP_CREATORS_ENABLED``) is on — the endpoint just mirrors
what's on disk. The frontend uses the ``flag_enabled`` field to
differentiate observation-only from active-steering.

No POST / DELETE — read-only surface. Runner state is managed by
systemd timers, not the operator.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint(
    "top_creator_priors_api",
    __name__,
    url_prefix="/api/v1/top-creator-priors",
)

_VALID_NICHES = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})

# B.2's fallback window (matches ``top_creator_priors.load_correlations``
# — kept in sync so the operator sees the same artifact the consumer
# would see). Weekly cadence means we need to look back a full week
# plus a day of slack.
_PRIORS_FALLBACK_DAYS = 8

# A.2's fallback window (matches A.3's ``_signal_creator_upload_lead``
# read side — today or yesterday only. Older is stale enough that
# recency weighting zeroes it out).
_UPLOADS_FALLBACK_DAYS = 1


def _tmp_root() -> Path:
    """Standard ``$GENLAB_TMP`` resolve — matches the runners'
    persistence rule so the read side finds what the write side
    dropped."""
    tmp = os.environ.get("GENLAB_TMP")
    return Path(tmp) if tmp else Path.cwd() / ".tmp"


def _load_recent_artifact(
    subdir: str,
    niche_id: str,
    fallback_days: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """Look back up to ``fallback_days`` days for a YYYYMMDD-<niche>.json
    file under ``$GENLAB_TMP/<subdir>/``. Returns ``(payload, stamp)``
    for the newest file found, or ``(None, None)``.

    Fail-open on JSONDecodeError / OSError — logs at WARN and
    returns ``(None, None)``. Never raises.
    """
    root = _tmp_root() / subdir
    if not root.is_dir():
        return None, None

    now = datetime.now(UTC)
    for delta in range(0, fallback_days + 1):
        stamp = (now - timedelta(days=delta)).strftime("%Y%m%d")
        path = root / f"{stamp}-{niche_id}.json"
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text()), stamp
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "top_creator_priors: %s/%s malformed: %s",
                stamp,
                niche_id,
                exc,
            )
            # Try older ones — a corrupted today's file shouldn't
            # shadow a good week-old one.
            continue
    return None, None


def _flag_enabled(env_var: str) -> bool:
    """Exact-match ``true`` / ``TRUE`` / ``True`` — the same rule
    the runners themselves use. NO strip, because sibling readers
    also don't strip (consistent operator UX)."""
    return os.environ.get(env_var, "") in ("true", "TRUE", "True")


def _validate_niche(niche_id: str):
    """Shared validation returning a tuple ``(is_valid, error_resp)``.
    If ``is_valid`` is False, return ``error_resp`` (already jsonified
    with status 400). This keeps the two endpoints consistent."""
    if niche_id not in _VALID_NICHES:
        return False, (
            jsonify(
                {
                    "status": "error",
                    "message": (f"Invalid niche_id (allowed: {sorted(_VALID_NICHES)})"),
                }
            ),
            400,
        )
    return True, None


@bp.route("/latest", methods=["GET"])
def get_latest():
    """Return B.2's most recent correlations artifact for one niche.

    Response shape::

        {
          "status": "success",
          "data": {
            "generated_at": "2026-07-13T04:00:00+00:00",
            "niche_id": "gaming",
            "video_count": 24,
            "correlations": {
              "title_length_chars": -0.15,
              "title_ends_with_question": 0.31,
              ...
            },
            "flag_enabled": false,
            "artifact_stamp": "20260713"
          }
        }

    ``data`` is ``null`` when nothing in the 8-day window — frontend
    renders "No correlations yet" without special-casing errors.

    ``flag_enabled`` reflects whether
    ``GENLAB_TOP_CREATOR_PRIORS_ENABLED`` is currently ``true`` on
    the server — the frontend's active-vs-observation-only badge.
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    ok, err = _validate_niche(niche_id)
    if not ok:
        return err

    payload, stamp = _load_recent_artifact("top-creator-priors", niche_id, _PRIORS_FALLBACK_DAYS)
    if payload is None:
        return jsonify(
            {
                "status": "success",
                "data": None,
                "message": (
                    f"No top-creator prior artifact for {niche_id} "
                    f"in last {_PRIORS_FALLBACK_DAYS} days"
                ),
            }
        )

    # Server-inject flag state + artifact stamp so the frontend
    # doesn't need to parse the ISO timestamp for the "as of" line.
    payload["flag_enabled"] = _flag_enabled("GENLAB_TOP_CREATOR_PRIORS_ENABLED")
    payload["artifact_stamp"] = stamp
    return jsonify({"status": "success", "data": payload})


@bp.route("/uploads", methods=["GET"])
def get_uploads():
    """Return A.2's most recent uploads artifact for one niche.

    Response shape::

        {
          "status": "success",
          "data": {
            "generated_at": "2026-07-08T14:00:00+00:00",
            "niche_id": "gaming",
            "creators": [
              {
                "channel_id": "UC...",
                "label": "MKBHD",
                "uploads": [
                  {
                    "video_id": "...",
                    "title": "Vision Pro Review",
                    "published_at": "2026-07-08T12:00:00Z",
                    "hours_since_publish": 2.0
                  }
                ]
              }
            ],
            "flag_enabled": true,
            "artifact_stamp": "20260708"
          }
        }

    ``data`` is ``null`` when no artifact today or yesterday.
    Older artifacts intentionally don't surface — A.3's recency
    weight zeroes past-24h uploads anyway, so a 3-day-old artifact
    contributes nothing to the composite. Showing it would mislead.
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    ok, err = _validate_niche(niche_id)
    if not ok:
        return err

    payload, stamp = _load_recent_artifact("top-creator-uploads", niche_id, _UPLOADS_FALLBACK_DAYS)
    if payload is None:
        return jsonify(
            {
                "status": "success",
                "data": None,
                "message": (
                    f"No top-creator upload artifact for {niche_id} "
                    f"in last {_UPLOADS_FALLBACK_DAYS + 1} days"
                ),
            }
        )

    payload["flag_enabled"] = _flag_enabled("GENLAB_TOP_CREATORS_ENABLED")
    payload["artifact_stamp"] = stamp
    return jsonify({"status": "success", "data": payload})
