"""BB-specific platform health checks — canonical location in genlab-core.

Previously lived at Content Scraper/execution/check_token_health.py. Moved here
so the dashboard can import without requiring Content Scraper on sys.path.

Re-exports the shared functions from genlab_core.monitoring.token_health and
adds BB-specific checks (check_youtube, check_twitter, check_facebook) that
were formerly coupled to Content Scraper utilities.

The YouTube and Facebook checks are implemented inline using standard libraries
(requests, google-auth) so genlab-core has no dependency on Content Scraper.

Usage as library:
    from genlab_core.monitoring.check_token_health import (
        check_youtube, check_facebook, check_twitter,
    )

Usage as CLI (delegates to token_health.main):
    uv run python -m genlab_core.monitoring.check_token_health
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

from genlab_core.monitoring.token_health import (  # noqa: F401 — re-export
    check_anthropic,
    check_backlog,
    check_meta_token,
    check_openai,
    check_threads,
    check_tiktok,
    main,
    refresh_meta_token,
    run_all_checks,
    _run_native_platform_checks,
    _token_status_to_dict,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════
# FACEBOOK PAGE TOKEN CHECK (formerly in Content Scraper)
# ══════════════════════════════════════════════════════════════

# Facebook Graph API error codes
_ERR_INVALID_TOKEN = 190
_ERR_EXPIRED_TOKEN = 463

# Permissions required for video publishing
_REQUIRED_PUBLISH_PERMISSIONS = frozenset({
    "publish_video",
    "pages_manage_posts",
})


def _verify_page_token(page_id: str, access_token: str, api_version: str = "v21.0") -> dict[str, Any]:
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

    Uses Meta Graph API directly — no Content Scraper dependency.
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
# YOUTUBE OAUTH CHECK (formerly in Content Scraper)
# ══════════════════════════════════════════════════════════════


def check_youtube() -> dict:
    """Test YouTube OAuth connection.

    Uses google-auth + googleapiclient directly — no Content Scraper dependency.
    Falls back to a lightweight token-exchange check when google-auth is unavailable.
    """
    client_id = os.getenv("YOUTUBE_CLIENT_ID", "").strip()
    client_secret = os.getenv("YOUTUBE_CLIENT_SECRET", "").strip()
    refresh_token = os.getenv("YOUTUBE_REFRESH_TOKEN", "").strip()

    if not client_id:
        return {"platform": "youtube", "status": "missing", "message": "YOUTUBE_CLIENT_ID not set"}
    if not client_secret:
        return {"platform": "youtube", "status": "missing", "message": "YOUTUBE_CLIENT_SECRET not set"}
    if not refresh_token:
        return {"platform": "youtube", "status": "missing", "message": "YOUTUBE_REFRESH_TOKEN not set"}

    # Try google-auth path first (same approach as YouTubeClient in Content Scraper)
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
            return {"platform": "youtube", "status": "error", "message": "No channel found for this OAuth token"}

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
        return {"platform": "youtube", "status": "error", "message": f"Token exchange failed: {error}"}

    except Exception as e:
        return {"platform": "youtube", "status": "error", "message": str(e)[:200]}


# ══════════════════════════════════════════════════════════════
# TWITTER CHECK (formerly in Content Scraper)
# ══════════════════════════════════════════════════════════════


def check_twitter() -> dict:
    """Test X/Twitter API connection.

    Uses tweepy directly — no Content Scraper dependency.
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
                return {"platform": "twitter", "status": "healthy", "message": "X API connected (bearer token)"}
            if resp.status_code == 403:
                return {
                    "platform": "twitter",
                    "status": "healthy-limited",
                    "message": "X API reachable (403 on /users/me is expected on free tier)",
                }
            if resp.status_code == 401:
                return {"platform": "twitter", "status": "expired", "message": "Bearer token invalid or revoked"}
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
