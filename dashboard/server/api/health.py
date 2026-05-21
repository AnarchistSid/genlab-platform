"""System health API for the operations dashboard.

Routes:
    GET /api/v1/health/detailed  -- comprehensive system health check
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

from flask import Blueprint

from server.core.responses import api_error, api_success

logger = logging.getLogger(__name__)
bp = Blueprint("health_api", __name__, url_prefix="/api/v1/health")

_DASHBOARD_ROOT = Path(__file__).resolve().parent.parent.parent
_GENLAB_ROOT = _DASHBOARD_ROOT.parent
_RUNS_DIR = _GENLAB_ROOT / ".tmp" / "runs"
_LOGS_DIR = _GENLAB_ROOT / ".logs"

NICHE_IDS = ["ai_creators", "gaming", "sports", "movies", "anime"]


def _check_services() -> dict[str, dict]:
    """Ping Redis and confirm dashboard is alive."""
    services: dict[str, dict] = {}

    # Redis — connect via TCP using the python redis library, NOT
    # via the `redis-cli` subprocess. The CLI isn't installed on
    # Hetzner (Redis runs in Docker) so the old check always reported
    # "Down" even though the engagement worker was actively talking
    # to it. 2026-05-21 audit found Redis: Down in dashboard while
    # the container was Up 2 weeks (healthy).
    import os

    try:
        import redis as _redis

        client = _redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "6379")),
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        if client.ping():
            services["redis"] = {"status": "up"}
        else:
            services["redis"] = {"status": "down", "error": "ping returned falsy"}
    except ImportError:
        services["redis"] = {"status": "down", "error": "redis lib not installed"}
    except Exception as exc:
        services["redis"] = {"status": "down", "error": str(exc)[:120]}

    # Prefect
    try:
        result = subprocess.run(
            ["curl", "-sf", "http://localhost:4200/api/health"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        services["prefect"] = {"status": "up" if result.returncode == 0 else "down"}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        services["prefect"] = {"status": "down", "error": "prefect unreachable"}

    # Dashboard itself is always up if we're responding
    services["dashboard"] = {"status": "up"}

    return services


def _get_last_run_per_niche() -> dict[str, dict | None]:
    """Scan .tmp/runs/ for the latest run_report.json per niche."""
    result: dict[str, dict | None] = {nid: None for nid in NICHE_IDS}

    if not _RUNS_DIR.is_dir():
        return result

    try:
        run_dirs = sorted(_RUNS_DIR.iterdir(), reverse=True)
    except OSError:
        return result

    # Track which niches we've already found (most recent first)
    found: set[str] = set()

    for run_dir in run_dirs:
        if not run_dir.is_dir():
            continue
        name = run_dir.name  # e.g. "gaming_20260316_165319"
        # Extract niche from directory name prefix
        matched_niche = None
        for nid in NICHE_IDS:
            if name.startswith(nid + "_"):
                matched_niche = nid
                break
        if not matched_niche or matched_niche in found:
            continue

        run_info: dict = {
            "run_id": name,
            "directory": str(run_dir),
        }

        # Try to read run_report.json for richer data
        report_path = run_dir / "run_report.json"
        if report_path.is_file():
            try:
                with open(report_path) as f:
                    report = json.load(f)
                run_info["started_at"] = report.get("started_at")
                run_info["finished_at"] = report.get("finished_at")
                run_info["status"] = report.get("status", "unknown")
                run_info["blueprints_created"] = report.get("blueprints_created", 0)
            except (json.JSONDecodeError, OSError):
                pass

        # Fallback: use directory mtime
        if "started_at" not in run_info:
            try:
                mtime = run_dir.stat().st_mtime
                run_info["started_at"] = datetime.fromtimestamp(mtime, tz=UTC).isoformat()
            except OSError:
                pass

        result[matched_niche] = run_info
        found.add(matched_niche)

        if len(found) == len(NICHE_IDS):
            break

    return result


def _calculate_error_rate_24h() -> dict:
    """Count ERROR lines in .logs/ files from the last 24 hours."""
    total_lines = 0
    error_lines = 0
    files_checked = 0

    if not _LOGS_DIR.is_dir():
        return {"total_lines": 0, "error_lines": 0, "error_rate_pct": 0.0, "files_checked": 0}

    cutoff = time.time() - 86400  # 24 hours ago

    try:
        log_files = [f for f in _LOGS_DIR.iterdir() if f.suffix == ".log" and f.is_file()]
    except OSError:
        log_files = []

    for log_file in log_files:
        try:
            if log_file.stat().st_mtime < cutoff:
                continue  # Skip files not modified in the last 24h
        except OSError:
            continue

        files_checked += 1
        try:
            content = log_file.read_text(errors="replace")
            lines = content.splitlines()
            total_lines += len(lines)
            error_lines += sum(1 for line in lines if "ERROR" in line or "CRITICAL" in line)
        except OSError:
            continue

    rate = (error_lines / total_lines * 100) if total_lines > 0 else 0.0
    return {
        "total_lines": total_lines,
        "error_lines": error_lines,
        "error_rate_pct": round(rate, 2),
        "files_checked": files_checked,
    }


def _get_disk_usage() -> dict:
    """Return disk usage percentage for the GenLab workspace."""
    try:
        usage = shutil.disk_usage(str(_GENLAB_ROOT))
        pct = round(usage.used / usage.total * 100, 1)
        return {
            "total_gb": round(usage.total / (1024**3), 1),
            "used_gb": round(usage.used / (1024**3), 1),
            "free_gb": round(usage.free / (1024**3), 1),
            "used_pct": pct,
        }
    except OSError:
        return {"used_pct": -1, "error": "unable to read disk usage"}


def _get_poller_status() -> dict[str, dict]:
    """Read engagement poller log files and report error counts."""
    pollers: dict[str, dict] = {}

    if not _LOGS_DIR.is_dir():
        return pollers

    try:
        poller_logs = [
            f
            for f in _LOGS_DIR.iterdir()
            if f.name.startswith("engagement_poller_") and f.suffix == ".log"
        ]
    except OSError:
        return pollers

    for log_file in poller_logs:
        name = log_file.stem  # e.g. "engagement_poller_twitter_gaming"
        try:
            content = log_file.read_text(errors="replace")
            lines = content.splitlines()
            error_count = sum(1 for line in lines if "ERROR" in line)
            mtime = log_file.stat().st_mtime
            pollers[name] = {
                "total_lines": len(lines),
                "error_count": error_count,
                "last_modified": datetime.fromtimestamp(mtime, tz=UTC).isoformat(),
            }
        except OSError:
            pollers[name] = {"error": "unable to read log"}

    return pollers


def _check_postgres() -> dict:
    """Check PostgreSQL connectivity."""
    import os

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn or os.getenv("GENLAB_USE_POSTGRES", "").lower() != "true":
        return {"status": "not_configured"}
    try:
        result = subprocess.run(
            ["pg_isready", "-h", "localhost", "-p", "5432", "-U", "genlab"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {"status": "up" if result.returncode == 0 else "down"}
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {"status": "unknown", "error": "pg_isready unavailable"}


def _get_launchagent_status() -> dict[str, str]:
    """Check which GenLab LaunchAgents are running/stopped."""
    agents: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        running_labels = set()
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and "com.genlab." in parts[2]:
                label = parts[2].strip()
                pid = parts[0].strip()
                running_labels.add(label)
                agents[label] = "running" if pid != "-" else "loaded"

        # Also check for plist files that aren't loaded
        import glob
        import os

        home = os.path.expanduser("~")
        for plist in glob.glob(f"{home}/Library/LaunchAgents/com.genlab.*.plist"):
            label = os.path.basename(plist).replace(".plist", "")
            if label not in agents:
                agents[label] = "stopped"
    except Exception:
        pass
    return agents


def _get_active_backends() -> dict:
    """Report active storage backends."""
    backends = {
        "primary": "sharepoint",
        "cache": "disk (.tmp/)",
        "media": "disk (.tmp/media/)",
    }
    # Check if storage_backends.yaml exists
    sb_path = _GENLAB_ROOT / "genlab-core" / "config" / "storage_backends.yaml"
    if sb_path.is_file():
        try:
            import yaml

            with open(sb_path) as f:
                sb_config = yaml.safe_load(f) or {}
            backends.update(sb_config)
        except Exception:
            pass
    return backends


@bp.route("/detailed", methods=["GET"])
def detailed_health():
    """Return comprehensive system health."""
    try:
        return api_success(
            data={
                "services": _check_services(),
                "last_run": _get_last_run_per_niche(),
                "error_rate_24h": _calculate_error_rate_24h(),
                "disk_usage": _get_disk_usage(),
                "engagement_pollers": _get_poller_status(),
                "postgres": _check_postgres(),
                "launch_agents": _get_launchagent_status(),
                "storage_backend": _get_active_backends(),
                "checked_at": datetime.now(UTC).isoformat(),
            }
        )
    except Exception as exc:
        logger.exception("Health check failed: %s", exc)
        return api_error(error=str(exc), code=500)
