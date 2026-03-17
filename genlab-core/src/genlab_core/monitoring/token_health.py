"""Token & API health checker — shared across all Gen Lab channels.

Checks AI platform API connections and social platform tokens via
genlab_core's HealthCheckable protocol. Platform-specific legacy
checks (YouTube OAuth, X API, Facebook Page) remain in channel
packages that own those credentials.

Usage as library:
    from genlab_core.monitoring.token_health import run_all_checks
    summary = run_all_checks()

Usage as CLI:
    uv run python -m genlab_core.monitoring.token_health
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Meta tokens with <14 days remaining get flagged
META_REFRESH_THRESHOLD_DAYS = 14


# ══════════════════════════════════════════════════════════════
# AI-SERVICE CHECKS
# ══════════════════════════════════════════════════════════════


def check_anthropic() -> dict:
    """Test Anthropic API key by making a minimal request."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {"platform": "anthropic", "status": "missing", "message": "ANTHROPIC_API_KEY not set"}

    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        return {
            "platform": "anthropic",
            "status": "healthy",
            "message": f"Claude Haiku OK ({msg.usage.input_tokens + msg.usage.output_tokens} tokens)",
        }
    except Exception as e:
        error_str = str(e).lower()
        if "credit" in error_str or "billing" in error_str or "insufficient" in error_str:
            return {"platform": "anthropic", "status": "credits_depleted", "message": str(e)[:200]}
        return {"platform": "anthropic", "status": "error", "message": str(e)[:200]}


def check_openai() -> dict:
    """Test OpenAI API key."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"platform": "openai", "status": "missing", "message": "OPENAI_API_KEY not set"}

    try:
        from openai import OpenAI
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        return {
            "platform": "openai",
            "status": "healthy",
            "message": f"gpt-4o-mini OK ({resp.usage.total_tokens} tokens)",
        }
    except Exception as e:
        error_str = str(e).lower()
        if "quota" in error_str or "billing" in error_str or "insufficient" in error_str:
            return {"platform": "openai", "status": "credits_depleted", "message": str(e)[:200]}
        return {"platform": "openai", "status": "error", "message": str(e)[:200]}


# ══════════════════════════════════════════════════════════════
# META / INSTAGRAM CHECK
# ══════════════════════════════════════════════════════════════


def check_meta_token() -> dict:
    """Check Instagram/Meta token validity via Facebook Graph API.

    Uses graph.facebook.com (not graph.instagram.com) since the token is
    an EAA Page token. Does NOT attempt ig_refresh_token.
    """
    token = os.getenv("META_ACCESS_TOKEN", "").strip()
    if not token:
        return {"platform": "instagram", "status": "missing", "message": "META_ACCESS_TOKEN not set"}

    page_id = os.getenv("META_FB_PAGE_ID", "").strip()

    try:
        verify_resp = requests.get(
            f"https://graph.facebook.com/v21.0/{page_id or 'me'}",
            params={"fields": "id,name,instagram_business_account{username}", "access_token": token},
            timeout=15,
        )

        if not verify_resp.ok:
            error_data = verify_resp.json().get("error", {})
            error_code = error_data.get("code", 0)
            error_msg = error_data.get("message", verify_resp.text)[:200]

            if error_code == 190 or "expired" in error_msg.lower() or "invalid" in error_msg.lower():
                return {
                    "platform": "instagram",
                    "status": "expired",
                    "message": f"Token expired/invalid. Manual re-auth required. Error: {error_msg}",
                }
            return {"platform": "instagram", "status": "error", "message": error_msg}

        data = verify_resp.json()
        page_name = data.get("name", "unknown")
        ig_acct = data.get("instagram_business_account", {})
        ig_username = ig_acct.get("username", "unknown")

        debug_resp = requests.get(
            "https://graph.facebook.com/v21.0/debug_token",
            params={"input_token": token, "access_token": token},
            timeout=15,
        )
        expires_at = 0
        if debug_resp.ok:
            debug_data = debug_resp.json().get("data", {})
            expires_at = debug_data.get("expires_at", 0)

        if expires_at == 0:
            return {
                "platform": "instagram",
                "status": "healthy",
                "message": f"@{ig_username} (page: {page_name}) — permanent page token",
                "days_remaining": None,
            }
        else:
            remaining = (datetime.fromtimestamp(expires_at, tz=timezone.utc) - datetime.now(timezone.utc)).days
            return {
                "platform": "instagram",
                "status": "healthy" if remaining > 7 else "expiring",
                "message": f"@{ig_username} (page: {page_name}) — {remaining} days remaining",
                "days_remaining": remaining,
            }

    except requests.exceptions.RequestException as e:
        return {"platform": "instagram", "status": "error", "message": f"Network error: {e}"}
    except Exception as e:
        return {"platform": "instagram", "status": "expired", "message": str(e)[:200]}


def refresh_meta_token(current_token: str) -> dict:
    """No-op: EAA page tokens are permanent and don't need refresh."""
    return {"success": False, "error": "EAA page tokens are permanent — refresh not needed"}


# ══════════════════════════════════════════════════════════════
# ENV-VAR-ONLY PLATFORM CHECKS
# ══════════════════════════════════════════════════════════════


def check_tiktok() -> dict:
    """Check TikTok access token health (24hr expiry)."""
    token = os.getenv("TIKTOK_ACCESS_TOKEN", "").strip()
    if not token:
        return {"platform": "tiktok", "status": "missing", "message": "TIKTOK_ACCESS_TOKEN not set"}

    issued_str = os.getenv("TIKTOK_TOKEN_ISSUED_AT", "").strip()
    if not issued_str:
        return {"platform": "tiktok", "status": "healthy", "message": "Token set but age unknown (TIKTOK_TOKEN_ISSUED_AT missing)"}

    try:
        issued_at = datetime.fromisoformat(issued_str)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - issued_at).total_seconds() / 3600
        remaining = max(0, 24 - age_hours)

        audit = os.getenv("TIKTOK_AUDIT_APPROVED", "false").lower() == "true"
        audit_note = "" if audit else " (SELF_ONLY until audit approved)"

        if remaining > 6:
            return {"platform": "tiktok", "status": "healthy",
                    "message": f"Token valid ({remaining:.0f}h remaining){audit_note}"}
        elif remaining > 1:
            return {"platform": "tiktok", "status": "healthy",
                    "message": f"Token expires soon ({remaining:.1f}h remaining){audit_note}"}
        else:
            return {"platform": "tiktok", "status": "expired",
                    "message": f"Token expired or expiring ({remaining:.1f}h remaining) — needs refresh{audit_note}"}
    except (ValueError, TypeError) as e:
        return {"platform": "tiktok", "status": "error", "message": f"Cannot parse TIKTOK_TOKEN_ISSUED_AT: {e}"}


def check_threads() -> dict:
    """Check Threads token health (60-day expiry)."""
    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        return {"platform": "threads", "status": "missing", "message": "THREADS_ACCESS_TOKEN not set"}

    issued_str = os.getenv("THREADS_TOKEN_ISSUED_AT", "").strip()
    if not issued_str:
        return {"platform": "threads", "status": "healthy", "message": "Token set but age unknown"}

    try:
        issued_at = datetime.fromisoformat(issued_str)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - issued_at).days
        remaining = 60 - age_days

        if remaining > 15:
            return {"platform": "threads", "status": "healthy",
                    "message": f"Token valid ({remaining} days remaining)",
                    "days_remaining": remaining}
        elif remaining > 2:
            return {"platform": "threads", "status": "healthy",
                    "message": f"Token expiring soon ({remaining} days remaining) — refresh recommended",
                    "days_remaining": remaining}
        else:
            return {"platform": "threads", "status": "expired",
                    "message": f"Token critical ({remaining} days remaining) — refresh immediately",
                    "days_remaining": remaining}
    except (ValueError, TypeError) as e:
        return {"platform": "threads", "status": "error", "message": f"Cannot parse THREADS_TOKEN_ISSUED_AT: {e}"}


# ══════════════════════════════════════════════════════════════
# BACKLOG CHECK
# ══════════════════════════════════════════════════════════════


def check_backlog() -> dict:
    """Test data storage connection (PostgreSQL or Microsoft Lists)."""
    use_postgres = os.getenv("GENLAB_USE_POSTGRES", "").lower() == "true"
    dsn = os.getenv("DATABASE_URL", "")

    if use_postgres and dsn:
        # Postgres health check — simple SELECT 1
        try:
            from genlab_core.storage.postgres import PostgresBackend
            pg = PostgresBackend(dsn=dsn)
            rows = pg.find("blueprints", max_records=1)
            return {
                "platform": "database",
                "status": "healthy",
                "message": f"PostgreSQL connected ({len(rows)} test row)",
            }
        except Exception as e:
            return {"platform": "database", "status": "error", "message": str(e)[:200]}

    # SharePoint health check (legacy)
    tenant_id = (os.getenv("AZURE_TENANT_ID") or "").strip()
    client_id = (os.getenv("AZURE_CLIENT_ID") or "").strip()
    if not tenant_id or not client_id:
        return {
            "platform": "database",
            "status": "missing",
            "message": "No database configured. Set GENLAB_USE_POSTGRES=true + DATABASE_URL, "
                       "or AZURE_TENANT_ID + AZURE_CLIENT_ID for SharePoint.",
        }

    try:
        from genlab_core.http.backlog_client import BacklogClient
        client = BacklogClient()
        ok = client.health_check()
        if ok:
            return {"platform": "database", "status": "healthy", "message": "Microsoft Lists connected"}
        return {"platform": "database", "status": "error", "message": "Health check returned False"}
    except Exception as e:
        return {"platform": "database", "status": "error", "message": str(e)[:200]}


# ══════════════════════════════════════════════════════════════
# NATIVE HEALTHCHECKABLE PROTOCOL
# ══════════════════════════════════════════════════════════════


def _token_status_to_dict(ts) -> dict:
    """Convert a genlab_core TokenStatus dataclass to the legacy result dict."""
    if ts.valid:
        status = "expiring" if ts.needs_refresh else "healthy"
    else:
        status = "error"

    result: dict = {
        "platform": ts.platform,
        "status": status,
        "message": ts.message,
    }

    if ts.expires_at is not None:
        result["expires_at"] = ts.expires_at.isoformat()
        remaining_days = (ts.expires_at - datetime.now(timezone.utc)).days
        result["days_remaining"] = remaining_days

    if ts.details:
        result["details"] = ts.details

    return result


def _run_native_platform_checks() -> list[dict]:
    """Use HealthCheckable clients from genlab_core.platforms for social platforms."""
    try:
        from genlab_core.platforms import get_client, list_platforms
        from genlab_core.platforms.protocols import HealthCheckable
    except ImportError as exc:
        logger.warning("genlab_core.platforms not importable — skipping native checks: %s", exc)
        return []

    results = []
    for pid in list_platforms():
        try:
            client = get_client(pid)
            if not isinstance(client, HealthCheckable):
                logger.debug("Platform %s does not implement HealthCheckable — skipping", pid)
                continue
            ts = client.check_token_health()
            results.append(_token_status_to_dict(ts))
            logger.debug("Native check %s → %s", pid, ts.valid)
        except Exception as exc:
            logger.warning("Native health check failed for %s: %s", pid, exc)
            results.append({
                "platform": pid,
                "status": "error",
                "message": f"Native client check raised: {exc}",
            })

    return results


# ══════════════════════════════════════════════════════════════
# ORCHESTRATOR
# ══════════════════════════════════════════════════════════════


def run_all_checks() -> dict:
    """Run all platform health checks. Returns summary dict."""
    start = time.time()

    ai_results = [
        check_anthropic(),
        check_openai(),
    ]

    logger.info("Using native HealthCheckable clients for social platform checks")
    platform_results = _run_native_platform_checks()
    platform_results.append(check_backlog())

    results = ai_results + platform_results

    elapsed = round(time.time() - start, 1)

    healthy = [r for r in results if r["status"] in ("healthy", "refreshed")]
    unhealthy = [r for r in results if r["status"] not in ("healthy", "refreshed", "missing")]
    missing = [r for r in results if r["status"] == "missing"]

    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": elapsed,
        "total_checked": len(results),
        "healthy": len(healthy),
        "unhealthy": len(unhealthy),
        "missing": len(missing),
        "all_healthy": len(unhealthy) == 0,
        "results": results,
    }

    return summary


def main():
    """CLI entry point for token health checks."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    summary = run_all_checks()

    logger.info("=" * 60)
    logger.info("TOKEN & API HEALTH CHECK")
    logger.info("=" * 60)

    for r in summary["results"]:
        icon = {"healthy": "\u2705", "refreshed": "\U0001f504", "missing": "\u26a0\ufe0f",
                "expired": "\u274c", "error": "\u274c", "credits_depleted": "\U0001f4b8",
                "refresh_failed": "\u26a0\ufe0f"}.get(r["status"], "\u2753")
        logger.info("  %s %s: %s", icon, r["platform"].upper(), r["message"])

    logger.info("-" * 60)
    logger.info(
        "Summary: %d/%d healthy, %d unhealthy, %d missing",
        summary["healthy"], summary["total_checked"],
        summary["unhealthy"], summary["missing"],
    )
    logger.info("=" * 60)

    # Write report
    report_dir = Path.home() / ".genlab" / "health"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "token_health.json"
    report_path.write_text(json.dumps(summary, indent=2))
    logger.info("Report: %s", report_path)

    latest_path = report_dir / "latest.json"
    latest_path.write_text(json.dumps(summary, indent=2))

    import sys
    sys.exit(0 if summary["all_healthy"] else 1)


if __name__ == "__main__":
    main()
