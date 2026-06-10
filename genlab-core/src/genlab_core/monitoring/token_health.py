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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

from genlab_core.platforms.meta_api import META_GRAPH_API_VERSION, META_GRAPH_BASE_URL

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
        return {
            "platform": "anthropic",
            "status": "missing",
            "message": "ANTHROPIC_API_KEY not set",
        }

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
        return {
            "platform": "instagram",
            "status": "missing",
            "message": "META_ACCESS_TOKEN not set",
        }

    page_id = os.getenv("META_FB_PAGE_ID", "").strip()

    try:
        verify_resp = requests.get(
            f"{META_GRAPH_BASE_URL}/{page_id or 'me'}",
            params={
                "fields": "id,name,instagram_business_account{username}",
                "access_token": token,
            },
            timeout=15,
        )

        if not verify_resp.ok:
            error_data = verify_resp.json().get("error", {})
            error_code = error_data.get("code", 0)
            error_msg = error_data.get("message", verify_resp.text)[:200]

            if (
                error_code == 190
                or "expired" in error_msg.lower()
                or "invalid" in error_msg.lower()
            ):
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
            f"{META_GRAPH_BASE_URL}/debug_token",
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
            remaining = (datetime.fromtimestamp(expires_at, tz=UTC) - datetime.now(UTC)).days
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
        return {
            "platform": "tiktok",
            "status": "healthy",
            "message": "Token set but age unknown (TIKTOK_TOKEN_ISSUED_AT missing)",
        }

    try:
        issued_at = datetime.fromisoformat(issued_str)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=UTC)
        age_hours = (datetime.now(UTC) - issued_at).total_seconds() / 3600
        remaining = max(0, 24 - age_hours)

        audit = os.getenv("TIKTOK_AUDIT_APPROVED", "false").lower() == "true"
        audit_note = "" if audit else " (SELF_ONLY until audit approved)"

        if remaining > 6:
            return {
                "platform": "tiktok",
                "status": "healthy",
                "message": f"Token valid ({remaining:.0f}h remaining){audit_note}",
            }
        elif remaining > 1:
            return {
                "platform": "tiktok",
                "status": "healthy",
                "message": f"Token expires soon ({remaining:.1f}h remaining){audit_note}",
            }
        else:
            return {
                "platform": "tiktok",
                "status": "expired",
                "message": f"Token expired or expiring ({remaining:.1f}h remaining) — needs refresh{audit_note}",
            }
    except (ValueError, TypeError) as e:
        return {
            "platform": "tiktok",
            "status": "error",
            "message": f"Cannot parse TIKTOK_TOKEN_ISSUED_AT: {e}",
        }


def check_threads() -> dict:
    """Check Threads token health (60-day expiry)."""
    token = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
    if not token:
        return {
            "platform": "threads",
            "status": "missing",
            "message": "THREADS_ACCESS_TOKEN not set",
        }

    issued_str = os.getenv("THREADS_TOKEN_ISSUED_AT", "").strip()
    if not issued_str:
        return {"platform": "threads", "status": "healthy", "message": "Token set but age unknown"}

    try:
        issued_at = datetime.fromisoformat(issued_str)
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=UTC)
        age_days = (datetime.now(UTC) - issued_at).days
        remaining = 60 - age_days

        if remaining > 15:
            return {
                "platform": "threads",
                "status": "healthy",
                "message": f"Token valid ({remaining} days remaining)",
                "days_remaining": remaining,
            }
        elif remaining > 2:
            return {
                "platform": "threads",
                "status": "healthy",
                "message": f"Token expiring soon ({remaining} days remaining) — refresh recommended",
                "days_remaining": remaining,
            }
        else:
            return {
                "platform": "threads",
                "status": "expired",
                "message": f"Token critical ({remaining} days remaining) — refresh immediately",
                "days_remaining": remaining,
            }
    except (ValueError, TypeError) as e:
        return {
            "platform": "threads",
            "status": "error",
            "message": f"Cannot parse THREADS_TOKEN_ISSUED_AT: {e}",
        }


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
            return {
                "platform": "database",
                "status": "healthy",
                "message": "Microsoft Lists connected",
            }
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
        remaining_days = (ts.expires_at - datetime.now(UTC)).days
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
            results.append(
                {
                    "platform": pid,
                    "status": "error",
                    "message": f"Native client check raised: {exc}",
                }
            )

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
        "timestamp": datetime.now(UTC).isoformat(),
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
        icon = {
            "healthy": "\u2705",
            "refreshed": "\U0001f504",
            "missing": "\u26a0\ufe0f",
            "expired": "\u274c",
            "error": "\u274c",
            "credits_depleted": "\U0001f4b8",
            "refresh_failed": "\u26a0\ufe0f",
        }.get(r["status"], "\u2753")
        logger.info("  %s %s: %s", icon, r["platform"].upper(), r["message"])

    logger.info("-" * 60)
    logger.info(
        "Summary: %d/%d healthy, %d unhealthy, %d missing",
        summary["healthy"],
        summary["total_checked"],
        summary["unhealthy"],
        summary["missing"],
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

# Facebook Graph API error codes
_ERR_INVALID_TOKEN = 190
_ERR_EXPIRED_TOKEN = 463

# Permissions required for video publishing
_REQUIRED_PUBLISH_PERMISSIONS = frozenset(
    {
        "publish_video",
        "pages_manage_posts",
    }
)


def _verify_page_token(
    page_id: str, access_token: str, api_version: str = META_GRAPH_API_VERSION
) -> dict[str, Any]:
    """Verify the Facebook Page access token is valid and has required permissions.

    Two-step check:
      1. GET /{page-id}         — confirms token is valid and can read the Page.
      2. Debug Token API        — confirms token has publish_video + pages_manage_posts.

    Returns dict with keys: valid, page_name, error, scopes, missing_permissions.
    """
    # Step 1: Basic token validity
    url = f"https://graph.facebook.com/{api_version}/{page_id}"
    try:
        resp = requests.get(
            url,
            params={"fields": "id,name", "access_token": access_token},
            timeout=15,
        )
        data = resp.json()

        if "error" in data:
            error = data["error"]
            code = error.get("code", 0)
            message = error.get("message", "Unknown error")
            if code in (_ERR_INVALID_TOKEN, _ERR_EXPIRED_TOKEN):
                return {
                    "valid": False,
                    "page_name": "",
                    "error": f"Token expired or invalid (code {code}): {message}",
                    "scopes": [],
                    "missing_permissions": list(_REQUIRED_PUBLISH_PERMISSIONS),
                }
            return {
                "valid": False,
                "page_name": "",
                "error": f"API error (code {code}): {message}",
                "scopes": [],
                "missing_permissions": [],
            }

        page_name = data.get("name", "")

    except requests.RequestException as exc:
        return {
            "valid": False,
            "page_name": "",
            "error": f"Connection error: {exc}",
            "scopes": [],
            "missing_permissions": [],
        }

    # Step 2: Scope check via Debug Token API
    app_id = os.getenv("FB_APP_ID", "").strip()
    app_secret = os.getenv("FB_APP_SECRET", "").strip()

    granted_scopes: list[str] = []
    if app_id and app_secret:
        try:
            debug_resp = requests.get(
                f"https://graph.facebook.com/{api_version}/debug_token",
                params={
                    "input_token": access_token,
                    "access_token": f"{app_id}|{app_secret}",
                },
                timeout=15,
            )
            debug_data = debug_resp.json().get("data", {})
            granted_scopes = debug_data.get("scopes", [])
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Debug Token API call failed: %s", exc)

    missing = [p for p in _REQUIRED_PUBLISH_PERMISSIONS if p not in granted_scopes]

    return {
        "valid": True,
        "page_name": page_name,
        "error": "",
        "scopes": granted_scopes,
        "missing_permissions": missing,
    }


def check_facebook() -> dict:
    """Test Facebook Page token health.

    Uses Meta Graph API directly — no BlackboxBrief dependency.
    """
    token = os.getenv("FB_PAGE_ACCESS_TOKEN", "").strip()
    page_id = os.getenv("META_FB_PAGE_ID", "").strip()

    if not token:
        return {
            "platform": "facebook",
            "status": "missing",
            "message": "FB_PAGE_ACCESS_TOKEN not set (required — META_ACCESS_TOKEN is IG-only)",
        }
    if not page_id:
        return {
            "platform": "facebook",
            "status": "missing",
            "message": "META_FB_PAGE_ID not set",
        }

    try:
        result = _verify_page_token(page_id, token)
        if result["valid"]:
            page_name = result.get("page_name", "Unknown")
            scopes = result.get("scopes", [])
            return {
                "platform": "facebook",
                "status": "healthy",
                "message": f"Page '{page_name}' (ID: {page_id}) accessible, {len(scopes)} scopes granted",
            }

        error = result.get("error", "Unknown error")
        missing = result.get("missing_permissions", [])
        error_lower = error.lower()
        is_token_issue = (
            "token expired" in error_lower
            or "token invalid" in error_lower
            or "invalid token" in error_lower
            or "(code 190)" in error_lower
            or "(code 463)" in error_lower
        )
        if is_token_issue:
            return {
                "platform": "facebook",
                "status": "expired",
                "message": f"{error}. Run: python setup/setup_facebook_oauth.py",
            }
        if missing:
            return {
                "platform": "facebook",
                "status": "error",
                "message": f"Missing permissions: {missing}. Run: python setup/setup_facebook_oauth.py",
            }
        return {"platform": "facebook", "status": "error", "message": error}

    except requests.exceptions.RequestException as e:
        return {"platform": "facebook", "status": "error", "message": f"Network error: {e}"}
    except Exception as e:
        return {"platform": "facebook", "status": "error", "message": str(e)[:200]}


# ══════════════════════════════════════════════════════════════
# YOUTUBE OAUTH CHECK (formerly in BlackboxBrief)
# ══════════════════════════════════════════════════════════════


def check_youtube() -> dict:
    """Test YouTube OAuth connection.

    Uses google-auth + googleapiclient directly — no BlackboxBrief dependency.
    Falls back to a lightweight token-exchange check when google-auth is unavailable.
    """
    client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip()

    if not client_id:
        return {"platform": "youtube", "status": "missing", "message": "YOUTUBE_CLIENT_ID not set"}
    if not client_secret:
        return {
            "platform": "youtube",
            "status": "missing",
            "message": "YOUTUBE_CLIENT_SECRET not set",
        }
    if not refresh_token:
        return {
            "platform": "youtube",
            "status": "missing",
            "message": "YOUTUBE_REFRESH_TOKEN not set",
        }

    # Try google-auth path first (same approach as YouTubeClient in BlackboxBrief)
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            client_id=client_id,
            client_secret=client_secret,
            token_uri="https://oauth2.googleapis.com/token",
        )
        service = build("youtube", "v3", credentials=creds)
        # Fetch channel list to verify the token resolves to a real channel
        resp = service.channels().list(part="id,statistics", mine=True).execute()
        items = resp.get("items", [])
        if not items:
            return {
                "platform": "youtube",
                "status": "error",
                "message": "No channel found for this OAuth token",
            }

        channel_id = items[0]["id"]
        subs = int(items[0].get("statistics", {}).get("subscriberCount", 0))
        return {
            "platform": "youtube",
            "status": "healthy",
            "message": f"Channel {channel_id} connected, {subs} subscribers",
            "subscribers": subs,
        }

    except ImportError:
        logger.debug("google-auth not available — falling back to token-exchange check")

    # Fallback: raw token exchange to verify credentials are not revoked
    try:
        token_resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=20,
        )
        data = token_resp.json()
        if "access_token" in data:
            return {
                "platform": "youtube",
                "status": "healthy",
                "message": "YouTube OAuth token valid (token-exchange check — google-auth not installed)",
            }
        error = data.get("error_description", data.get("error", "Unknown error"))
        return {
            "platform": "youtube",
            "status": "error",
            "message": f"Token exchange failed: {error}",
        }

    except Exception as e:
        return {"platform": "youtube", "status": "error", "message": str(e)[:200]}


# ══════════════════════════════════════════════════════════════
# TWITTER CHECK (formerly in BlackboxBrief)
# ══════════════════════════════════════════════════════════════


def check_twitter() -> dict:
    """Test X/Twitter API connection.

    Uses tweepy directly — no BlackboxBrief dependency.
    403 on /users/me is expected on free-tier X API (treated as healthy-limited).
    """
    api_key = os.getenv("X_API_KEY", "").strip()
    api_secret = os.getenv("X_API_KEY_SECRET", "").strip()
    access_token = os.getenv("X_ACCESS_TOKEN", "").strip()
    access_token_secret = os.getenv("X_ACCESS_TOKEN_SECRET", "").strip()

    if not api_key:
        return {"platform": "twitter", "status": "missing", "message": "X_API_KEY not set"}

    try:
        import tweepy

        auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_token_secret)
        api = tweepy.API(auth)
        api.verify_credentials()
        return {"platform": "twitter", "status": "healthy", "message": "X API connected"}

    except ImportError:
        # tweepy not installed — do a lightweight bearer token check
        bearer = os.getenv("X_BEARER_TOKEN", "").strip()
        if not bearer:
            return {
                "platform": "twitter",
                "status": "missing",
                "message": "X_API_KEY set but tweepy not installed and X_BEARER_TOKEN not set",
            }
        try:
            resp = requests.get(
                "https://api.twitter.com/2/users/me",
                headers={"Authorization": f"Bearer {bearer}"},
                timeout=15,
            )
            if resp.status_code == 200:
                return {
                    "platform": "twitter",
                    "status": "healthy",
                    "message": "X API connected (bearer token)",
                }
            if resp.status_code == 403:
                return {
                    "platform": "twitter",
                    "status": "healthy-limited",
                    "message": "X API reachable (403 on /users/me is expected on free tier)",
                }
            if resp.status_code == 401:
                return {
                    "platform": "twitter",
                    "status": "expired",
                    "message": "Bearer token invalid or revoked",
                }
            return {"platform": "twitter", "status": "error", "message": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"platform": "twitter", "status": "error", "message": str(e)[:200]}

    except Exception as e:
        err_str = str(e)
        if "401" in err_str or "Unauthorized" in err_str:
            return {
                "platform": "twitter",
                "status": "expired",
                "message": "OAuth tokens expired/revoked — regenerate at developer.twitter.com",
            }
        if "403" in err_str or "Forbidden" in err_str:
            return {
                "platform": "twitter",
                "status": "healthy-limited",
                "message": "X API reachable (403 on /users/me is expected on free tier)",
            }
        return {"platform": "twitter", "status": "error", "message": err_str[:200]}


if __name__ == "__main__":
    main()
