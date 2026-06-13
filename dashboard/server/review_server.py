#!/usr/bin/env python3
"""Web-based review dashboard for the express lane pipeline.

Dark terminal aesthetic. Shows DRAFTED + VISUAL_READY blueprints
as review cards with approve/reject/skip actions. Integrates with
express lane pipeline via WebSocket for real-time progress.

Usage:
    python execution/review_server.py                    # Start on :5151
    python execution/review_server.py --port 8080        # Custom port
    python execution/review_server.py --dry-run          # No backlog writes
    python execution/review_server.py --local             # Use local blueprint_pack.json
"""

import argparse
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# GENLAB_PROJECT_ROOT defaults to the GenLab workspace root (parent of dashboard/)
_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = Path(
    os.environ.get(
        "GENLAB_PROJECT_ROOT",
        str(_DASHBOARD_ROOT.parent),
    )
)
# GenLab workspace root — media files for all niches live under their own dirs here
GENLAB_ROOT = _DASHBOARD_ROOT.parent
_DASHBOARD_DIST = _DASHBOARD_ROOT / "frontend" / "dist"

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env", override=True)

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    request,
    send_file,
    send_from_directory,
    session,
)
from flask_socketio import SocketIO

logger = logging.getLogger(__name__)
try:
    from genlab_core.observability.logging import configure_logging

    _is_json = os.environ.get("GENLAB_LOG_JSON", "").lower() == "true"
    configure_logging(json_output=_is_json, level=logging.INFO)
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

# ── Login rate limiting ──────────────────────────────────
import time as _time
from collections import defaultdict

_login_attempts: dict[str, list] = defaultdict(list)
_LOGIN_RATE_LIMIT = 5
_LOGIN_RATE_WINDOW = 60  # seconds

# Strict run_id validation: alphanumeric, underscores, hyphens only, max 64 chars
_SAFE_RUN_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

app = Flask(__name__)

# ── Security: require explicit secret key (no hardcoded fallback) ──
_secret_key = os.getenv("FLASK_SECRET_KEY", "").strip()
if not _secret_key:
    import secrets

    _secret_key = secrets.token_hex(32)
    logger.warning(
        "FLASK_SECRET_KEY not set — using random key (sessions won't persist across restarts). "
        "Set FLASK_SECRET_KEY in .env for production use."
    )
app.config["SECRET_KEY"] = _secret_key
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_COOKIE_SECURE", "true").lower() == "true"
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 7  # 7 days
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB upload limit

# ── Security: restrict CORS to localhost only ──
# Port is fixed at init time; --port override handled by re-adding origins in main().
_DEFAULT_PORT = 5151

# Detect async mode: use eventlet when available (production), threading for dev
_async_mode = "eventlet" if "eventlet" in sys.modules else "threading"
logger.info("SocketIO async_mode: %s", _async_mode)

# Cloudflare tunnels can break WebSocket Upgrade headers. When a tunnel URL
# is configured, restrict to long-polling so the handshake always succeeds.
_tunnel_url = os.getenv("CLOUDFLARE_TUNNEL_URL", "")
if _tunnel_url:
    logger.info("Cloudflare tunnel detected — SocketIO restricted to polling transport")

socketio = SocketIO(
    app,
    cors_allowed_origins=[
        f"http://localhost:{_DEFAULT_PORT}",
        f"http://127.0.0.1:{_DEFAULT_PORT}",
        "https://review.aspirehub.ai",
    ],
    async_mode=_async_mode,
    allow_upgrades=not bool(_tunnel_url),
)

# ── Global error handler ──────────────────────────────
from werkzeug.exceptions import HTTPException

from server.core.responses import api_error as _api_error
from server.core.responses import api_success as _api_success


@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return _api_error(error=e.description, message=e.name, code=e.code)
    app.logger.exception("Unhandled error")
    return _api_error(error="Internal server error", code=500)


# ── Clean up PostgreSQL connection pool on shutdown ──
import atexit


def _cleanup_pg_pool():
    """Close PostgreSQL connection pools to prevent __del__ errors."""
    try:
        from server.core.graph_sync import get_sync_client

        client = get_sync_client()
        pg = getattr(client, "_pg", None)
        if pg and hasattr(pg, "close"):
            pg.close()
            logger.info("PostgreSQL connection pool closed cleanly")
    except Exception:
        pass


atexit.register(_cleanup_pg_pool)


# ── WebSocket room support for niche-specific events ──
from flask_socketio import join_room, leave_room


# ── WebSocket authentication ──
@socketio.on("connect")
def handle_connect():
    """Reject unauthenticated WebSocket connections when auth is enabled."""
    if _AUTH_ENABLED and not session.get("authenticated"):
        return False


# NOTE: These WebSocket room events are currently unused by the frontend.
# They were added for future real-time niche filtering but no client code
# emits subscribe_niche/unsubscribe_niche yet. Kept for forward compatibility.
@socketio.on("subscribe_niche")
def handle_subscribe_niche(data):
    """Join a niche-specific room for filtered events."""
    niche_id = data.get("niche_id")
    if niche_id:
        join_room(f"niche:{niche_id}")
        logger.debug("Client joined room niche:%s", niche_id)


@socketio.on("unsubscribe_niche")
def handle_unsubscribe_niche(data):
    """Leave a niche-specific room."""
    niche_id = data.get("niche_id")
    if niche_id:
        leave_room(f"niche:{niche_id}")
        logger.debug("Client left room niche:%s", niche_id)


# ── Auto-approve timers (server-side countdown per blueprint) ──
_auto_approve_timers: dict[str, threading.Timer] = {}
_timer_lock = threading.Lock()


def _cancel_auto_approve_timer(record_id: str) -> None:
    """Cancel a pending auto-approve timer for a blueprint, if one exists."""
    with _timer_lock:
        timer = _auto_approve_timers.pop(record_id, None)
    if timer is not None:
        timer.cancel()
        logger.debug("Cancelled auto-approve timer for %s", record_id)


def _start_auto_approve_timer(record_id: str, seconds: float) -> None:
    """Start a server-side auto-approve timer for a blueprint."""
    _cancel_auto_approve_timer(record_id)

    def _auto_approve():
        with _timer_lock:
            _auto_approve_timers.pop(record_id, None)
        logger.info("Auto-approving %s after %ds timeout", record_id, int(seconds))
        try:
            _execute_review_action(
                record_id,
                "approved",
                notes=f"Auto-approved after {int(seconds)}s timeout",
            )
            socketio.emit(
                "blueprint_updated",
                {
                    "id": record_id,
                    "record_id": record_id,
                    "action": "approved",
                    "auto_approved": True,
                },
            )
        except Exception as exc:
            logger.error("Auto-approve failed for %s: %s", record_id, exc)

    timer = threading.Timer(seconds, _auto_approve)
    timer.daemon = True
    with _timer_lock:
        _auto_approve_timers[record_id] = timer
    timer.start()
    logger.debug("Started %ds auto-approve timer for %s", int(seconds), record_id)


# ── Security: HTTP Basic Auth ──────────────────────────────
# Set REVIEW_AUTH_USER and REVIEW_AUTH_PASS in .env to enable.
# When not set, the server runs unauthenticated (localhost-only use).
_AUTH_USER = os.getenv("REVIEW_AUTH_USER", "")
_AUTH_PASS = os.getenv("REVIEW_AUTH_PASS", "")
_AUTH_ENABLED = bool(_AUTH_USER and _AUTH_PASS)

if _AUTH_ENABLED:
    logger.info("HTTP Basic Auth enabled (user: %s)", _AUTH_USER)
else:
    logger.critical(
        "Review server running WITHOUT authentication. "
        "Set REVIEW_AUTH_USER and REVIEW_AUTH_PASS in .env to protect the review dashboard. "
        "Non-localhost bind (0.0.0.0) will be refused until auth is configured."
    )


def _enforce_localhost_when_no_auth():
    """Block non-localhost requests when authentication is disabled.

    When REVIEW_AUTH_USER/REVIEW_AUTH_PASS are not set, only requests from
    localhost (127.0.0.1, ::1) are allowed. This prevents accidental exposure
    via a 0.0.0.0 bind or reverse proxy without auth.
    """
    if _AUTH_ENABLED:
        return None
    remote = request.remote_addr or ""
    if remote not in ("127.0.0.1", "::1"):
        logger.warning(
            "Blocked non-localhost request from %s — auth is disabled",
            remote,
        )
        return Response("Authentication required. Configure REVIEW_AUTH_USER/PASS.", status=403)
    return None


app.before_request(_enforce_localhost_when_no_auth)


def _check_auth():
    """Verify auth via session cookie (preferred) or HTTP Basic Auth (fallback).

    After a successful login (form or Basic Auth), a session cookie is set so
    that subsequent fetch() calls from the React SPA work without re-sending
    credentials.  The browser's fetch() API cannot inherit Basic Auth
    credentials from the URL, but it *does* send same-origin cookies
    automatically.
    """
    from flask import session

    if not _AUTH_ENABLED:
        return None

    # 1. Check session cookie first (set after initial login)
    if session.get("authenticated"):
        return None

    # 2. Fall back to HTTP Basic Auth header (API clients / curl)
    auth = request.authorization
    if (
        auth
        and hmac.compare_digest(auth.username or "", _AUTH_USER)
        and hmac.compare_digest(auth.password or "", _AUTH_PASS)
    ):
        session["authenticated"] = True
        session.permanent = True
        return None

    # 3. Not authenticated — redirect browsers to /login, return 401 for API
    if request.path.startswith("/api/"):
        return Response(
            '{"error":"Authentication required"}\n',
            401,
            {"Content-Type": "application/json"},
        )
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def _login_page():
    """Form-based login page — sets session cookie on success."""
    from flask import session

    error = ""
    if request.method == "POST":
        # ── Rate limiting: 5 attempts per minute per IP ──
        ip = request.remote_addr or "unknown"
        now = _time.time()
        # Purge stale IPs to keep dict bounded
        if len(_login_attempts) > 200:
            cutoff = now - _LOGIN_RATE_WINDOW * 10
            stale_ips = [k for k, v in _login_attempts.items() if all(t < cutoff for t in v)]
            for k in stale_ips:
                del _login_attempts[k]

        _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _LOGIN_RATE_WINDOW]
        if len(_login_attempts[ip]) >= _LOGIN_RATE_LIMIT:
            return "Too many login attempts. Try again later.", 429
        _login_attempts[ip].append(now)

        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if hmac.compare_digest(username.encode(), _AUTH_USER.encode()) and hmac.compare_digest(
            password.encode(), _AUTH_PASS.encode()
        ):
            session["authenticated"] = True
            session.permanent = True
            return redirect("/")
        error = "Invalid credentials"
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Login — Review Dashboard</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;
display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#1e293b;border-radius:12px;padding:2rem;width:100%;max-width:380px;
box-shadow:0 25px 50px -12px rgba(0,0,0,.5)}}
h1{{font-size:1.25rem;margin-bottom:.25rem;color:#f8fafc}}
.sub{{color:#94a3b8;font-size:.875rem;margin-bottom:1.5rem}}
label{{display:block;font-size:.8rem;color:#94a3b8;margin-bottom:.25rem}}
input{{width:100%;padding:.6rem .75rem;border:1px solid #334155;border-radius:8px;
background:#0f172a;color:#f8fafc;font-size:.9rem;margin-bottom:1rem;outline:none}}
input:focus{{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.3)}}
button{{width:100%;padding:.65rem;border:none;border-radius:8px;background:#3b82f6;
color:#fff;font-size:.9rem;font-weight:600;cursor:pointer}}
button:hover{{background:#2563eb}}
.err{{color:#f87171;font-size:.8rem;margin-bottom:1rem;text-align:center}}
</style></head><body>
<div class="card">
<h1>Review Dashboard</h1>
<p class="sub">Sign in to continue</p>
{"<p class='err'>" + error + "</p>" if error else ""}
<form method="post">
<label for="u">Username</label>
<input id="u" name="username" type="text" autocomplete="username" autofocus required>
<label for="p">Password</label>
<input id="p" name="password" type="password" autocomplete="current-password" required>
<button type="submit">Sign in</button>
</form></div></body></html>"""


@app.route("/logout")
def _logout_page():
    """Clear session and redirect to login."""
    from flask import session

    session.clear()
    return redirect("/login")


@app.before_request
def _enforce_auth():
    """Apply session + form auth to all routes (except /login itself)."""
    if request.path in ("/login", "/logout", "/api/health", "/manifest.json"):
        return None
    # Webhook endpoints use signature verification instead of session auth
    if request.path.startswith("/api/v1/webhooks/"):
        return None
    # CDN media files must be public for Instagram API video upload
    if request.path.startswith("/api/media/.media/cdn/"):
        return None
    # Legal pages are public (required by Meta App Review)
    if request.path.startswith("/legal/"):
        return None
    # Link-in-bio pages and click tracker are public — used by Instagram visitors
    if request.path.startswith("/links/"):
        return None
    denied = _check_auth()
    if denied is not None:
        return denied


@app.after_request
def _add_security_headers(response):
    """Add security headers to all responses."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Link-in-bio pages need inline scripts (subscribe form, deep link redirect,
    # GA4, Facebook Pixel) — use a relaxed CSP for /links/ routes only.
    if request.path.startswith("/links/"):
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com https://connect.facebook.net; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob: https://m.media-amazon.com; "
            "connect-src 'self'; "
            "font-src 'self'; "
        )
    else:
        # Dashboard CSP: strict, no inline scripts
        csp = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "img-src 'self' data: blob: https://i.ytimg.com https://cdn.akamai.steamstatic.com https://*.steamstatic.com; "
            "media-src 'self' blob:; "
            f"connect-src 'self' wss://review.aspirehub.ai ws://localhost:{_DEFAULT_PORT}; "
            "font-src 'self' https://fonts.gstatic.com; "
        )
    response.headers["Content-Security-Policy"] = csp
    return response


# ── Security: Record ID validation ───────────────
# Integer (SharePoint) or UUID (Postgres) record IDs
RECORD_RE = re.compile(r"^[\w-]+$")


# ── Security: CSRF token for state-changing requests ───────
import hashlib
import hmac

_CSRF_SECRET = app.config["SECRET_KEY"]
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _generate_csrf_token() -> str:
    """Generate a CSRF token tied to the session.

    Uses a per-session nonce so stolen tokens are scoped to one session.
    All gunicorn workers share the session backend, so tokens stay valid
    across workers.
    """
    import secrets as _secrets

    if "csrf_nonce" not in session:
        session["csrf_nonce"] = _secrets.token_hex(16)
    return hmac.new(
        _CSRF_SECRET.encode(),
        session["csrf_nonce"].encode(),
        hashlib.sha256,
    ).hexdigest()[:32]


@app.before_request
def _enforce_csrf():
    """Check CSRF token on state-changing API requests."""
    if request.method in _SAFE_METHODS:
        return None
    if not request.path.startswith("/api/"):
        return None
    # Webhook endpoints use signature verification instead of CSRF
    if request.path.startswith("/api/v1/webhooks/"):
        return None
    # WebSocket upgrade requests don't need CSRF
    if request.environ.get("HTTP_UPGRADE", "").lower() == "websocket":
        return None
    token = request.headers.get("X-CSRF-Token", "") or (request.get_json(silent=True) or {}).get(
        "_csrf_token", ""
    )
    if not hmac.compare_digest(token, _generate_csrf_token()):
        return _api_error(error="CSRF token missing or invalid", code=403)


@app.context_processor
def _inject_csrf():
    """Make csrf_token available in templates."""
    return {"csrf_token": _generate_csrf_token()}


@app.route("/api/csrf-token")
def get_csrf_token():
    """Return current CSRF token for JS clients."""
    return jsonify({"csrf_token": _generate_csrf_token()})


# ── Register API v1 modules ──────────────────────────────
from server.api.alerts import bp as alerts_bp
from server.api.analytics import bp as analytics_bp
from server.api.audience import bp as audience_bp
from server.api.auto_approval import bp as auto_approval_bp
from server.api.blueprints import bp as blueprints_bp
from server.api.blueprints import health_bp as focus_health_bp
from server.api.config_routes import bp as config_bp
from server.api.config_routes import settings_bp
from server.api.engagement import bp as engagement_bp
from server.api.events import bp as events_bp
from server.api.health import bp as health_bp
from server.api.learning import bp as learning_bp
from server.api.legal import legal_bp
from server.api.links import bp as links_bp
from server.api.metrics import bp as metrics_bp
from server.api.monetisation import bp as monetisation_bp
from server.api.niches import bp as niches_bp
from server.api.overview import bp as overview_bp
from server.api.pipeline import bp as pipeline_bp
from server.api.platform_posts import bp as platform_posts_bp
from server.api.publishing_queue import bp as queue_bp
from server.api.revenue import bp as revenue_bp
from server.api.runway import bp as runway_bp
from server.api.schedule import bp as schedule_bp
from server.api.scheduler import scheduler_bp
from server.api.sources import bp as sources_bp
from server.api.stories import bp as stories_bp
from server.api.token_health import bp as token_health_bp
from server.api.trends import bp as trends_bp
from server.api.webhook_receiver import webhook_bp
from server.api.youtube_quota import bp as youtube_quota_bp

app.register_blueprint(blueprints_bp)
app.register_blueprint(stories_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(pipeline_bp)
app.register_blueprint(config_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(niches_bp)
app.register_blueprint(overview_bp)
app.register_blueprint(queue_bp)
app.register_blueprint(sources_bp)
app.register_blueprint(auto_approval_bp)
app.register_blueprint(focus_health_bp)
app.register_blueprint(token_health_bp)
app.register_blueprint(platform_posts_bp)
app.register_blueprint(learning_bp)
app.register_blueprint(webhook_bp)
app.register_blueprint(legal_bp)
app.register_blueprint(runway_bp)
app.register_blueprint(engagement_bp)
app.register_blueprint(trends_bp)
app.register_blueprint(youtube_quota_bp)
app.register_blueprint(monetisation_bp)
app.register_blueprint(scheduler_bp)
app.register_blueprint(health_bp)
app.register_blueprint(links_bp)
app.register_blueprint(revenue_bp)
app.register_blueprint(alerts_bp)
app.register_blueprint(metrics_bp)
app.register_blueprint(audience_bp)
app.register_blueprint(events_bp)


# ── State ──────────────────────────────────────────────────
express_state = {
    "running": False,
    "current_step": None,
    "steps_completed": [],
    "last_run": None,
}
_express_lock = threading.Lock()

review_settings = {
    "auto_approve": False,
    "auto_approve_seconds": 60,
}

VENV_PYTHON = sys.executable
SERVER_STARTED_AT = time.time()
BLUEPRINT_CACHE_TTL_SECONDS = max(
    2.0,
    float(os.getenv("REVIEW_BLUEPRINT_CACHE_TTL_SECONDS", "8")),
)
ALLOW_LOCAL_FALLBACK = str(os.getenv("REVIEW_ALLOW_LOCAL_FALLBACK", "0")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
_blueprint_cache_lock = threading.Lock()
_blueprint_cache: dict[str, Any] = {
    "timestamp": 0.0,
    "records": [],
    "source": "empty",
    "stale": False,
}


def _blueprint_sort_key(bp: dict[str, Any]) -> tuple[int, float, str]:
    """Prioritize VISUAL_READY first, then urgency_score descending."""
    status_rank = {
        "VISUAL_READY": 0,
    }
    status = str(bp.get("status", "VISUAL_READY")).upper()
    urgency_score = bp.get("urgency_score", 0) or 0
    try:
        score = float(urgency_score)
    except (TypeError, ValueError):
        score = 0.0
    return (
        status_rank.get(status, 99),
        -score,
        str(bp.get("id", "")),
    )


def _sort_blueprints(blueprints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return stable, high-signal ordering for review cards."""
    return sorted(blueprints, key=_blueprint_sort_key)


def _load_backlog_blueprints() -> list[dict[str, Any]]:
    """Load VISUAL_READY blueprints from backlog for visual review.

    Text review stage removed — only VISUAL_READY needs human review.
    Excludes blueprints that already have action_taken set (approved/rejected/revised).
    """
    from server.core.graph_sync import get_sync_client

    client = get_sync_client()
    visual_ready = client.blueprints.all(
        formula="AND({status}='VISUAL_READY', OR({action_taken}='', {action_taken}=BLANK()))"
    )
    normalized = [normalize_blueprint(bp, source="backlog") for bp in visual_ready]
    return _sort_blueprints(normalized)


def _get_backlog_blueprints_cached(
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], str, bool]:
    """Return backlog blueprints with TTL cache and stale fallback."""
    now = time.time()
    with _blueprint_cache_lock:
        has_cache = bool(_blueprint_cache["records"])
        age = now - float(_blueprint_cache["timestamp"] or 0.0)
        if not force_refresh and has_cache and age < BLUEPRINT_CACHE_TTL_SECONDS:
            return list(_blueprint_cache["records"]), "backlog_cache", False

    try:
        records = _load_backlog_blueprints()
        with _blueprint_cache_lock:
            _blueprint_cache["timestamp"] = now
            _blueprint_cache["records"] = list(records)
            _blueprint_cache["source"] = "backlog"
            _blueprint_cache["stale"] = False
        return records, "backlog", False
    except Exception as exc:
        with _blueprint_cache_lock:
            cached = list(_blueprint_cache["records"])
        if cached:
            logger.warning("Backlog load failed; serving stale cache: %s", exc)
            return cached, "backlog_cache", True
        raise


# ══════════════════════════════════════════════════════════════
# API Routes
# ══════════════════════════════════════════════════════════════


@app.route("/")
def index():
    """Serve the React SPA."""
    if _DASHBOARD_DIST.exists() and (_DASHBOARD_DIST / "index.html").exists():
        resp = send_from_directory(str(_DASHBOARD_DIST), "index.html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp
    # Fallback if dashboard not built
    return Response(
        '<html><body style="background:#0a0a0a;color:#fafafa;font-family:sans-serif;padding:40px">'
        "<h1>Dashboard not built</h1>"
        "<p>Run <code>cd dashboard &amp;&amp; npm run build</code></p>"
        "</body></html>",
        mimetype="text/html",
    )


@app.route("/api/blueprints")
def get_blueprints():
    """Get DRAFTED + VISUAL_READY blueprints.

    In production (default), backlog errors fail closed with HTTP 503.
    Set REVIEW_ALLOW_LOCAL_FALLBACK=1 to permit local fallback behavior.
    """
    local_mode = app.config.get("LOCAL_MODE", False)

    if local_mode:
        response = jsonify(_sort_blueprints(load_local_blueprints()))
        response.headers["X-Review-Source"] = "local"
        response.headers["X-Review-Stale"] = "0"
        response.headers["Cache-Control"] = "no-store"
        return response

    try:
        blueprints, source, stale = _get_backlog_blueprints_cached()
        response = jsonify(blueprints)
        response.headers["X-Review-Source"] = source
        response.headers["X-Review-Stale"] = "1" if stale else "0"
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as e:
        if not ALLOW_LOCAL_FALLBACK:
            logger.error("Backlog unavailable; fail-closed mode active: %s", e)
            response = jsonify(
                {
                    "error": "Backlog unavailable",
                    "detail": "Internal error — check server logs",
                    "fail_closed": True,
                }
            )
            response.headers["X-Review-Source"] = "backlog_unavailable"
            response.headers["X-Review-Stale"] = "1"
            response.headers["Cache-Control"] = "no-store"
            return response, 503

        logger.warning("Backlog unavailable, falling back to local: %s", e)
        response = jsonify(_sort_blueprints(load_local_blueprints()))
        response.headers["X-Review-Source"] = "local_fallback"
        response.headers["X-Review-Stale"] = "1"
        response.headers["Cache-Control"] = "no-store"
        return response


@app.route("/api/health")
def health():
    """Lightweight health / diagnostics endpoint.

    Unauthenticated: returns minimal liveness {"status": "ok"}.
    Authenticated: returns full diagnostics (uptime, counts, state).
    """
    # Check if request is authenticated (without enforcing — health always responds)
    from flask import session

    authenticated = session.get("authenticated", False)

    if not authenticated:
        return jsonify({"status": "ok", "version": "2.0.0", "environment": "production"})

    # ── Full diagnostics for authenticated users ──
    local_mode = app.config.get("LOCAL_MODE", False)
    uptime_seconds = round(time.time() - SERVER_STARTED_AT, 2)

    if local_mode:
        records = _sort_blueprints(load_local_blueprints())
        counts = {
            "total": len(records),
            "drafted": sum(1 for bp in records if bp.get("status") == "DRAFTED"),
            "visual_ready": sum(1 for bp in records if bp.get("status") == "VISUAL_READY"),
        }
        with _express_lock:
            _express_running = bool(express_state["running"])
        return jsonify(
            {
                "status": "ok",
                "mode": "local",
                "uptime_seconds": uptime_seconds,
                "data_source": "local",
                "stale": False,
                "blueprints": counts,
                "cache_ttl_seconds": BLUEPRINT_CACHE_TTL_SECONDS,
                "express_running": _express_running,
            }
        )

    force_refresh = request.args.get("fresh", "0") == "1"
    try:
        records, source, stale = _get_backlog_blueprints_cached(force_refresh=force_refresh)
        counts = {
            "total": len(records),
            "drafted": sum(1 for bp in records if bp.get("status") == "DRAFTED"),
            "visual_ready": sum(1 for bp in records if bp.get("status") == "VISUAL_READY"),
        }
        with _express_lock:
            _express_running = bool(express_state["running"])
        return jsonify(
            {
                "status": "ok",
                "version": "2.0.0",
                "environment": "production",
                "mode": "backlog",
                "uptime_seconds": uptime_seconds,
                "data_source": source,
                "stale": stale,
                "blueprints": counts,
                "cache_ttl_seconds": BLUEPRINT_CACHE_TTL_SECONDS,
                "express_running": _express_running,
            }
        )
    except Exception as exc:
        logger.error("Health check backlog error: %s", exc, exc_info=True)
        with _express_lock:
            _express_running = bool(express_state["running"])
        if not ALLOW_LOCAL_FALLBACK:
            return jsonify(
                {
                    "status": "error",
                    "mode": "backlog",
                    "uptime_seconds": uptime_seconds,
                    "data_source": "unavailable",
                    "stale": True,
                    "error": "Backlog unavailable",
                    "fail_closed": True,
                    "cache_ttl_seconds": BLUEPRINT_CACHE_TTL_SECONDS,
                    "express_running": _express_running,
                }
            ), 503
        return jsonify(
            {
                "status": "degraded",
                "mode": "backlog",
                "uptime_seconds": uptime_seconds,
                "data_source": "unavailable",
                "stale": True,
                "error": "Backlog unavailable",
                "cache_ttl_seconds": BLUEPRINT_CACHE_TTL_SECONDS,
                "express_running": _express_running,
            }
        ), 503


def _execute_review_action(
    record_id: str,
    action: str,
    *,
    notes: str = "",
    feedback_issue: str = "",
    feedback_notes: str = "",
    feedback_fix: str = "",
) -> dict[str, Any]:
    """Shared review logic: persist a review decision to backlog.

    Both the legacy /api/review/ endpoint and /api/v1/blueprints/<id>/review
    delegate here for the actual backlog write.

    Returns the update_fields dict on success.
    Raises on failure (caller handles error response).
    """
    from server.core.graph_sync import get_sync_client

    update_fields: dict[str, Any] = {
        "action_taken": action,
        "reviewed_at": datetime.now(UTC).isoformat(),
    }

    if notes:
        update_fields["review_notes"] = notes

    client = get_sync_client()

    if action == "approved":
        # Always find the next available slot for this niche — even if the
        # pipeline already set a tentative scheduled_for.  Multiple approvals
        # on the same day would otherwise stack on the same slot (1 per niche
        # per day cap).
        from server.core.publishing_queue import _next_available_slot

        niche_id = ""
        try:
            bp_data = client.blueprints.get(record_id)
            niche_id = (bp_data.get("fields", {}).get("niche_id") or "").strip()
        except Exception:
            pass
        next_slot = _next_available_slot(niche_id=niche_id)
        if next_slot:
            update_fields["scheduled_for"] = next_slot
            logger.info(
                "[REVIEW] Auto-scheduled %s → %s (niche=%s)", record_id, next_slot, niche_id
            )
        else:
            logger.warning(
                "[REVIEW] No available slot found for niche=%s — "
                "approving %s without scheduled_for (will sit in queue until "
                "a slot opens)",
                niche_id,
                record_id[:16],
            )
    elif action == "rejected":
        update_fields["status"] = "ARCHIVED"
        update_fields["feedback_issue"] = feedback_issue or "rejected_in_review"
        update_fields["feedback_notes"] = feedback_notes or notes
    elif action == "revised":
        update_fields["feedback_issue"] = feedback_issue or "needs_revision"
        update_fields["feedback_notes"] = feedback_notes or notes

    if feedback_fix:
        update_fields["feedback_fix"] = feedback_fix

    # skipped: no extra fields

    client.blueprints.update(record_id, update_fields, typecast=True)

    # AUTO #1b (2026-06-13): Calibration logging. Capture what the
    # AutoApprovalGate WOULD have said vs what the operator actually
    # clicked. Best-effort — must NEVER block the review action. After
    # ~1 week of data the per-niche confusion matrix tells us whether
    # AUTO #2 (enforcement) is safe to flip on. See task #48.
    try:
        from genlab_core.scheduling import calibration_logger
        from genlab_core.scheduling.auto_approval_gate import evaluate as _gate_evaluate

        # Re-fetch blueprint to get the shape evaluate() expects. We
        # already loaded `bp_data` for the niche_id lookup in the
        # "approved" branch, but it's scoped there. Simpler to reload.
        _bp = client.blueprints.get(record_id)
        _fields = _bp.get("fields", {}) if isinstance(_bp, dict) else {}
        _flat = {"id": _bp.get("id"), **_fields}
        _niche = (_flat.get("niche_id") or "").strip()
        if not isinstance(_flat.get("extra"), dict):
            _flat["extra"] = {
                "visual_paths": _flat.get("visual_paths"),
                "composite_score": _flat.get("composite_score"),
                "virality_score": _flat.get("virality_score"),
                "validation_status": _flat.get("validation_status"),
            }
        try:
            _decision = _gate_evaluate(_flat)
        except Exception as _ev_exc:
            logger.debug("[calibration] gate evaluation failed: %s", _ev_exc)
            _decision = None
        calibration_logger.log(
            blueprint_id=record_id,
            niche_id=_niche,
            decision=_decision,
            operator_action=action,
        )
    except Exception as _cal_exc:  # noqa: BLE001 — never block review
        logger.debug("[calibration] skipped (non-fatal): %s", _cal_exc)

    return update_fields


@app.route("/api/review/<record_id>", methods=["POST"])
def review_blueprint(record_id):
    """Handle approve/reject/skip action on a blueprint."""
    if not RECORD_RE.match(record_id):
        return _api_error(error="Invalid record ID format", code=400)

    data = request.json or {}
    action = data.get("action", "")
    notes = data.get("notes", "")
    feedback_issue = data.get("feedback_issue", "")
    dry_run = app.config.get("DRY_RUN", False)

    if action not in ("approved", "rejected", "skipped", "revised"):
        return _api_error(error=f"Invalid action: {action}", code=400)

    logger.info(
        "Review action: %s on %s (issue: %s, notes: %s)",
        action,
        record_id,
        feedback_issue,
        notes[:50],
    )

    # Cancel server-side auto-approve timer if one exists
    _cancel_auto_approve_timer(record_id)

    if dry_run:
        logger.info("[DRY RUN] Would update %s → %s", record_id, action)
        socketio.emit(
            "blueprint_updated",
            {
                "id": record_id,
                "record_id": record_id,
                "action": action,
                "dry_run": True,
            },
        )
        return _api_success(data={"dry_run": True, "action": action})

    try:
        _execute_review_action(
            record_id,
            action,
            notes=notes,
            feedback_issue=feedback_issue,
            feedback_notes=notes,
        )

        socketio.emit(
            "blueprint_updated",
            {
                "id": record_id,
                "record_id": record_id,
                "action": action,
            },
        )

        return _api_success(data={"action": action})
    except Exception as e:
        logger.error("Failed to update blueprint %s: %s", record_id, e)
        return _api_error(error="Failed to update blueprint — check server logs", code=500)


@app.route("/api/batch-review", methods=["POST"])
def batch_review():
    """Batch approve/reject multiple blueprints."""
    data = request.json or {}
    record_ids = data.get("record_ids", [])
    action = data.get("action", "")
    notes = data.get("notes", "")
    dry_run = app.config.get("DRY_RUN", False)

    if action not in ("approved", "rejected"):
        return _api_error(error=f"Invalid batch action: {action}", code=400)
    if not record_ids:
        return _api_error(error="No record_ids provided", code=400)

    # Validate all record IDs before processing any
    invalid_ids = [rid for rid in record_ids if not RECORD_RE.match(rid)]
    if invalid_ids:
        return _api_error(error=f"Invalid record ID format: {invalid_ids[0]}", code=400)

    results = []
    client = None
    if not dry_run:
        from server.core.graph_sync import get_sync_client

        client = get_sync_client()

    for record_id in record_ids:
        try:
            if dry_run:
                logger.info("[DRY RUN] Batch %s: %s", action, record_id)
                results.append({"id": record_id, "status": "ok", "dry_run": True})
                socketio.emit(
                    "blueprint_updated",
                    {
                        "id": record_id,
                        "record_id": record_id,
                        "action": action,
                        "dry_run": True,
                    },
                )
            else:
                update_fields = {
                    "action_taken": action,
                    "review_notes": notes or f"Batch {action}",
                    "reviewed_at": datetime.now(UTC).isoformat(),
                }
                if action == "approved":
                    # Auto-schedule: find the next available publish slot for this niche
                    from server.core.publishing_queue import _next_available_slot

                    niche_id = ""
                    try:
                        bp_data = client.blueprints.get(record_id)
                        niche_id = (bp_data.get("fields", {}).get("niche_id") or "").strip()
                    except Exception:
                        pass
                    next_slot = _next_available_slot(niche_id=niche_id)
                    if next_slot:
                        update_fields["scheduled_for"] = next_slot
                        logger.info(
                            "[BATCH] Auto-scheduled %s → %s (niche=%s)",
                            record_id,
                            next_slot,
                            niche_id,
                        )
                elif action == "rejected":
                    update_fields["feedback_issue"] = "rejected_in_review"
                    update_fields["feedback_notes"] = notes or f"Batch {action}"

                client.blueprints.update(record_id, update_fields, typecast=True)
                results.append({"id": record_id, "status": "ok"})
                socketio.emit(
                    "blueprint_updated",
                    {
                        "id": record_id,
                        "record_id": record_id,
                        "action": action,
                    },
                )
        except Exception as e:
            logger.error("Batch review failed for %s: %s", record_id, e)
            results.append({"id": record_id, "status": "error", "error": type(e).__name__})

    succeeded = sum(1 for r in results if r["status"] == "ok")
    failed = len(results) - succeeded
    logger.info("Batch %s: %d/%d succeeded", action, succeeded, len(results))
    return _api_success(data={"results": results, "succeeded": succeeded, "failed": failed})


@app.route("/api/settings")
def get_settings():
    """Get current review settings."""
    return _api_success(data=review_settings)


@app.route("/api/settings", methods=["POST"])
def update_settings():
    """Update review settings (auto-approve toggle + timer)."""
    data = request.json or {}
    if "auto_approve" in data:
        review_settings["auto_approve"] = bool(data["auto_approve"])
    if "auto_approve_seconds" in data:
        val = int(data["auto_approve_seconds"])
        review_settings["auto_approve_seconds"] = max(10, min(300, val))  # Clamp 10-300s
    logger.info("Settings updated: %s", review_settings)
    socketio.emit("settings_updated", review_settings)
    return _api_success(data=review_settings)


@app.route("/api/express/status")
def express_status():
    """Return current express lane status and last run timing."""
    report = load_latest_report()
    with _express_lock:
        state_snapshot = {
            "running": express_state["running"],
            "current_step": express_state["current_step"],
            "steps_completed": list(express_state["steps_completed"]),
        }
    state_snapshot["last_run"] = report
    return _api_success(data=state_snapshot)


@app.route("/api/express/trigger", methods=["POST"])
def trigger_express():
    """Trigger a new express lane run in a background thread.

    Concurrency model: an on-disk fcntl.flock survives dashboard worker
    restarts and is the source of truth. The in-memory `express_state`
    dict is informational only — it can desync with the lock during
    crashes but won't cause double-runs.
    """
    import fcntl

    data = request.json or {}
    run_id = data.get("run_id") or request.args.get("run-id", "test_express")
    if not _SAFE_RUN_ID.match(run_id):
        return _api_error(error="Invalid run ID", code=400)

    lock_path = PROJECT_ROOT / ".tmp" / "express_pipeline.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_fd.close()
        return _api_error(error="Express lane already running", code=409)

    with _express_lock:
        express_state["running"] = True
        express_state["current_step"] = None
        express_state["steps_completed"] = []

    thread = threading.Thread(
        target=run_express_pipeline,
        args=(run_id, lock_fd),
        daemon=True,
    )
    thread.start()

    return _api_success(data={"run_id": run_id}, message="started")


@app.route("/api/local/blueprints")
def get_local_blueprints_explicit():
    """Always return local blueprints from the latest run directory."""
    return jsonify(load_local_blueprints())


def _resolve_video_url(blueprint: dict) -> str | None:
    """Resolve a blueprint's rendered video to a browser-accessible URL.

    Priority:
      1. CDN URL stored in blueprint fields (already public)
      2. Cloudflare tunnel URL (CLOUDFLARE_TUNNEL_URL env var)
      3. /api/video/<id> streaming endpoint (local fallback)
    """
    fields = blueprint.get("fields", blueprint)

    # 1. Check for stored CDN URL
    cdn_url = fields.get("cdn_url") or fields.get("video_url")
    if cdn_url and isinstance(cdn_url, str) and cdn_url.startswith("https://"):
        return cdn_url

    # 2. Use tunnel URL if configured
    os.getenv("CLOUDFLARE_TUNNEL_URL", "").rstrip("/")
    rendered_path = fields.get("rendered_path") or fields.get("video_path")
    if not rendered_path:
        # Try extracting from visual_paths
        vp = fields.get("visual_paths")
        if isinstance(vp, str):
            try:
                parsed = json.loads(vp)
                if isinstance(parsed, list) and parsed:
                    rendered_path = parsed[0]
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(vp, list) and vp:
            rendered_path = vp[0]

    if rendered_path and isinstance(rendered_path, str):
        # Convert absolute filesystem path to a relative /api/media/ URL.
        # The /api/media/ endpoint resolves files under GENLAB_ROOT.
        rel_path = None
        for prefix in (str(GENLAB_ROOT), str(PROJECT_ROOT), "/opt/genlab"):
            if rendered_path.startswith(prefix):
                rel_path = rendered_path[len(prefix) :].lstrip("/")
                break
        if rel_path:
            return f"/api/media/{rel_path}"

    # 3. Fall back to streaming endpoint
    blueprint_id = fields.get("candidate_id") or blueprint.get("id")
    if blueprint_id:
        return f"/api/video/{blueprint_id}"

    return None


def _placeholder_image():
    """Return a dark placeholder SVG with a video icon. Used when a blueprint
    has no video and no thumbnail so the queue/schedule shows a styled
    'no preview' state instead of empty space or broken-image icons."""
    from flask import Response

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120" viewBox="0 0 120 120">'
        '<rect width="120" height="120" fill="#1a1a2e"/>'
        '<path d="M45 40v40l30-20z" fill="#333" stroke="#444" stroke-width="1.5"/>'
        '<text x="60" y="100" text-anchor="middle" fill="#555" font-size="10" font-family="sans-serif">No preview</text>'
        "</svg>"
    )
    return Response(
        svg.encode("utf-8"),
        mimetype="image/svg+xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.route("/api/video/<blueprint_id>")
def stream_video(blueprint_id):
    """Stream a blueprint's rendered video by looking up its path from SharePoint."""
    # Accept integer IDs (SharePoint) and hex candidate IDs
    if not re.match(r"^[\w\-]+$", str(blueprint_id)):
        return _api_error(error="Invalid blueprint ID", code=400)

    try:
        from server.core.graph_sync import get_sync_client

        client = get_sync_client()
        record = client.blueprints.get(blueprint_id)
    except Exception as e:
        logger.warning("Failed to look up blueprint %s: %s", blueprint_id, e)
        return _api_error(error="Blueprint not found", code=404)

    if not record:
        return _api_error(error="Blueprint not found", code=404)

    fields = record.get("fields", {})
    video_path = fields.get("rendered_path") or fields.get("video_path")

    if not video_path:
        # Try visual_paths
        vp_raw = fields.get("visual_paths", "")
        if isinstance(vp_raw, str):
            try:
                paths = json.loads(vp_raw)
                if isinstance(paths, list):
                    video_path = next(
                        (p for p in paths if str(p).endswith((".mp4", ".webm", ".mov"))), None
                    )
            except (json.JSONDecodeError, TypeError):
                pass

    # Thumbnail redirect allowlist — shared by both "no video" and "video cleaned up" paths
    _allowed_redirect_domains = {
        "i.ytimg.com",
        "cdn.akamai.steamstatic.com",
        "cdn.cloudflare.steamstatic.com",
        "image.tmdb.org",
    }
    if _tunnel_url:
        _allowed_redirect_domains.add(urlparse(_tunnel_url).hostname or "")

    if not video_path or not isinstance(video_path, str):
        # No video path — redirect to thumbnail if available
        thumb = fields.get("thumbnail_url")
        if thumb and isinstance(thumb, str) and thumb.startswith("http"):
            parsed_thumb = urlparse(thumb)
            if parsed_thumb.hostname in _allowed_redirect_domains:
                return redirect(thumb)
        # Return a transparent 1×1 PNG instead of 404 to prevent broken <img> icons
        return _placeholder_image()

    resolved = Path(video_path).resolve()

    # Security: must be under PROJECT_ROOT or GENLAB_ROOT
    project_root_resolved = PROJECT_ROOT.resolve()
    genlab_root_resolved = GENLAB_ROOT.resolve()
    try:
        resolved.relative_to(project_root_resolved)
    except ValueError:
        try:
            resolved.relative_to(genlab_root_resolved)
        except ValueError:
            return _api_error(error="Access denied", code=403)

    if not resolved.is_file():
        # Fallback: check .tmp/renders/ (permanent copies survive cleanup)
        renders_dir = PROJECT_ROOT / ".tmp" / "renders"
        fallback = renders_dir / resolved.name
        if not fallback.is_file():
            # Try candidate_id subdirectory
            bid = fields.get("candidate_id") or record.get("id") or ""
            if bid:
                fallback = renders_dir / bid[:16] / resolved.name
        if fallback.is_file():
            try:
                fallback.resolve().relative_to(project_root_resolved)
            except ValueError:
                return _api_error(error="Access denied", code=403)
            resolved = fallback
        else:
            # Video cleaned up — redirect to thumbnail if available
            thumb = fields.get("thumbnail_url")
            if thumb and isinstance(thumb, str) and thumb.startswith("http"):
                parsed_thumb2 = urlparse(thumb)
                if parsed_thumb2.hostname in _allowed_redirect_domains:
                    return redirect(thumb)
            return _placeholder_image()

    mime = mimetypes.guess_type(str(resolved))[0] or "video/mp4"
    # Serve with explicit Content-Length and Accept-Ranges for proper video seeking.
    # eventlet workers can stall on Range requests without Content-Length.
    file_size = resolved.stat().st_size
    response = send_file(str(resolved), mimetype=mime, conditional=True)
    response.headers["Content-Length"] = str(file_size)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/api/media/<path:filepath>")
def serve_media(filepath):
    """Serve local media files (PNG/MP4) with strict path validation.

    Serves files under PROJECT_ROOT (GenLab workspace root) or GENLAB_ROOT
    as a secondary lookup (handles legacy absolute paths from older pipeline runs).
    Rejects absolute paths, path traversal sequences, and non-media file types.
    """
    # Security: reject absolute paths, URL-encoded traversal, and backslashes
    from urllib.parse import unquote

    decoded = unquote(filepath)
    if decoded.startswith("/") or ".." in decoded or "\\" in decoded:
        logger.warning("Blocked path traversal attempt: %s", filepath[:200])
        abort(403)

    # Try PROJECT_ROOT first (BB media), then GENLAB_ROOT (all niches).
    # Also allow MEDIA_VOLUME for symlinked media dirs (e.g. /mnt/genlab-media/).
    _MEDIA_VOLUME = Path(os.environ.get("GENLAB_MEDIA_VOLUME", "/mnt/genlab-media"))
    resolved = None
    allowed_roots = [PROJECT_ROOT, GENLAB_ROOT, _MEDIA_VOLUME]
    for root in (PROJECT_ROOT, GENLAB_ROOT):
        candidate = (root / filepath).resolve()
        # After resolving symlinks, the path may land in MEDIA_VOLUME
        if not any(
            str(candidate).startswith(str(ar.resolve())) for ar in allowed_roots if ar.exists()
        ):
            continue
        if candidate.is_file():
            resolved = candidate
            break
        # visual_paths often point to a directory containing the .mp4
        # (e.g., visuals/<candidate_id>/ with <hash>_reel.mp4 inside)
        if candidate.is_dir():
            media_exts = (".mp4", ".webm", ".mov", ".png", ".jpg")
            for child in candidate.iterdir():
                if child.is_file() and child.suffix.lower() in media_exts:
                    resolved = child
                    break
            if resolved:
                break

    if resolved is None:
        abort(404)

    # Only serve media files
    suffix = resolved.suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".mp4", ".webm", ".gif"}
    if suffix not in allowed:
        abort(403)

    mime = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    response = send_file(str(resolved), mimetype=mime, conditional=True)
    file_size = resolved.stat().st_size
    response.headers["Content-Length"] = str(file_size)
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


# ══════════════════════════════════════════════════════════════
# Express Pipeline Runner
# ══════════════════════════════════════════════════════════════


def run_express_pipeline(run_id: str, lock_fd=None):
    """Run express lane steps, emit WebSocket progress after each.

    `lock_fd` is the flock-held file handle from `trigger_express`; the
    worker owns the lock for the run's lifetime. The on-disk lock is the
    source of truth for whether a run is in flight; the in-memory
    express_state dict is informational only.
    """
    run_dir = PROJECT_ROOT / ".tmp" / "runs" / run_id
    express_tp = str(run_dir / "express_trend_pack.json")
    total_start = time.time()

    steps = [
        (
            "classify_urgency",
            [
                VENV_PYTHON,
                "execution/classify_urgency.py",
                "--run-id",
                run_id,
            ],
        ),
        (
            "compose_blueprints",
            [
                VENV_PYTHON,
                "execution/compose_blueprints.py",
                "--trend-pack",
                express_tp,
                "--run-id",
                run_id,
            ],
        ),
        (
            "generate_content",
            [
                VENV_PYTHON,
                "execution/generate_content.py",
                "--run-id",
                run_id,
            ],
        ),
        (
            "run_qc_gates",
            [
                VENV_PYTHON,
                "execution/run_qc_gates.py",
                "--run-id",
                run_id,
            ],
        ),
    ]

    socketio.emit(
        "express_progress",
        {
            "type": "started",
            "run_id": run_id,
            "total_steps": len(steps),
        },
    )

    all_passed = True
    step_results = []

    for i, (step_name, cmd) in enumerate(steps):
        with _express_lock:
            express_state["current_step"] = step_name
        socketio.emit(
            "express_progress",
            {
                "type": "step_start",
                "step": step_name,
                "step_index": i,
                "total_steps": len(steps),
            },
        )

        step_start = time.time()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(PROJECT_ROOT),
                timeout=300,
            )
            elapsed = time.time() - step_start
            success = result.returncode == 0

            step_result = {
                "step": step_name,
                "success": success,
                "elapsed_s": round(elapsed, 2),
                "total_elapsed_s": round(time.time() - total_start, 2),
            }

            if not success:
                step_result["error"] = result.stderr[-300:] if result.stderr else "Unknown error"
                all_passed = False

        except Exception as e:
            elapsed = time.time() - step_start
            step_result = {
                "step": step_name,
                "success": False,
                "elapsed_s": round(elapsed, 2),
                "total_elapsed_s": round(time.time() - total_start, 2),
                "error": type(e).__name__,
            }
            all_passed = False

        step_results.append(step_result)
        with _express_lock:
            express_state["steps_completed"].append(step_result)

        socketio.emit(
            "express_progress",
            {
                "type": "step_complete",
                **step_result,
                "step_index": i,
                "total_steps": len(steps),
            },
        )

    total_elapsed = time.time() - total_start
    with _express_lock:
        express_state["running"] = False
        express_state["current_step"] = None
        express_state["last_run"] = {
            "run_id": run_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "total_seconds": round(total_elapsed, 2),
            "all_passed": all_passed,
            "steps": step_results,
        }

    socketio.emit(
        "express_progress",
        {
            "type": "complete",
            "run_id": run_id,
            "total_seconds": round(total_elapsed, 2),
            "all_passed": all_passed,
            "steps": step_results,
        },
    )

    socketio.emit("blueprints_updated", {})

    logger.info(
        "Express pipeline %s: %s in %.1fs",
        "PASSED" if all_passed else "FAILED",
        run_id,
        total_elapsed,
    )

    # Release the on-disk lock — must be the last thing we do so a follow-up
    # POST can immediately claim it.
    if lock_fd is not None:
        try:
            lock_fd.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════


def load_local_blueprints():
    """Load blueprints from the most recent run directory."""
    runs_dir = PROJECT_ROOT / ".tmp" / "runs"
    if not runs_dir.exists():
        return []

    candidates = []
    for check_id in ["test_express"]:
        bp_path = runs_dir / check_id / "blueprint_pack.json"
        if bp_path.exists():
            candidates.append(bp_path)

    for run_dir in sorted(runs_dir.iterdir(), reverse=True):
        if run_dir.is_dir() and run_dir.name != "test_express":
            bp_path = run_dir / "blueprint_pack.json"
            if bp_path.exists():
                candidates.append(bp_path)
                break

    blueprints = []
    seen_ids = set()

    for bp_path in candidates:
        try:
            with open(bp_path) as f:
                data = json.load(f)
            for bp in data.get("candidates", []):
                bp_id = bp.get("candidate_id", bp.get("id", str(len(blueprints))))
                if bp_id not in seen_ids:
                    seen_ids.add(bp_id)
                    blueprints.append(normalize_blueprint(bp, source="local"))
        except Exception as e:
            logger.warning("Failed to load %s: %s", bp_path, e)

    return blueprints


def normalize_blueprint(bp, source="unknown"):
    """Normalize a blueprint record to a consistent format for the UI."""
    if source == "backlog":
        fields = bp.get("fields", bp)
        paths = _parse_visual_paths(fields.get("visual_paths", "[]"))
        video_url = _resolve_video_url(bp)
        # Frontend expects visual_paths as a string (first video URL), not array.
        # Resolve to a browser-accessible URL via _resolve_video_url().
        first_url = video_url
        if not first_url and paths:
            first_url = paths[0]
        return {
            "id": bp.get("id", ""),
            "source": "backlog",
            "title": fields.get("title") or fields.get("story_title") or fields.get("angle", ""),
            "hook": fields.get("hook") or fields.get("hook_text") or fields.get("Hook", ""),
            "caption": fields.get("caption", ""),
            "slide_content": fields.get("slide_content", ""),
            "format": fields.get("format", fields.get("Format", "reel")),
            "template_id": fields.get("template_id", ""),
            "status": fields.get("status", fields.get("Status", "DRAFTED")),
            "niche_id": fields.get("niche_id", ""),
            "priority_score": fields.get("priority_score", 0),
            "action_taken": fields.get("action_taken", ""),
            "scheduled_for": fields.get("scheduled_for", ""),
            "video_url": fields.get("video_url", ""),
            "urgency": fields.get("urgency", ""),
            "urgency_score": fields.get("urgency_score", 0),
            "category": fields.get("category", ""),
            "hashtags": fields.get("hashtags", ""),
            "slide_previews": fields.get("slide_previews", []),
            "candidate_id": fields.get("candidate_id", ""),
            "visual_paths": first_url,
            "all_media_urls": [video_url] if video_url else paths,
        }
    else:
        paths = _parse_visual_paths(bp.get("visual_paths", "[]"))
        first_path = paths[0] if paths else None
        return {
            "id": bp.get("candidate_id", bp.get("id", "")),
            "source": "local",
            "title": bp.get("story_title", bp.get("title", "")),
            "hook": bp.get("hook", bp.get("headline", "")),
            "caption": bp.get("caption", ""),
            "slide_content": _format_slides(bp.get("slides", bp.get("slide_content", []))),
            "format": bp.get("format", "reel"),
            "template_id": bp.get("template_id", ""),
            "status": bp.get("status", "DRAFTED"),
            "urgency": bp.get("urgency", bp.get("express", "")),
            "urgency_score": bp.get("urgency_score", bp.get("priority_score", 0)),
            "category": bp.get("category", bp.get("template_category", "")),
            "hashtags": bp.get("hashtags", ""),
            "slide_previews": [],
            "candidate_id": bp.get("candidate_id", ""),
            "visual_paths": first_path,
            "all_media_urls": paths,
        }


def _format_slides(slides):
    """Format slide content for display."""
    if isinstance(slides, str):
        return slides
    if isinstance(slides, list):
        parts = []
        for i, s in enumerate(slides):
            if isinstance(s, dict):
                text = s.get("text", s.get("content", str(s)))
            else:
                text = str(s)
            parts.append(f"Slide {i + 1}: {text[:100]}")
        return " | ".join(parts)
    return str(slides) if slides else ""


def _parse_visual_paths(raw):
    """Parse visual_paths field — returns list of absolute path strings."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw if p]
    if isinstance(raw, str):
        try:
            paths = json.loads(raw)
            if isinstance(paths, list):
                return [str(p) for p in paths if p]
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def load_latest_report():
    """Load the most recent express E2E report."""
    runs_dir = PROJECT_ROOT / ".tmp" / "runs"

    for check_id in ["test_express"]:
        report_path = runs_dir / check_id / "express_e2e_report.json"
        if report_path.exists():
            try:
                with open(report_path) as f:
                    return json.load(f)
            except Exception:
                pass

    return None


@app.route("/api/ws-health")
def ws_health():
    """WebSocket health check — verify SocketIO is initialized."""
    try:
        rooms = list(socketio.server.manager.rooms.get("/", {}).keys())
    except Exception:
        rooms = []
    return jsonify(
        {
            "socketio_initialized": socketio is not None,
            "async_mode": _async_mode,
            "cors_origins": socketio.server.cors_allowed_origins,
            "rooms": rooms,
        }
    )


@app.route("/manifest.json")
def pwa_manifest():
    """PWA manifest — eliminates console 404 on every page load."""
    return jsonify(
        {
            "name": "Gen Lab",
            "short_name": "GenLab",
            "description": "Multi-niche content automation dashboard",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#08080D",
            "theme_color": "#3B82F6",
            "icons": [],
        }
    )


# ══════════════════════════════════════════════════════════════
# SPA catch-all (must be LAST route)
# ══════════════════════════════════════════════════════════════


@app.route("/<path:path>")
def serve_spa(path):
    """Serve static files from dashboard/dist, fall back to index.html for client-side routing."""
    # Never intercept API routes — let Flask return 404 naturally
    if path.startswith("api/"):
        abort(404)
    file_path = _DASHBOARD_DIST / path
    if _DASHBOARD_DIST.exists() and file_path.exists() and file_path.is_file():
        resp = send_from_directory(str(_DASHBOARD_DIST), path)
        # Vite emits hash-named files under /assets/ — the URL changes
        # whenever the file content changes, so we can cache aggressively.
        # Marking immutable tells browsers to skip even the conditional
        # revalidation round-trip on every navigation.
        # Non-asset static files (favicon.ico, manifest.json, etc.) get a
        # moderate cache so they're still revalidated periodically.
        if path.startswith("assets/"):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
        return resp
    # Client-side route — let React Router handle it
    if _DASHBOARD_DIST.exists() and (_DASHBOARD_DIST / "index.html").exists():
        resp = send_from_directory(str(_DASHBOARD_DIST), "index.html")
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        return resp
    return Response("Not found", status=404)


# ══════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════


def _watch_pipeline_progress():
    """Background thread: emit stage-change + log events via WebSocket.

    Polls .tmp/ progress files every 3 seconds for stage changes.
    Tails JSONL log files every cycle and emits new log lines as
    ``pipeline_logs`` events for real-time dashboard display.
    """
    import time as _t

    _GENLAB_ROOT = Path(os.environ.get("GENLAB_ROOT", _DASHBOARD_ROOT.parent))
    _LOGS_DIR = _GENLAB_ROOT / ".tmp"

    last_stages: dict[str, str] = {}
    # Track last-read byte offset per log file for incremental tailing
    log_offsets: dict[str, int] = {}

    while True:
        try:
            for niche_id in ("ai_creators", "gaming", "sports", "movies", "anime"):
                # ── Stage progress (existing) ────────────────────────
                progress_file = PROJECT_ROOT / ".tmp" / f"{niche_id}_progress.json"
                if progress_file.exists():
                    try:
                        data = json.loads(progress_file.read_text())
                    except (json.JSONDecodeError, OSError):
                        data = None
                    if data:
                        stage = data.get("stage")
                        if stage and stage != last_stages.get(niche_id):
                            last_stages[niche_id] = stage
                            event_data = {
                                "niche_id": niche_id,
                                "stage": stage,
                                "stage_index": data.get("stage_index"),
                                "items_processed": data.get("items_processed"),
                                "items_total": data.get("items_total"),
                                "elapsed_seconds": data.get("elapsed_seconds"),
                            }
                            socketio.emit("pipeline_progress", event_data, room=f"niche:{niche_id}")
                            socketio.emit("pipeline_progress", event_data)
                elif niche_id in last_stages:
                    del last_stages[niche_id]
                    event_data = {"niche_id": niche_id}
                    socketio.emit("pipeline_complete", event_data, room=f"niche:{niche_id}")
                    socketio.emit("pipeline_complete", event_data)

                # ── Log tailing ──────────────────────────────────────
                log_file = _LOGS_DIR / f"{niche_id}_pipeline_logs.jsonl"
                if not log_file.exists():
                    log_offsets.pop(niche_id, None)
                    continue

                try:
                    file_size = log_file.stat().st_size
                except OSError:
                    continue

                offset = log_offsets.get(niche_id, 0)
                # File was truncated or rotated — reset
                if file_size < offset:
                    offset = 0

                if file_size <= offset:
                    continue

                try:
                    with open(log_file, encoding="utf-8") as f:
                        f.seek(offset)
                        new_lines = f.readlines()
                        log_offsets[niche_id] = f.tell()
                except OSError:
                    continue

                # Parse and emit new log entries (batch up to 50 per cycle)
                entries = []
                for line in new_lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue

                if entries:
                    batch = entries[-50:]  # cap per-cycle emissions
                    event = {"niche_id": niche_id, "lines": batch}
                    socketio.emit("pipeline_logs", event, room=f"niche:{niche_id}")
                    socketio.emit("pipeline_logs", event)

        except Exception:
            logger.exception("Pipeline watcher error")
        _t.sleep(3)


# Start pipeline watcher thread
threading.Thread(target=_watch_pipeline_progress, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(
        description="Express Lane web review dashboard",
    )
    parser.add_argument("--port", type=int, default=5151, help="Port (default: 5151)")
    parser.add_argument("--dry-run", action="store_true", help="No backlog writes")
    parser.add_argument(
        "--local", action="store_true", help="Use local blueprint files instead of backlog"
    )
    parser.add_argument("--debug", action="store_true", help="Flask debug mode")
    args = parser.parse_args()

    app.config["DRY_RUN"] = args.dry_run
    app.config["LOCAL_MODE"] = args.local

    # Update CORS origins if custom port — keep tunnel URL
    if args.port != _DEFAULT_PORT:
        socketio.server.cors_allowed_origins = [
            f"http://localhost:{args.port}",
            f"http://127.0.0.1:{args.port}",
            "https://review.aspirehub.ai",
        ]

    mode_flags = []
    if args.dry_run:
        mode_flags.append("DRY RUN")
    if args.local:
        mode_flags.append("LOCAL MODE")
    mode_str = f" ({', '.join(mode_flags)})" if mode_flags else ""

    print(f"\n  \u26a1 Express Lane Review Dashboard{mode_str}")
    print(f"  http://localhost:{args.port}")
    print("  Press Ctrl+C to stop\n")

    host = "127.0.0.1"
    # Safety: refuse to bind on all interfaces without authentication
    if not _AUTH_ENABLED and host == "0.0.0.0":
        logger.critical(
            "Refusing to bind on 0.0.0.0 without authentication. "
            "Set REVIEW_AUTH_USER and REVIEW_AUTH_PASS in .env."
        )
        sys.exit(1)
    socketio.run(
        app,
        host=host,
        port=args.port,
        debug=args.debug,
        allow_unsafe_werkzeug=True,  # Dev-only path; production uses gunicorn
    )


if __name__ == "__main__":
    main()
