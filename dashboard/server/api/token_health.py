"""Token Health API endpoints.

Routes:
    GET  /api/token-health                    — token expiry status for all platforms
    POST /api/token-health/refresh/<platform> — trigger immediate token refresh
"""

import logging
import time as _time
from datetime import UTC, datetime

from flask import Blueprint

from server.core.responses import api_error, api_not_found, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("token_health_api", __name__, url_prefix="/api")

# 5-minute cache to avoid hammering platform APIs
_health_cache: dict = {"data": None, "ts": 0.0}
_HEALTH_TTL = 300.0


def _check_tiktok() -> dict:
    """Check TikTok token health without refreshing."""
    from genlab_core.settings import settings

    token = getattr(settings, "tiktok_access_token", None)
    if not token:
        return {
            "platform": "tiktok",
            "access_token_status": "missing",
            "refresh_token_status": "missing",
        }

    audit_raw = getattr(settings, "tiktok_audit_approved", "false")
    audit_approved = str(audit_raw).lower() == "true"

    issued_str = getattr(settings, "tiktok_token_issued_at", None)
    access_status = "unknown"
    access_hours = None
    last_refreshed = None

    if issued_str:
        try:
            issued_at = datetime.fromisoformat(str(issued_str))
            if issued_at.tzinfo is None:
                issued_at = issued_at.replace(tzinfo=UTC)
            age_hours = (datetime.now(UTC) - issued_at).total_seconds() / 3600
            remaining_hours = max(0, 24 - age_hours)
            access_hours = round(remaining_hours, 1)
            last_refreshed = issued_at.isoformat()

            if remaining_hours > 6:
                access_status = "ok"
            elif remaining_hours > 1:
                access_status = "refresh_soon"
            else:
                access_status = "critical"
        except (ValueError, TypeError):
            pass

    # Refresh token: 1 year lifetime — estimate from issued_at
    refresh_status = "ok"
    refresh_days = None
    if issued_str:
        try:
            issued_at = datetime.fromisoformat(str(issued_str))
            if issued_at.tzinfo is None:
                issued_at = issued_at.replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - issued_at).days
            refresh_days = max(0, 365 - age_days)
            if refresh_days < 30:
                refresh_status = "critical"
            elif refresh_days < 90:
                refresh_status = "refresh_soon"
        except (ValueError, TypeError):
            refresh_status = "unknown"

    return {
        "platform": "tiktok",
        "access_token_status": access_status,
        "access_token_expires_in_hours": access_hours,
        "refresh_token_status": refresh_status,
        "refresh_token_expires_in_days": refresh_days,
        "last_refreshed": last_refreshed,
        "audit_approved": audit_approved,
    }


def _check_threads() -> dict:
    """Check Threads token health without refreshing."""
    from genlab_core.settings import settings

    token = getattr(settings, "threads_access_token", None)
    if not token:
        return {"platform": "threads", "token_status": "missing", "expires_in_days": None}

    issued_str = getattr(settings, "threads_token_issued_at", None)
    status = "unknown"
    days_remaining = None
    last_refreshed = None

    if issued_str:
        try:
            issued_at = datetime.fromisoformat(str(issued_str))
            if issued_at.tzinfo is None:
                issued_at = issued_at.replace(tzinfo=UTC)
            age_days = (datetime.now(UTC) - issued_at).days
            days_remaining = max(0, 60 - age_days)
            last_refreshed = issued_at.isoformat()

            if days_remaining > 15:
                status = "ok"
            elif days_remaining > 2:
                status = "refresh_soon"
            else:
                status = "critical"
        except (ValueError, TypeError):
            pass

    return {
        "platform": "threads",
        "token_status": status,
        "expires_in_days": days_remaining,
        "last_refreshed": last_refreshed,
    }


def _check_instagram() -> dict:
    """Check Instagram (Meta) token health."""
    from genlab_core.monitoring.token_health import check_meta_token

    result = check_meta_token()
    days = result.get("days_remaining")
    status = "ok"
    if result["status"] in ("expired", "error"):
        status = "critical"
    elif days is not None and days < 14:
        status = "refresh_soon"

    return {
        "platform": "instagram",
        "token_status": status,
        "expires_in_days": round(days, 1) if days else None,
        "last_refreshed": None,
    }


def _check_youtube() -> dict:
    """Check YouTube OAuth health."""
    from genlab_core.monitoring.check_token_health import check_youtube

    result = check_youtube()
    status = "ok" if result["status"] == "healthy" else "critical"
    return {
        "platform": "youtube",
        "token_status": status,
        "expires_in_days": None,  # OAuth2 refresh tokens are permanent
        "last_refreshed": None,
    }


def _check_facebook() -> dict:
    """Check Facebook Page token health."""
    from genlab_core.monitoring.check_token_health import check_facebook

    result = check_facebook()
    status = "ok"
    if result["status"] in ("expired", "error"):
        status = "critical"
    elif result["status"] == "missing":
        status = "missing"
    return {
        "platform": "facebook",
        "token_status": status,
        "expires_in_days": None,
        "last_refreshed": None,
    }


@bp.route("/token-health", methods=["GET"])
def token_health():
    """Return token expiry status for all platforms. Cached for 5 minutes."""
    now = _time.time()
    if _health_cache["data"] and (now - _health_cache["ts"]) < _HEALTH_TTL:
        return api_success(data=_health_cache["data"])

    platforms = []
    for checker in [
        _check_tiktok,
        _check_threads,
        _check_instagram,
        _check_youtube,
        _check_facebook,
    ]:
        try:
            platforms.append(checker())
        except Exception as e:
            logger.warning("Token health check failed: %s", e)

    result = {
        "platforms": platforms,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    _health_cache["data"] = result
    _health_cache["ts"] = now
    return api_success(data=result)


@bp.route("/token-health/refresh/<platform>", methods=["POST"])
def refresh_token(platform: str):
    """Trigger immediate token refresh for a specific platform."""
    if platform == "threads":
        try:
            from genlab_core.publishing.threads_client import ThreadsClient

            client = ThreadsClient()
            client.refresh_token()
            # Invalidate cache
            _health_cache["data"] = None
            return api_success(data={"status": "refreshed", "platform": "threads"})
        except Exception as e:
            return api_error(error=str(e)[:200], code=500)

    if platform == "tiktok":
        try:
            from genlab_core.publishing.tiktok_client import TikTokClient

            client = TikTokClient()
            client._refresh_access_token()
            _health_cache["data"] = None
            return api_success(data={"status": "refreshed", "platform": "tiktok"})
        except Exception as e:
            return api_error(error=str(e)[:200], code=500)

    if platform in ("instagram", "facebook"):
        return api_success(
            data={
                "status": "redirect",
                "message": f"{platform} uses Meta OAuth — refresh via check_token_health.py or re-auth flow",
            }
        )

    if platform == "youtube":
        return api_success(
            data={
                "status": "redirect",
                "message": "YouTube uses Google OAuth2 with permanent refresh tokens",
            }
        )

    return api_not_found(message=f"Unknown platform: {platform}")
