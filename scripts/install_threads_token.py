"""Install + validate a freshly-generated Threads long-lived token.

Usage:
    AGENT_ROOT=/opt/genlab/BlackboxBrief python scripts/install_threads_token.py NEW_TOKEN

What it does:
  1. Validates the token via GET /me?fields=id,username (proves it works).
  2. Optionally exchanges a short-lived (1h) token for a long-lived (60d)
     token via /access_token?grant_type=th_exchange_token. Detection is
     heuristic — if /me succeeds but the token looks short (starts with
     "TH..." but length < 100), we attempt the exchange.
  3. Updates /opt/genlab/.env (or path from THREADS_ENV_PATH) atomically:
       THREADS_ACCESS_TOKEN=<new>
       THREADS_TOKEN_ISSUED_AT=<now ISO 8601>
  4. Restarts services that read the token at startup
     (genlab-engagement-poller, genlab-engagement-worker, genlab-publisher,
     genlab-dashboard).

Why ISO 8601 not Unix timestamp:
  Older entries used Unix epoch ('1773047145') but threads.py's
  _token_needs_refresh() parsed only ISO 8601 — silently broken since
  commit ?. Auto-refresh at day-50 then never fired and the token
  expired untouched. Commit b5ed1d9 made the parser accept both formats,
  but new tokens are written in ISO for clarity.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

ENV_PATH = Path(os.environ.get("THREADS_ENV_PATH", "/opt/genlab/.env"))
RESTART_SERVICES = [
    "genlab-engagement-poller.service",
    "genlab-engagement-worker.service",
    "genlab-publisher.service",
    "genlab-dashboard.service",
]


def validate_token(token: str) -> dict:
    """Hit /me to confirm the token works. Returns the JSON or raises."""
    resp = requests.get(
        "https://graph.threads.net/v1.0/me",
        params={"fields": "id,username,threads_profile_picture_url", "access_token": token},
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token validation failed (HTTP {resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"/me response missing 'id': {data}")
    return data


def looks_short_lived(token: str) -> bool:
    """Heuristic: short-lived Threads tokens are ~80-120 chars; long-lived
    are ~180-220. Not authoritative but a useful hint."""
    return len(token) < 150


def exchange_for_long_lived(short_token: str, app_secret: str) -> str:
    """th_exchange_token: short-lived → long-lived (60-day)."""
    resp = requests.get(
        "https://graph.threads.net/access_token",
        params={
            "grant_type": "th_exchange_token",
            "client_secret": app_secret,
            "access_token": short_token,
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"th_exchange_token failed (HTTP {resp.status_code}): {resp.text[:300]}")
    long_token = resp.json().get("access_token", "")
    if not long_token:
        raise RuntimeError(f"Exchange returned no access_token: {resp.json()}")
    return long_token


def update_env(token: str) -> None:
    """Rewrite THREADS_ACCESS_TOKEN + THREADS_TOKEN_ISSUED_AT atomically.

    Preserves the original file's ownership + permissions so non-root
    services (engagement-poller, dashboard) can still read .env after
    the rewrite. Bug 2026-05-20: script ran as root and chmod 0o600
    locked out the genlab user, breaking the dashboard.
    """
    issued_iso = datetime.now(UTC).isoformat()

    if not ENV_PATH.exists():
        raise RuntimeError(f".env not found at {ENV_PATH}")

    # Capture original ownership + permissions BEFORE rewriting
    orig_stat = ENV_PATH.stat()
    orig_uid = orig_stat.st_uid
    orig_gid = orig_stat.st_gid
    orig_mode = orig_stat.st_mode & 0o777

    lines = ENV_PATH.read_text().splitlines()
    new_lines = []
    found_token = False
    found_issued = False

    for line in lines:
        if line.startswith("THREADS_ACCESS_TOKEN="):
            new_lines.append(f"THREADS_ACCESS_TOKEN={token}")
            found_token = True
        elif line.startswith("THREADS_TOKEN_ISSUED_AT="):
            new_lines.append(f"THREADS_TOKEN_ISSUED_AT={issued_iso}")
            found_issued = True
        else:
            new_lines.append(line)

    if not found_token:
        new_lines.append(f"THREADS_ACCESS_TOKEN={token}")
    if not found_issued:
        new_lines.append(f"THREADS_TOKEN_ISSUED_AT={issued_iso}")

    tmp_path = ENV_PATH.with_suffix(".tmp")
    tmp_path.write_text("\n".join(new_lines) + "\n")
    # Restore original mode + ownership before atomic rename so we don't
    # accidentally lock out service users.
    tmp_path.chmod(orig_mode)
    try:
        os.chown(tmp_path, orig_uid, orig_gid)
    except PermissionError:
        # Non-root can't chown; the rename will keep the new file owned
        # by the caller. Log it so the user knows to fix manually if so.
        print(
            f"  ⚠ Could not chown to uid={orig_uid} gid={orig_gid} — "
            f"verify {ENV_PATH} is readable by service users.",
        )
    tmp_path.replace(ENV_PATH)
    print(f"  ✓ Updated {ENV_PATH} (mode {oct(orig_mode)}, uid={orig_uid})")
    print(f"  ✓ THREADS_TOKEN_ISSUED_AT={issued_iso}")


def restart_services() -> None:
    for svc in RESTART_SERVICES:
        try:
            subprocess.run(
                ["systemctl", "restart", svc],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            print(f"  ✓ Restarted {svc}")
        except subprocess.CalledProcessError as exc:
            print(f"  ✗ Failed to restart {svc}: {exc.stderr[:200]}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"  ✗ Timeout restarting {svc}", file=sys.stderr)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    raw_token = sys.argv[1].strip()
    if not raw_token:
        print("Empty token argument", file=sys.stderr)
        return 2

    print(f"Token length: {len(raw_token)} chars")

    # 1) Validate
    print("1) Validating token via /me ...")
    try:
        me = validate_token(raw_token)
    except Exception as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        return 1
    print(f"  ✓ id={me['id']} username={me.get('username', '?')}")

    # 2) Exchange if short-lived
    final_token = raw_token
    if looks_short_lived(raw_token):
        app_secret = os.environ.get("THREADS_APP_SECRET", "").strip()
        if not app_secret:
            print(
                "  ⚠ Token looks short-lived but THREADS_APP_SECRET not set — "
                "skipping exchange. Re-run with --no-exchange to suppress this hint.",
                file=sys.stderr,
            )
        else:
            print("2) Exchanging short-lived → long-lived ...")
            try:
                final_token = exchange_for_long_lived(raw_token, app_secret)
                print(f"  ✓ Got long-lived token (length: {len(final_token)})")
                # Re-validate the new token
                me2 = validate_token(final_token)
                print(f"  ✓ Long-lived token validated: id={me2['id']}")
            except Exception as exc:
                print(f"  ✗ Exchange failed, falling back to input token: {exc}", file=sys.stderr)
                final_token = raw_token
    else:
        print("2) Token appears long-lived already — skipping exchange")

    # 3) Write .env
    print("3) Updating /opt/genlab/.env ...")
    update_env(final_token)

    # 4) Restart services
    print("4) Restarting services that cache the token at startup ...")
    restart_services()

    print("\nDone. Next pipeline / publisher / engagement run will pick up the new token.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
