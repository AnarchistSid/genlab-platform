"""Generate the shared YouTube Analytics refresh token via OAuth.

Prerequisites
-------------
1. Pick ONE Google account to be the "super-account" for analytics reads.
2. Add that account as a *Manager* on each of the 5 YouTube channels via
   YouTube Studio → Settings → Permissions. This grants read access to
   analytics without granting any publish/edit rights — the per-niche
   write tokens stay isolated.
3. In Google Cloud Console for the OAuth app behind ``YOUTUBE_CLIENT_ID``:
     * APIs & Services → Library → enable "YouTube Analytics API".
     * APIs & Services → OAuth consent screen → add scopes:
         ``https://www.googleapis.com/auth/yt-analytics.readonly``
         ``https://www.googleapis.com/auth/youtube.force-ssl``
4. Authorize ``http://localhost:8765/`` as a redirect URI on the OAuth
   client (APIs & Services → Credentials → click the client → add URI).

Usage
-----
    cd GenLab/genlab-core
    uv run python scripts/generate_yt_analytics_token.py

The script opens your browser to Google's consent screen. Sign in as the
super-account, approve both scopes, and you get redirected to a local
page that prints the refresh token. Paste it into your .env as:

    YOUTUBE_ANALYTICS_REFRESH_TOKEN=<the_token>

(Optional: ``YOUTUBE_ANALYTICS_CLIENT_ID`` / ``YOUTUBE_ANALYTICS_CLIENT_SECRET``
if using a different OAuth app than YOUTUBE_CLIENT_ID; otherwise the
script reuses the existing YouTube client.)

After updating .env, scp it to Hetzner and the next metric_collector
tick will start filling avg_view_duration / subscriber_gained from real
analytics data.
"""
from __future__ import annotations

import http.server
import os
import socketserver
import sys
import threading
import urllib.parse
import webbrowser

import requests

_CLIENT_ID = (
    os.environ.get("YOUTUBE_ANALYTICS_CLIENT_ID")
    or os.environ.get("YOUTUBE_CLIENT_ID", "")
).strip()
_CLIENT_SECRET = (
    os.environ.get("YOUTUBE_ANALYTICS_CLIENT_SECRET")
    or os.environ.get("YOUTUBE_CLIENT_SECRET", "")
).strip()
_REDIRECT_URI = "http://localhost:8765/"
_SCOPES = [
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def main() -> int:
    if not _CLIENT_ID or not _CLIENT_SECRET:
        print("ERROR: set YOUTUBE_CLIENT_ID + YOUTUBE_CLIENT_SECRET in env first")
        return 1

    auth_params = {
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(_SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # force refresh_token issuance
    }
    auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urllib.parse.urlencode(auth_params)
    )

    print("Opening browser to:", auth_url)
    print()
    print("Sign in as your analytics SUPER-ACCOUNT (the one that's a manager")
    print("on all 5 YouTube channels). Approve both scopes.")
    print()

    received: dict[str, str] = {}

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.urlparse(self.path).query
            params = urllib.parse.parse_qs(qs)
            if "code" in params:
                received["code"] = params["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<h1>Got it.</h1><p>You can close this tab.</p>"
                )
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *_a, **_k):  # silence default logging
            pass

    server = socketserver.TCPServer(("", 8765), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    webbrowser.open(auth_url)

    print("Waiting for redirect to http://localhost:8765/...")
    while "code" not in received:
        pass
    server.shutdown()

    code = received["code"]
    print()
    print("Got auth code, exchanging for refresh_token...")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
            "code": code,
            "redirect_uri": _REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if token_resp.status_code != 200:
        print("ERROR:", token_resp.status_code, token_resp.text)
        return 2

    payload = token_resp.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        print("ERROR: no refresh_token in response. Full payload:")
        print(payload)
        print("If you've already authorized this app, revoke access at")
        print("https://myaccount.google.com/permissions and re-run.")
        return 3

    print()
    print("SUCCESS. Add this to your .env file:")
    print()
    print(f"YOUTUBE_ANALYTICS_REFRESH_TOKEN={refresh_token}")
    print()
    print("Scopes granted:", payload.get("scope", "<unknown>"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
