#!/usr/bin/env bash
# Keep YouTube session cookies fresh for yt-dlp.
#
# Background: pipelines use yt-dlp via _download_video() in
# genlab_core.media.download_top_videos. That function refreshes
# /opt/genlab/.youtube_cookies.txt on every successful request.
# When pipelines run less often than cookies decay, the next run
# hits YouTube's "Sign in to confirm you're not a bot" wall and
# produces zero blueprints.
#
# This script runs yt-dlp with --simulate against a known-stable
# public video to refresh session state without downloading any
# bytes. It's scheduled via genlab-yt-session-warm.timer.
#
# Keep yt-dlp args in sync with _download_video() in
# genlab-core/src/genlab_core/media/download_top_videos.py.
set -uo pipefail

PROJECT_ROOT="${GENLAB_PROJECT_ROOT:-/opt/genlab}"
COOKIES="${PROJECT_ROOT}/.youtube_cookies.txt"
SESSION="${PROJECT_ROOT}/.youtube_session.json"
YT_DLP="${PROJECT_ROOT}/.venv/bin/yt-dlp"

# "Me at the zoo" — first YouTube video, public, 19s, uploaded 2005.
# Zero chance of takedown, zero age/region restriction.
WARM_URL="${GENLAB_YT_WARM_URL:-https://www.youtube.com/watch?v=jNQXAC9IVRw}"

if [[ ! -x "$YT_DLP" ]]; then
    echo "yt-dlp not found at $YT_DLP"
    exit 0  # soft-fail: don't disrupt timers
fi

# Read visitor_data from session file if present
EXTRACTOR_ARGS="youtube:player_client=android,ios,tv,web"
if [[ -f "$SESSION" ]]; then
    VISITOR=$(python3 -c "
import json, sys
try:
    print(json.load(open('$SESSION')).get('visitor_data', ''))
except Exception:
    pass
" 2>/dev/null)
    if [[ -n "${VISITOR:-}" ]]; then
        EXTRACTOR_ARGS="${EXTRACTOR_ARGS};visitor_data=${VISITOR}"
    fi
fi

# WARP SOCKS proxy — autodetect like _download_video does
PROXY_FLAG=()
if [[ -n "${YT_DLP_PROXY:-}" ]]; then
    PROXY_FLAG=(--proxy "$YT_DLP_PROXY")
elif [[ -d /run/cloudflare-warp ]]; then
    PROXY_FLAG=(--proxy "socks5://127.0.0.1:40000")
fi

# Cookies file — touch only if non-empty with real entries
COOKIES_FLAG=()
if [[ -s "$COOKIES" ]] && grep -q '^[^#]' "$COOKIES" 2>/dev/null; then
    COOKIES_FLAG=(--cookies "$COOKIES")
fi

# curl_cffi impersonation — skip flag if package missing
IMPERSONATE_FLAG=()
if "$PROJECT_ROOT/.venv/bin/python" -c "import curl_cffi" 2>/dev/null; then
    IMPERSONATE_FLAG=(--impersonate chrome-136)
fi

START_TS=$(date -u +%s)
"$YT_DLP" \
    --simulate \
    --no-playlist \
    --socket-timeout 15 \
    --retries 1 \
    --quiet \
    --no-warnings \
    --extractor-args "$EXTRACTOR_ARGS" \
    --user-agent "com.google.android.youtube/19.09.37 (Linux; U; Android 14) gzip" \
    "${PROXY_FLAG[@]}" \
    "${COOKIES_FLAG[@]}" \
    "${IMPERSONATE_FLAG[@]}" \
    "$WARM_URL" 2>&1
EXIT=$?
ELAPSED=$(( $(date -u +%s) - START_TS ))

if [[ $EXIT -eq 0 ]]; then
    echo "yt-session-warm: ok (${ELAPSED}s)"
    exit 0
else
    echo "yt-session-warm: FAIL exit=$EXIT (${ELAPSED}s) — cookies may be stale"
    # Soft-fail so the timer doesn't enter failed state and disable itself.
    # The next pipeline run will surface the real error via its own SLO.
    exit 0
fi
