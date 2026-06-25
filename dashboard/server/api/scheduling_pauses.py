"""Niche-pause dashboard endpoints (2026-06-25 — PR #577 consumer).

The operator-facing surface for PR #575's per-niche pause primitive.
Without these endpoints, operators have to write DB rows manually to
pause autonomy on a niche. With them, the future Mission Control
toggle (or a curl from incident response) can pause/resume in one
HTTP call.

## Endpoints

  GET /api/v1/scheduling/pauses
      List currently-active pauses (across all allowed niches).
      SR-F: restricted operators see only their niche's row.

  POST /api/v1/scheduling/pauses
      Body: {niche_id, reason, paused_until?}
      Creates / updates the pause row. paused_by auto-captured from
      the current session's REVIEW_AUTH_USER.
      SR-F: 403 when niche_id outside allowlist.

  DELETE /api/v1/scheduling/pauses/<niche_id>
      Removes the pause row (idempotent — 200 even when nothing
      was paused).
      SR-F: 403 when niche_id outside allowlist.

## paused_by capture

Comes from ``_username_for_session()`` (the same helper SR-F uses).
The audit log shows WHO paused each niche — critical for forensic
review after a copyright strike or shadowban incident.

## What this PR DOES NOT do

  * No React UI — that ships in a follow-up frontend PR
  * No publisher wire — auto_approver wire shipped in the prior
    PR; publisher wire is a separate concern (and the niche-pause
    primitive already supports it, just needs to call is_paused
    in publish_all_platforms)
  * No auto-resume sweeper — table hygiene only; not correctness
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("scheduling_pauses_api", __name__, url_prefix="/api/v1/scheduling/pauses")


@bp.route("", methods=["GET"])
def list_pauses():
    """Return currently-active pauses (those with paused_until NULL
    or in the future).

    SR-F: restricted operator's response is filtered to their
    allowlist. Admin sees all.

    Response shape:
      {data: [
        {niche_id, paused_until, reason, paused_by, created_at}, ...
      ]}
    """
    try:
        from genlab_core.scheduling.niche_pause import list_active_pauses
    except ImportError:
        # Graceful degrade against pre-PR-577 core
        return api_success(data=[])

    pauses = list_active_pauses()

    # SR-F: post-filter by allowlist (same shape as other list endpoints)
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None:
        pauses = [p for p in pauses if p.niche_id in _allowed]

    data = [
        {
            "niche_id": p.niche_id,
            "paused_until": p.paused_until.isoformat() if p.paused_until else None,
            "reason": p.reason,
            "paused_by": p.paused_by,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in pauses
    ]
    return api_success(data=data)


@bp.route("", methods=["POST"])
def create_pause():
    """Pause a niche. Idempotent UPSERT — re-posting the same niche
    updates the existing pause with the new reason / paused_until.

    Body:
      niche_id (str, required)
      reason (str, required) — for the audit log
      paused_until (ISO-8601 str, optional) — None = indefinite

    Returns 200 on success, 4xx on validation failure, 403 when SR-F
    restricts the caller's access to the niche.

    paused_by is auto-captured from the session user (same source
    as SR-F's allowlist resolution).
    """
    body = request.get_json(silent=True) or {}
    niche_id = (body.get("niche_id") or "").strip()
    reason = (body.get("reason") or "").strip()
    paused_until_raw = body.get("paused_until")

    if not niche_id:
        return api_error(error="niche_id required")
    if not reason:
        return api_error(error="reason required (audit log)")

    # SR-F: must be in operator's allowlist
    from server.auth.niche_allowlist import _username_for_session, get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None and niche_id not in _allowed:
        return api_error(
            error=(
                f"Niche '{niche_id}' is not in your allowlist "
                f"(allowed: {sorted(_allowed)}). See SR-F."
            ),
            code=403,
        )

    paused_until = None
    if paused_until_raw:
        try:
            # Accept ISO-8601 with or without Z timezone marker
            s = paused_until_raw.replace("Z", "+00:00")
            paused_until = datetime.fromisoformat(s)
            if paused_until.tzinfo is None:
                paused_until = paused_until.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            return api_error(error="paused_until must be ISO-8601 datetime")

    paused_by = _username_for_session() or "unknown"

    try:
        from genlab_core.scheduling.niche_pause import pause
    except ImportError:
        return api_error(error="niche_pause module unavailable", code=503)

    if not pause(
        niche_id=niche_id,
        reason=reason,
        paused_until=paused_until,
        paused_by=paused_by,
    ):
        return api_error(error="pause write failed (see logs)", code=500)

    logger.info(
        "[scheduling_pauses] niche=%s paused by=%s reason=%r until=%s",
        niche_id,
        paused_by,
        reason,
        paused_until.isoformat() if paused_until else "indefinite",
    )
    return api_success(
        data={
            "niche_id": niche_id,
            "paused_until": paused_until.isoformat() if paused_until else None,
            "reason": reason,
            "paused_by": paused_by,
        }
    )


@bp.route("/<niche_id>", methods=["DELETE"])
def remove_pause(niche_id: str):
    """Unpause a niche. Idempotent — returns 200 even when no pause
    existed (operator clicking 'resume' on an already-resumed niche
    shouldn't error).

    SR-F: 403 when niche_id outside allowlist.
    """
    niche_id = (niche_id or "").strip()
    if not niche_id:
        return api_error(error="niche_id required")

    # SR-F guard
    from server.auth.niche_allowlist import get_allowed_niches

    _allowed = get_allowed_niches()
    if _allowed is not None and niche_id not in _allowed:
        return api_error(
            error=(
                f"Niche '{niche_id}' is not in your allowlist "
                f"(allowed: {sorted(_allowed)}). See SR-F."
            ),
            code=403,
        )

    try:
        from genlab_core.scheduling.niche_pause import unpause
    except ImportError:
        return api_error(error="niche_pause module unavailable", code=503)

    if not unpause(niche_id):
        return api_error(error="unpause write failed (see logs)", code=500)

    from server.auth.niche_allowlist import _username_for_session

    logger.info(
        "[scheduling_pauses] niche=%s unpaused by=%s",
        niche_id,
        _username_for_session() or "unknown",
    )
    return api_success(data={"niche_id": niche_id, "paused": False})
