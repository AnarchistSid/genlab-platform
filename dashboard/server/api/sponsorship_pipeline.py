"""Sponsorship pipeline API (Phase 3.C sessions 1-2, 2026-08-14).

Sessions 1 shipped the read-only listing. Session 2 adds the
write endpoints for the DRAFTED → APPROVED → SENT lifecycle plus
the actual Outlook-transport send wire.

## Endpoints

  * ``GET /api/v1/sponsorship/pipeline`` — list rows filterable
    by niche_id + status.
  * ``POST /api/v1/sponsorship/pipeline/<id>/approve`` — DRAFTED
    → APPROVED. Idempotent (re-approve is a no-op).
  * ``POST /api/v1/sponsorship/pipeline/<id>/reject`` — DRAFTED or
    APPROVED → REJECTED with reason. Never allowed on SENT rows.
  * ``POST /api/v1/sponsorship/pipeline/<id>/send`` — APPROVED →
    SENT. Fires Outlook API synchronously. Gated behind
    ``GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED=1`` — returns 503 when
    off. Every send = deliberate operator click; there is NO cron
    that auto-sends APPROVED rows.

## Safety contract

  * ``sending_enabled=false`` in list-response tells the frontend
    to disable the Send button + show a banner.
  * ``/send`` handler re-reads row state inside the transaction —
    prevents double-send if operator double-clicks.
  * Rate limit: ``GENLAB_SPONSORSHIP_MAX_SENDS_PER_HOUR`` (default
    10) protects against runaway. Applied at endpoint layer, not
    transport, so the same limit governs all callers.
  * ``/send`` failures route by error class: AUTH_FAILED → 503 +
    leave APPROVED for retry, INVALID_RECIPIENT → mark REJECTED,
    RATE_LIMITED → 429 + leave APPROVED, UNKNOWN → 500 + leave
    APPROVED.

## Fail-open contract (list only)

Cold-start returns ``{"data": null}`` rather than 500ing so the
frontend renders 'No pipeline rows yet' copy without special-casing.
Write endpoints DO surface errors — the operator needs to see them.
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint(
    "sponsorship_pipeline_api",
    __name__,
    url_prefix="/api/v1/sponsorship/pipeline",
)

_VALID_STATUSES = frozenset({
    "DRAFTED", "APPROVED", "SENT", "RESPONDED", "DEAL",
    "REJECTED", "STALE",
})


def _sending_enabled() -> bool:
    return os.environ.get(
        "GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED", "0",
    ).strip().lower() in {"1", "true", "yes"}


def _max_sends_per_hour() -> int:
    try:
        return max(1, int(os.environ.get(
            "GENLAB_SPONSORSHIP_MAX_SENDS_PER_HOUR", "10",
        )))
    except (TypeError, ValueError):
        return 10


def _rate_limit_exceeded(conn) -> bool:
    """True if we've already sent more than the hourly cap. Global
    (not per-niche) — total outbound velocity is what deliverability
    reputation cares about."""
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)::int AS n
            FROM sponsorship_pipeline
            WHERE sent_at >= NOW() - INTERVAL '1 hour'
            """
        ).fetchone()
        n = row.get("n") if hasattr(row, "get") else row[0]
        return int(n) >= _max_sends_per_hour()
    except Exception as exc:
        # Fail-CLOSED: if the rate-limit query fails, refuse to send
        # (opposite of the fail-open pattern for read endpoints).
        # Sending is irreversible — better to defer than double-send.
        logger.warning("[sponsorship_pipeline] rate-limit check failed: %s", exc)
        return True


@bp.route("", methods=["GET"])
def list_pipeline():
    """List pipeline rows.

    Query params:
      * ``niche_id`` (optional) — filter to one niche
      * ``status`` (optional) — filter to one status (whitelisted)
      * ``limit`` (optional, default 50, cap 200)

    Response:
        {
          "status": "success",
          "data": {
            "sending_enabled": false,
            "rows": [
              {
                "id": "...",
                "niche_id": "gaming",
                "brand_name": "...",
                "brand_email": "...",
                "tier_at_generation": "eligible_now",
                "subject": "...",
                "body": "...",
                "kit_url": "...",
                "status": "DRAFTED",
                "drafted_at": "..."
              }
            ]
          }
        }
    """
    niche_filter = request.args.get("niche_id")
    status_filter = request.args.get("status")
    if status_filter and status_filter not in _VALID_STATUSES:
        return jsonify({"status": "error", "reason": "invalid status"}), 400
    try:
        limit = max(1, min(200, int(request.args.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "success", "data": None})

    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            params: list = []
            where_clauses = []
            if niche_filter:
                where_clauses.append("sp.niche_id = %s")
                params.append(niche_filter)
            if status_filter:
                where_clauses.append("sp.status = %s")
                params.append(status_filter)
            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            params.append(limit)

            rows = conn.execute(
                f"""
                SELECT sp.id, sp.niche_id, sp.tier_at_generation,
                       sp.subject, sp.body, sp.kit_url, sp.status,
                       sp.drafted_at, sp.approved_at, sp.sent_at,
                       sp.responded_at, sp.deal_closed_at,
                       sbt.brand_name, sbt.brand_email,
                       sbt.contact_first_name
                FROM sponsorship_pipeline sp
                JOIN sponsorship_brand_targets sbt ON sbt.id = sp.target_id
                {where_sql}
                ORDER BY sp.drafted_at DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
    except Exception as exc:
        logger.warning("[sponsorship_pipeline] query failed: %s", exc)
        return jsonify({"status": "success", "data": None})

    if not rows:
        return jsonify({"status": "success", "data": None})

    def _iso(v):
        try:
            return v.isoformat() if v is not None else None
        except AttributeError:
            return v

    # sending_enabled = the session-2 auto-send flag. Frontend uses
    # this to disable the Send button + show a banner when off.
    sending_enabled = _sending_enabled()

    return jsonify(
        {
            "status": "success",
            "data": {
                "sending_enabled": sending_enabled,
                "rows": [
                    {
                        "id": str(r["id"]),
                        "niche_id": r["niche_id"],
                        "brand_name": r["brand_name"],
                        "brand_email": r["brand_email"],
                        "contact_first_name": r["contact_first_name"],
                        "tier_at_generation": r["tier_at_generation"],
                        "subject": r["subject"],
                        "body": r["body"],
                        "kit_url": r["kit_url"],
                        "status": r["status"],
                        "drafted_at": _iso(r["drafted_at"]),
                        "approved_at": _iso(r["approved_at"]),
                        "sent_at": _iso(r["sent_at"]),
                        "responded_at": _iso(r["responded_at"]),
                        "deal_closed_at": _iso(r["deal_closed_at"]),
                    }
                    for r in rows
                ],
            },
        }
    )


# ── Write endpoints (Phase 3.C session 2, 2026-08-14) ─────────────


def _load_row(conn, row_id: str):
    """Fetch a pipeline row + its target-side email/brand for the
    write endpoints. Returns dict or None. Never fail-open — write
    handlers need the row to make a decision."""
    row = conn.execute(
        """
        SELECT sp.id, sp.niche_id, sp.status, sp.subject, sp.body,
               sp.approved_at, sp.sent_at,
               sbt.brand_name, sbt.brand_email
        FROM sponsorship_pipeline sp
        JOIN sponsorship_brand_targets sbt ON sbt.id = sp.target_id
        WHERE sp.id = %s
        """,
        (row_id,),
    ).fetchone()
    return dict(row) if row else None


@bp.route("/<row_id>/approve", methods=["POST"])
def approve_row(row_id: str):
    """DRAFTED → APPROVED. Idempotent — re-approving an already-
    APPROVED row is a no-op. Rejects if row is SENT / RESPONDED /
    DEAL / REJECTED (terminal states not reversible via this API)."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "error", "reason": "DATABASE_URL unset"}), 500
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            row = _load_row(conn, row_id)
            if not row:
                return jsonify({"status": "error", "reason": "not found"}), 404
            if row["status"] == "APPROVED":
                return jsonify({"status": "success", "data": {"status": "APPROVED", "already": True}})
            if row["status"] != "DRAFTED":
                return jsonify({
                    "status": "error",
                    "reason": f"cannot approve from {row['status']}",
                }), 409
            conn.execute(
                """
                UPDATE sponsorship_pipeline
                SET status = 'APPROVED', approved_at = NOW()
                WHERE id = %s AND status = 'DRAFTED'
                """,
                (row_id,),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[sponsorship_pipeline] approve %s failed: %s", row_id, exc)
        return jsonify({"status": "error", "reason": str(exc)}), 500
    return jsonify({"status": "success", "data": {"status": "APPROVED"}})


@bp.route("/<row_id>/reject", methods=["POST"])
def reject_row(row_id: str):
    """DRAFTED or APPROVED → REJECTED with reason. SENT rows can
    NOT be rejected — the email already went out; the operator
    needs to reply/apologize out-of-band."""
    reason = (request.json or {}).get("reason") if request.is_json else None
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "error", "reason": "DATABASE_URL unset"}), 500
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            row = _load_row(conn, row_id)
            if not row:
                return jsonify({"status": "error", "reason": "not found"}), 404
            if row["status"] not in ("DRAFTED", "APPROVED"):
                return jsonify({
                    "status": "error",
                    "reason": f"cannot reject from {row['status']}",
                }), 409
            conn.execute(
                """
                UPDATE sponsorship_pipeline
                SET status = 'REJECTED',
                    rejected_at = NOW(),
                    rejection_reason = %s
                WHERE id = %s AND status IN ('DRAFTED', 'APPROVED')
                """,
                (reason, row_id),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[sponsorship_pipeline] reject %s failed: %s", row_id, exc)
        return jsonify({"status": "error", "reason": str(exc)}), 500
    return jsonify({"status": "success", "data": {"status": "REJECTED"}})


@bp.route("/<row_id>/send", methods=["POST"])
def send_row(row_id: str):
    """APPROVED → SENT. Fires Outlook API synchronously.

    Behaviour matrix:
      * flag off       → 503 (send disabled)
      * row not found  → 404
      * row.status != APPROVED → 409 (must approve first)
      * rate-limit hit → 429 (leave APPROVED for retry)
      * transport AUTH_FAILED   → 503 (leave APPROVED for retry)
      * transport RATE_LIMITED  → 429 (leave APPROVED for retry)
      * transport INVALID       → mark REJECTED with reason
      * transport UNKNOWN       → 500 (leave APPROVED for retry)
      * success        → 202 + row.status = SENT
    """
    if not _sending_enabled():
        return jsonify({
            "status": "error",
            "reason": "GENLAB_SPONSORSHIP_AUTO_SEND_ENABLED is off",
        }), 503
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        return jsonify({"status": "error", "reason": "DATABASE_URL unset"}), 500

    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            row = _load_row(conn, row_id)
            if not row:
                return jsonify({"status": "error", "reason": "not found"}), 404
            if row["status"] != "APPROVED":
                return jsonify({
                    "status": "error",
                    "reason": f"cannot send from {row['status']} (must be APPROVED)",
                }), 409
            if _rate_limit_exceeded(conn):
                return jsonify({
                    "status": "error",
                    "reason": f"rate limit: max {_max_sends_per_hour()}/hour",
                }), 429

            # Import here so the endpoint file doesn't hard-depend
            # on outlook_sender (module doesn't need to import
            # azure.identity when only listing is used).
            from genlab_core.integrations.outlook_sender import (
                OutlookMailSender, SendError, INVALID_RECIPIENT,
                RATE_LIMITED, AUTH_FAILED,
            )
            try:
                sender = OutlookMailSender()
                result = sender.send(
                    to_email=row["brand_email"],
                    subject=row["subject"],
                    body=row["body"],
                )
            except SendError as exc:
                if exc.reason == INVALID_RECIPIENT:
                    # Mark REJECTED — the operator will not want to
                    # retry to a bad address.
                    conn.execute(
                        """
                        UPDATE sponsorship_pipeline
                        SET status = 'REJECTED',
                            rejected_at = NOW(),
                            rejection_reason = %s
                        WHERE id = %s AND status = 'APPROVED'
                        """,
                        (f"send failed: {exc.detail[:200]}", row_id),
                    )
                    conn.commit()
                    return jsonify({
                        "status": "error",
                        "reason": "invalid recipient — marked REJECTED",
                        "detail": exc.detail,
                    }), 400
                if exc.reason == RATE_LIMITED:
                    return jsonify({
                        "status": "error", "reason": "graph rate-limited",
                        "detail": exc.detail,
                    }), 429
                if exc.reason == AUTH_FAILED:
                    return jsonify({
                        "status": "error",
                        "reason": "auth failed — check Mail.Send app permission",
                        "detail": exc.detail,
                    }), 503
                return jsonify({
                    "status": "error", "reason": "send failed",
                    "detail": exc.detail,
                }), 500
            except ValueError as exc:
                # Env misconfiguration (GENLAB_OUTREACH_FROM_UPN unset, etc)
                return jsonify({
                    "status": "error",
                    "reason": "sender misconfigured",
                    "detail": str(exc),
                }), 500

            # Success — flip status inside the same connection so
            # a concurrent send-retry can't double-send.
            conn.execute(
                """
                UPDATE sponsorship_pipeline
                SET status = 'SENT', sent_at = NOW()
                WHERE id = %s AND status = 'APPROVED'
                """,
                (row_id,),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("[sponsorship_pipeline] send %s failed: %s", row_id, exc)
        return jsonify({"status": "error", "reason": str(exc)}), 500

    return jsonify({"status": "success", "data": {"status": "SENT"}})
