"""Startup health probe: verify YT_DLP_COOKIES_FILE works against a
sentinel YouTube URL BEFORE the pipeline burns 20 min discovering
cookies are stale.

Invoked from `deploy/scripts/ensure_yt_dlp_environment.sh` at the start
of every gaming pipeline fire. Always exits 0 (never blocks the
pipeline); failure signal reaches the operator via `pipeline_alerts`.

Verification: fetches metadata for a public evergreen video
(`dQw4w9WgXcQ` — Rick Astley, 1.6B views, will not disappear). If
yt-dlp returns exit 0 AND stdout contains a plausible title, cookies
are healthy. If yt-dlp emits any bot-check marker in stderr, cookies
are stale — write a CRITICAL alert.

Fail-open shape (rule #19 sibling): if DATABASE_URL is missing,
pg_connect times out, or the sentinel is temporarily unreachable, we
log WARN and exit 0. The whole point is fail-fast on the KNOWN failure
mode (stale cookies), not to invent new failure surfaces.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("probe_yt_cookies")
logging.basicConfig(
    level=logging.INFO,
    format="[probe_yt_cookies] %(message)s",
)

SENTINEL_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
SENTINEL_TIMEOUT_S = 20

_BOT_CHECK_MARKERS = (
    "sign in to confirm you're not a bot",
    "confirm you're not a bot",
    "use --cookies-from-browser or --cookies",
    "http error 429",
)


def _is_bot_check(stderr: str) -> bool:
    low = (stderr or "").lower()
    return any(m in low for m in _BOT_CHECK_MARKERS)


def _emit_alert(check_name: str, severity: str, message: str, details: dict) -> None:
    """Best-effort insert into pipeline_alerts. Never raises."""
    try:
        from genlab_core.storage.tenant_context import pg_connect
    except Exception as exc:
        logger.warning("could not import pg_connect: %s", exc)
        return
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.warning("no DATABASE_URL; skipping alert emission")
        return
    try:
        with pg_connect(dsn, niche_id="gaming", connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM pipeline_alerts WHERE check_name = %s "
                    "AND niche_id = %s AND resolved_at IS NULL",
                    (check_name, "gaming"),
                )
                if cur.fetchone():
                    return
                cur.execute(
                    "INSERT INTO pipeline_alerts "
                    "(niche_id, check_name, severity, message, details) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ("gaming", check_name, severity, message, json.dumps(details)),
                )
                conn.commit()
                logger.warning("wrote %s alert: %s", check_name, message[:120])
    except Exception as exc:
        logger.warning("alert emission failed: %s", exc)


def main() -> int:
    cookies_path = os.environ.get("YT_DLP_COOKIES_FILE", "").strip()
    if not cookies_path:
        # No cookies configured — the pipeline runs in "no auth" mode,
        # which today means YT tiers fail-back to Twitch/Steam. This is
        # a real degradation but not the acute failure the probe is
        # meant to catch. Emit a low-severity "warning" alert once.
        _emit_alert(
            "yt_cookies_not_configured",
            "warning",
            "YT_DLP_COOKIES_FILE env var is unset. YouTube downloads "
            "will bot-block from datacenter IPs. See "
            "[[class-of-bug-datacenter-ip-bot-detection]] for the "
            "Playwright + SCP cookies setup path.",
            {"env_var": "YT_DLP_COOKIES_FILE"},
        )
        return 0

    if not Path(cookies_path).is_file():
        _emit_alert(
            "yt_cookies_file_missing",
            "critical",
            f"YT_DLP_COOKIES_FILE points at {cookies_path} but the file "
            f"does not exist. Re-run cookies export or fix the env var.",
            {"path": cookies_path},
        )
        return 0

    cmd = [
        "yt-dlp",
        SENTINEL_URL,
        "--cookies", cookies_path,
        "--skip-download",
        "--simulate",
        "--quiet",
        "--no-warnings",
        "--print", "%(title)s",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=SENTINEL_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        # Network hiccup, not necessarily cookies. Log + continue.
        logger.warning("sentinel probe timed out after %ds", SENTINEL_TIMEOUT_S)
        return 0
    except FileNotFoundError:
        logger.warning("yt-dlp binary not found on PATH; skipping probe")
        return 0

    if result.returncode == 0 and result.stdout.strip():
        logger.info("cookies healthy — sentinel returned '%s'", result.stdout.strip()[:80])
        return 0

    if _is_bot_check(result.stderr or ""):
        _emit_alert(
            "yt_cookies_stale",
            "critical",
            f"yt-dlp bot-check hit against sentinel {SENTINEL_URL} — "
            f"YT_DLP_COOKIES_FILE at {cookies_path} is stale or "
            f"invalid. Re-run the Playwright cookies export flow to "
            f"unblock gaming YT tiers.",
            {
                "cookies_path": cookies_path,
                "sentinel_url": SENTINEL_URL,
                "stderr_tail": (result.stderr or "")[-500:],
            },
        )
    else:
        # Unknown failure mode — sentinel was reachable, cookies aren't
        # obviously bot-blocked, but exit was non-zero. Log for triage.
        logger.warning(
            "sentinel probe exit=%d unexpectedly; stderr: %s",
            result.returncode, (result.stderr or "")[-200:],
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
