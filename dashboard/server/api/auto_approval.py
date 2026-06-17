"""Auto-approval gate stats + kill-switch endpoints.

AUTO #1b (2026-06-13): Surfaces the per-niche confusion matrix for the
AutoApprovalGate. Read by the dashboard to show:

- "would auto-approve · 78%" badge per blueprint (via blueprints API)
- agreement-rate card on Mission Control (this module)

D3.10 (2026-06-15, AUTO #2 runbook): Adds a file-backed global kill
switch endpoint so the operator can disable auto-approval from the
dashboard without SSH-editing env vars. The worker checks BOTH the
``GENLAB_AUTO_APPROVE_DISABLED`` env var AND this file flag — either
being set disables auto-approval globally for every niche.

Operators consult this before flipping AUTO #2's opt-in flag. Once a
niche shows ≥30 samples + ≥90% agreement, enforcement becomes
defensible.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("auto_approval_api", __name__, url_prefix="/api/v1/auto-approval")

# Niche IDs the dashboard supports. Whitelisted to prevent SQL injection
# via the niche_id param (defense-in-depth — calibration_logger.stats also
# parameterizes). If a future niche is added, append here.
_VALID_NICHES = frozenset({"ai_creators", "gaming", "sports", "movies", "anime"})


@bp.route("/calibration-stats", methods=["GET"])
def calibration_stats():
    """Return the per-niche agreement rate for a rolling window.

    Query params:
        niche_id (required): one of ai_creators, gaming, sports, movies, anime
        window_days (optional, default 7): rolling window size

    Response:
        {
          "niche_id": "gaming",
          "window_days": 7,
          "sample_count": 18,
          "agreement_count": 16,
          "agreement_rate": 0.889,
          "confusion_matrix": {
            "true_positives": 12,
            "true_negatives": 4,
            "false_positives": 1,
            "false_negatives": 1
          },
          "ready_for_enforcement": false  // <30 samples
        }
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id query param required", code=400)
    if niche_id not in _VALID_NICHES:
        return api_error(
            error=f"niche_id must be one of {sorted(_VALID_NICHES)}",
            code=400,
        )

    try:
        window_days = int(request.args.get("window_days", "7"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    try:
        from genlab_core.scheduling.calibration_logger import stats

        s = stats(niche_id=niche_id, window_days=window_days)
    except Exception as exc:
        logger.exception("calibration-stats failed for %s", niche_id)
        return api_error(error=f"Stats query failed: {exc}", code=500)

    return api_success(
        data={
            "niche_id": s.niche_id,
            "window_days": window_days,
            "sample_count": s.sample_count,
            "agreement_count": s.agreement_count,
            "agreement_rate": round(s.agreement_rate, 3),
            "confusion_matrix": {
                "true_positives": s.true_positives,
                "true_negatives": s.true_negatives,
                "false_positives": s.false_positives,
                "false_negatives": s.false_negatives,
            },
            "ready_for_enforcement": s.ready_for_enforcement,
        }
    )


# ── W4.4: Track-record endpoint (per-day agreement trend) ─────────────
#
# Closes W4.4 from the autonomy plan. ``calibration-stats`` returns a
# single-window snapshot — useful for the Day-8 readiness check but
# blind to TRENDS. If a niche's agreement rate is climbing day over
# day, the operator should see that signal earlier. If it's regressing,
# even earlier. This endpoint returns per-day bins so the dashboard
# can sparkline the trend.
#
# Mirrors calibration-stats' validation surface (whitelisted niche_id,
# 1..90 day window). Adds bin_days for granularity control — typically
# 1 (daily) but operators may want 7 (weekly) for a 90-day view.


@bp.route("/track-record", methods=["GET"])
def track_record():
    """Per-day (or per-bin) agreement-rate trend for a niche.

    Query params:
        niche_id (required): one of the 5 valid niches
        window_days (optional, default 30): rolling window size
        bin_days (optional, default 1): bin size — 1 = daily, 7 = weekly

    Response:
        {
          "niche_id": "gaming",
          "window_days": 30,
          "bin_days": 1,
          "bins": [
            {"date": "2026-05-20", "sample_count": 5, "agreement": 4,
             "rate": 0.80},
            ...
          ],
          "overall": {"sample_count": 105, "agreement": 95, "rate": 0.905}
        }

    Returns an empty bins list when no calibration data exists in
    the window — frontend renders a "no data yet" message.
    """
    niche_id = (request.args.get("niche_id") or "").strip()
    if not niche_id:
        return api_error(error="niche_id query param required", code=400)
    if niche_id not in _VALID_NICHES:
        return api_error(
            error=f"niche_id must be one of {sorted(_VALID_NICHES)}",
            code=400,
        )

    try:
        window_days = int(request.args.get("window_days", "30"))
    except (TypeError, ValueError):
        return api_error(error="window_days must be an integer", code=400)
    if window_days < 1 or window_days > 90:
        return api_error(error="window_days must be 1..90", code=400)

    try:
        bin_days = int(request.args.get("bin_days", "1"))
    except (TypeError, ValueError):
        return api_error(error="bin_days must be an integer", code=400)
    if bin_days < 1 or bin_days > window_days:
        return api_error(error=f"bin_days must be 1..{window_days}", code=400)

    try:
        import os

        from psycopg.rows import dict_row

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            return api_error(error="DATABASE_URL not configured", code=503)

        # Bin SQL: floor((NOW() - decided_at) / bin_days) gives the
        # bin index; date_trunc + interval would also work but the
        # arithmetic stays explicit so an operator can re-run it by
        # hand to sanity-check.
        # SR-A/C/D Tier-4 (2026-06-17): pg_connect sets app.niche_id
        # automatically, so the manual ``SET app.niche_id`` is removed.
        from genlab_core.storage.tenant_context import pg_connect

        with pg_connect(dsn, connect_timeout=5, row_factory=dict_row, niche_id=niche_id) as conn:
            # Agreement = TP + TN per the existing calibration_logger
            # convention. Aligns with calibration-stats' agreement_count.
            # Note: gate-vs-gate filtering happens at INSERT time in
            # ``calibration_logger.log()`` (S1 fix, 2026-06-15) — rows
            # whose ``action_taken_source`` matches an auto-approver
            # tag are simply never written. So no WHERE-clause filter
            # is needed here, and the table schema doesn't carry an
            # ``action_taken_source`` column at all (caught at deploy
            # 2026-06-17 evening when the prod schema diverged from a
            # docstring assumption).
            rows = conn.execute(
                """
                SELECT
                    (decided_at::date) AS day,
                    COUNT(*) AS sample_count,
                    COUNT(*) FILTER (
                        WHERE (gate_approved = true AND operator_action = 'approved')
                           OR (gate_approved = false AND operator_action IN
                                ('rejected', 'revised', 'skipped'))
                    ) AS agreement_count
                FROM auto_approval_calibration
                WHERE niche_id = %s
                  AND decided_at > NOW() - (%s || ' days')::interval
                GROUP BY 1
                ORDER BY 1
                """,
                (niche_id, window_days),
            ).fetchall()

        # Optionally re-bin into bin_days groups. Default bin_days=1
        # means "one bin per day"; bin_days=7 groups by week.
        if bin_days == 1:
            bins = [
                {
                    "date": r["day"].isoformat(),
                    "sample_count": int(r["sample_count"]),
                    "agreement": int(r["agreement_count"]),
                    "rate": round(r["agreement_count"] / r["sample_count"], 3)
                    if r["sample_count"]
                    else 0.0,
                }
                for r in rows
            ]
        else:
            # Group consecutive days into bin_days-sized buckets.
            # We bin from the most-recent day backwards so the latest
            # bin always reflects the latest data even when the window
            # isn't a clean multiple of bin_days.
            from datetime import timedelta

            day_to_row = {r["day"]: r for r in rows}
            bins = []
            if rows:
                today = max(day_to_row)
                cursor = today
                while True:
                    bin_end = cursor
                    bin_start = bin_end - timedelta(days=bin_days - 1)
                    samples = 0
                    agreements = 0
                    d = bin_start
                    while d <= bin_end:
                        if d in day_to_row:
                            samples += int(day_to_row[d]["sample_count"])
                            agreements += int(day_to_row[d]["agreement_count"])
                        d += timedelta(days=1)
                    if samples:
                        bins.append(
                            {
                                "date": bin_end.isoformat(),
                                "sample_count": samples,
                                "agreement": agreements,
                                "rate": round(agreements / samples, 3),
                            }
                        )
                    cursor = bin_start - timedelta(days=1)
                    if cursor < min(day_to_row):
                        break
                bins.reverse()  # chronological order

        total_samples = sum(b["sample_count"] for b in bins)
        total_agreement = sum(b["agreement"] for b in bins)
        overall = {
            "sample_count": total_samples,
            "agreement": total_agreement,
            "rate": round(total_agreement / total_samples, 3) if total_samples else 0.0,
        }

        return api_success(
            data={
                "niche_id": niche_id,
                "window_days": window_days,
                "bin_days": bin_days,
                "bins": bins,
                "overall": overall,
            }
        )

    except Exception as exc:
        logger.exception("track-record failed for %s", niche_id)
        return api_error(error=f"Track-record query failed: {exc}", code=500)


# ── D3.10: Global kill-switch endpoint ────────────────────────────────
#
# Tiny file-backed flag the auto_approver worker checks before doing
# anything. Two sources of truth combined:
#   - GENLAB_AUTO_APPROVE_DISABLED env var (operator can set via shell
#     during an incident — survives until the worker restarts)
#   - file at $GENLAB_AUTO_APPROVE_KILL_FILE or
#     /opt/genlab/.runtime/auto_approve_kill_switch (default)
#
# Either being set disables auto-approval globally. The dashboard
# button writes/removes the file; env var is shell-only.


def _kill_switch_file_path() -> Path:
    """Resolve the file flag path — mirrors auto_approver._kill_switch_file_path."""
    override = os.environ.get("GENLAB_AUTO_APPROVE_KILL_FILE", "").strip()
    return Path(override) if override else Path("/opt/genlab/.runtime/auto_approve_kill_switch")


def _read_kill_switch_state() -> dict:
    """Return the current global kill-switch state.

    Mirrors auto_approver._kill_switch_active so dashboard + worker
    can never disagree about what "active" means.
    """
    env_set = os.environ.get("GENLAB_AUTO_APPROVE_DISABLED", "").strip() not in (
        "",
        "0",
        "false",
        "False",
    )
    file_path = _kill_switch_file_path()
    file_set = file_path.is_file()
    if env_set and file_set:
        source = "both"
    elif env_set:
        source = "env"
    elif file_set:
        source = "file"
    else:
        source = "none"
    return {
        "active": env_set or file_set,
        "source": source,
        "file_path": str(file_path),
        # Surface env-var separately so the UI can warn "env var also
        # set — only SSH can clear that".
        "env_var_set": env_set,
    }


@bp.route("/kill-switch", methods=["GET"])
def get_kill_switch():
    """Read current global kill-switch state."""
    return api_success(data=_read_kill_switch_state())


@bp.route("/kill-switch", methods=["POST"])
def set_kill_switch():
    """Flip the file-backed kill switch on or off.

    Body: ``{"active": true}`` to disable auto-approval globally,
    ``{"active": false}`` to re-enable. The env var ``GENLAB_AUTO_APPROVE_DISABLED``
    is NOT touched — that's set via shell on the worker host and
    survives restarts independently. The UI must surface that if it
    is set, this endpoint cannot fully clear it.
    """
    body = request.get_json(silent=True) or {}
    if "active" not in body:
        return api_error(error="body must include 'active' boolean", code=400)
    if not isinstance(body["active"], bool):
        return api_error(error="'active' must be a boolean", code=400)

    file_path = _kill_switch_file_path()
    try:
        if body["active"]:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(
                "Auto-approval globally disabled via dashboard kill-switch.\n"
                "Delete this file (or POST kill-switch active=false) to re-enable.\n"
            )
            logger.warning("[kill-switch] DISABLED via dashboard — file=%s", file_path)
        else:
            if file_path.exists():
                file_path.unlink()
                logger.warning("[kill-switch] RE-ENABLED via dashboard — file=%s", file_path)
            # else: idempotent — already off
    except OSError as exc:
        logger.exception("[kill-switch] file op failed")
        return api_error(error=f"file op failed: {exc}", code=500)

    return api_success(data=_read_kill_switch_state())
