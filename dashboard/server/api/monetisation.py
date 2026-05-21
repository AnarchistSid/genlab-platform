"""Monetisation progress API endpoint.

Routes:
    GET  /api/v1/monetisation/progress  -- per-niche threshold progress from SP list
    POST /api/v1/monetisation/trigger   -- manually run the tracker (background thread)
    POST /api/v1/monetisation/send-deal -- send deal notification emails to channel subscribers
"""

import logging
import os
import time as _time

from flask import Blueprint, request

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("monetisation_api", __name__, url_prefix="/api/v1/monetisation")

_MONETISATION_LIST_ID = "1a464f5e-fc95-4597-84ab-3fe6e7f4274a"

_cache: dict = {"data": None, "ts": 0.0}
_CACHE_TTL = 300.0  # 5 min — SP data only updates daily


def _fetch_progress() -> list[dict]:
    """Fetch all MonetisationProgress rows from SharePoint."""
    entry = _cache.get("data")
    now = _time.time()
    if entry and (now - _cache["ts"]) < _CACHE_TTL:
        return entry

    from server.core.graph_sync import get_sync_client

    client = get_sync_client()
    records = client.monetisation_progress.all()
    _cache["data"] = records
    _cache["ts"] = now
    return records


@bp.route("/progress")
def monetisation_progress():
    """Return monetisation progress grouped by niche → platform → metrics."""
    try:
        records = _fetch_progress()
    except Exception as e:
        logger.error("Failed to fetch monetisation progress: %s", e)
        return api_error(error=str(e), code=500)

    # Group by niche_id → platform → list of metrics
    grouped: dict = {}
    for raw_rec in records:
        rec = raw_rec.get("fields", raw_rec)
        niche = rec.get("niche_id", "unknown")
        platform = rec.get("platform", "unknown")
        metric = {
            "metric_name": rec.get("metric_name", ""),
            "current_value": rec.get("current_value"),
            "target_value": rec.get("target_value"),
            "pct_complete": rec.get("pct_complete"),
            "delta_7d": rec.get("delta_7d"),
            "days_to_threshold_est": rec.get("days_to_threshold_est"),
            "is_threshold_met": rec.get("is_threshold_met", False),
            "data_source": rec.get("data_source", "api"),
            "as_of_date": rec.get("as_of_date"),
            "error_log": rec.get("error_log"),
        }

        if niche not in grouped:
            grouped[niche] = {}
        if platform not in grouped[niche]:
            grouped[niche][platform] = {"metrics": [], "is_monetised": False}

        grouped[niche][platform]["metrics"].append(metric)

        # A platform is monetised if ALL its metrics with targets are met
        if metric["is_threshold_met"]:
            grouped[niche][platform]["is_monetised"] = True

    # Compute per-niche overall pct (average across metrics that have targets)
    for _niche_id, platforms in grouped.items():
        pcts = []
        for plat_data in platforms.values():
            for m in plat_data["metrics"]:
                if m["pct_complete"] is not None:
                    pcts.append(m["pct_complete"])
                    if not m["is_threshold_met"]:
                        pass
            # Correct is_monetised: ALL metrics must be met
            met_count = sum(
                1
                for m in plat_data["metrics"]
                if m["is_threshold_met"] and m["target_value"] is not None
            )
            target_count = sum(1 for m in plat_data["metrics"] if m["target_value"] is not None)
            plat_data["is_monetised"] = met_count == target_count and target_count > 0

    return api_success(data=grouped)


@bp.route("/trigger", methods=["POST"])
def trigger_tracker():
    """Run the monetisation tracker in a background thread.

    The tracker collects current metrics from YouTube/Facebook/Instagram APIs
    and writes results to the GenLab_MonetisationProgress SharePoint list.
    Subsequent GET /progress calls will pick up the new data.
    """
    import threading

    def _run_tracker():
        try:
            from pathlib import Path

            from genlab_core.http.backlog_client import BacklogClient
            from genlab_core.monitoring.monetisation_tracker import MonetisationTracker

            genlab_root = Path(
                os.environ.get(
                    "GENLAB_PROJECT_ROOT",
                    str(Path(__file__).resolve().parent.parent.parent.parent),
                )
            )
            config_path = genlab_root / "genlab-core" / "config" / "monetisation_targets.yaml"
            client = BacklogClient()
            tracker = MonetisationTracker(client, config_path=config_path)
            results = tracker.run(dry_run=False)
            logger.info("[MONETISATION] Tracker completed: %d niches", len(results))
            # Invalidate cache so next GET picks up fresh data
            _cache["data"] = None
            _cache["ts"] = 0.0
        except Exception as exc:
            logger.error("[MONETISATION] Tracker failed: %s", exc, exc_info=True)

    try:
        t = threading.Thread(target=_run_tracker, daemon=True)
        t.start()
        return api_success(data={"status": "started", "message": "Tracker running in background"})
    except Exception as e:
        logger.error("Failed to start monetisation tracker: %s", e)
        return api_error(error=str(e), code=500)


_VALID_CHANNELS = {"blackboxbrief", "criticalrush", "clutchwire", "splicereel", "framedrift"}


@bp.route("/send-deal", methods=["POST"])
def send_deal():
    """Send deal notification emails to all active subscribers of a channel.

    Expects JSON body:
        {
            "channel": "criticalrush",
            "product_name": "PS5 Console",
            "product_url": "https://...",
            "discount_text": "10% off"  (optional)
        }

    This is an admin-only endpoint (protected by session auth via before_request).
    """
    data = request.get_json(silent=True)
    if not data:
        return api_error(error="Request body must be JSON", code=400)

    channel = (data.get("channel") or "").strip().lower()
    product_name = (data.get("product_name") or "").strip()
    product_url = (data.get("product_url") or "").strip()
    discount_text = (data.get("discount_text") or "").strip()

    if not channel or channel not in _VALID_CHANNELS:
        return api_error(
            error=f"Invalid channel. Must be one of: {', '.join(sorted(_VALID_CHANNELS))}",
            code=400,
        )
    if not product_name:
        return api_error(error="product_name is required", code=400)
    if not product_url:
        return api_error(error="product_url is required", code=400)

    try:
        from server.core.email_sender import is_configured, send_batch_deals

        if not is_configured():
            return api_error(
                error="Email not configured. Set RESEND_API_KEY in .env to enable.",
                code=503,
            )

        sent = send_batch_deals(
            channel_slug=channel,
            product_name=product_name,
            product_url=product_url,
            discount_text=discount_text,
        )
        logger.info("[MONETISATION] Deal emails sent: %d for channel %s", sent, channel)
        return api_success(data={"sent": sent, "channel": channel, "product": product_name})
    except Exception as e:
        logger.error("[MONETISATION] send-deal failed: %s", e, exc_info=True)
        return api_error(error=str(e), code=500)
