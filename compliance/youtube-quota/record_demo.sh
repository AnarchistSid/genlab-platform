#!/usr/bin/env bash
# record_demo.sh — one-shot terminal orchestrator for the YouTube quota-review
# compliance recording.
#
# Runs the four terminal shots the reviewer wants to see — client location,
# asset, videos.insert exchange, end-URL — in order, paced for a legible OBS
# recording. Replaces the previous shot-list of 7 hand-typed commands.
#
# Usage
#   ./record_demo.sh --check     # dry-run: validate env + creds, NO upload
#   ./record_demo.sh             # real take: performs one videos.insert
#
# Required env
#   YT_COMPLIANCE_ASSET   absolute path to a readable MP4 to upload
#   YT_COMPLIANCE_NICHE   one of {ai_creators, gaming, sports, movies, anime}
#
# Optional env
#   PACE                  seconds between sections (default 2)
#   YT_COMPLIANCE_TITLE   video title (default "GenLab API compliance test")
#   PYTHON                python executable (default: python3)
#
# Exit codes
#   0    all shots completed (upload succeeded, or --check passed)
#   2    missing/bad env var (fails BEFORE any recording-valuable output)
#   3    upload failed (publish() returned success=False)
#   4    unexpected exception in the Python child
#   5    --check failed (auth/asset unreachable)

set -euo pipefail

# -----------------------------------------------------------------------------
# arg parsing (deliberately minimal — the Python child handles the real args)
# -----------------------------------------------------------------------------
CHECK_MODE=0
case "${1:-}" in
    --check) CHECK_MODE=1 ;;
    "")      CHECK_MODE=0 ;;
    -h|--help)
        sed -n '2,30p' "$0"
        exit 0
        ;;
    *)
        echo "ERROR: unknown argument '$1'. Use --check or no args." >&2
        exit 2
        ;;
esac

# -----------------------------------------------------------------------------
# env validation — fail BEFORE any recording-valuable output
# -----------------------------------------------------------------------------
_missing=0
if [[ -z "${YT_COMPLIANCE_ASSET:-}" ]]; then
    echo "ERROR: YT_COMPLIANCE_ASSET must be set to an absolute MP4 path" >&2
    _missing=1
fi
if [[ -z "${YT_COMPLIANCE_NICHE:-}" ]]; then
    echo "ERROR: YT_COMPLIANCE_NICHE must be set (ai_creators|gaming|sports|movies|anime)" >&2
    _missing=1
fi
[[ $_missing -eq 1 ]] && exit 2
PACE="${PACE:-2}"
PYTHON="${PYTHON:-/opt/genlab/.venv/bin/python}"
TITLE="${YT_COMPLIANCE_TITLE:-GenLab API compliance test}"

# Case-validate the niche so the error surface is bash-side, not a stack trace
# out of argparse mid-recording.
case "$YT_COMPLIANCE_NICHE" in
    ai_creators|gaming|sports|movies|anime) : ;;
    *)
        echo "ERROR: YT_COMPLIANCE_NICHE must be one of ai_creators|gaming|sports|movies|anime, got '$YT_COMPLIANCE_NICHE'" >&2
        exit 2
        ;;
esac

if [[ ! -r "$YT_COMPLIANCE_ASSET" ]]; then
    echo "ERROR: YT_COMPLIANCE_ASSET is not a readable file: $YT_COMPLIANCE_ASSET" >&2
    exit 2
fi

# Locate the Python entrypoint relative to this script so `cd` behaviour
# doesn't matter for the operator.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_ENTRY="$SCRIPT_DIR/run_compliance_upload.py"
if [[ ! -f "$PY_ENTRY" ]]; then
    echo "ERROR: cannot find $PY_ENTRY next to record_demo.sh" >&2
    exit 2
fi

# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------
_bar() { printf '%*s\n' 78 | tr ' ' '='; }
section() {
    echo
    _bar
    echo "== $1"
    _bar
}
pause() { sleep "$PACE"; }

# -----------------------------------------------------------------------------
# SHOT 1 — client location (Hetzner nbg1)
# -----------------------------------------------------------------------------
section "1. CLIENT LOCATION — this terminal is running on our production host"
echo "\$ hostname"
hostname
echo
echo "\$ curl -s ipinfo.io"
# All probes below are wrapped so a network hiccup or missing tool never kills
# the recording mid-take (set -o pipefail would otherwise propagate).
if command -v jq >/dev/null 2>&1; then
    echo "  (piped through jq for the ip/org/region/country fields)"
    { curl -s --max-time 5 ipinfo.io 2>/dev/null || echo '{}'; } \
        | { jq '{ip, org, region, country}' 2>/dev/null || echo '(ipinfo.io unreachable or malformed — retake this shot)'; }
else
    echo "  (jq not installed — showing the raw org field only)"
    ORG_LINE="$(curl -s --max-time 5 ipinfo.io/org 2>/dev/null || echo 'ipinfo.io unreachable')"
    echo "org: $ORG_LINE"
fi
pause

# -----------------------------------------------------------------------------
# SHOT 2 — the real asset on disk
# -----------------------------------------------------------------------------
section "2. THE VIDEO WE'RE ABOUT TO UPLOAD — a real MP4 on disk"
echo "\$ ls -la $YT_COMPLIANCE_ASSET"
ls -la "$YT_COMPLIANCE_ASSET"
echo
if command -v ffprobe >/dev/null 2>&1; then
    echo "\$ ffprobe -v error -show_entries format=duration,size -show_entries stream=width,height \"$YT_COMPLIANCE_ASSET\""
    # Wrapped: an invalid container prints a warning but must not kill the recording.
    { ffprobe -v error \
        -show_entries format=duration,size \
        -show_entries stream=width,height \
        "$YT_COMPLIANCE_ASSET" 2>&1 || echo '(ffprobe returned non-zero — file may not be a valid media container; ls output above still shows the real byte size)'; } \
        | head -20
else
    echo "(ffprobe not installed — skipping dimension probe; ls output above shows size)"
fi
pause

# -----------------------------------------------------------------------------
# SHOT 3 — the API call (or --check dry-run)
# -----------------------------------------------------------------------------
if [[ $CHECK_MODE -eq 1 ]]; then
    section "3. DRY-RUN — validate credentials WITHOUT uploading (--check)"
    echo "\$ $PYTHON $PY_ENTRY --niche $YT_COMPLIANCE_NICHE --asset $YT_COMPLIANCE_ASSET --check"
    pause
    # Exit code from Python propagates directly (set -e).
    "$PYTHON" "$PY_ENTRY" \
        --niche "$YT_COMPLIANCE_NICHE" \
        --asset "$YT_COMPLIANCE_ASSET" \
        --check
    echo
    echo "════════════════════════════════════════════════════════════════════════════"
    echo "  ✓ --check PASSED. You can now run without --check for the real take."
    echo "════════════════════════════════════════════════════════════════════════════"
    exit 0
fi

section "3. THE API CALL — same YouTubeClient.publish() the daily pipeline uses"
echo "\$ $PYTHON $PY_ENTRY --niche $YT_COMPLIANCE_NICHE --asset $YT_COMPLIANCE_ASSET"
echo "  (this invokes videos.insert with uploadType=resumable against"
echo "   googleapis.com/upload/youtube/v3/videos — the same call the daily"
echo "   publisher uses. Response body prints in full at the end.)"
pause
pause  # extra beat before the API traffic starts

# Capture stdout to a temp file so we can (a) print it live via tee and
# (b) parse the final URL for the end-result echo without a second run.
TMP_OUT="$(mktemp -t genlab-compliance-out.XXXXXX)"
trap 'rm -f "$TMP_OUT"' EXIT

if "$PYTHON" "$PY_ENTRY" \
        --niche "$YT_COMPLIANCE_NICHE" \
        --asset "$YT_COMPLIANCE_ASSET" \
        --title "$TITLE" 2>&1 | tee "$TMP_OUT"; then
    :
else
    PY_EXIT=${PIPESTATUS[0]}
    echo
    echo "ERROR: run_compliance_upload.py exited $PY_EXIT — see output above." >&2
    exit "$PY_EXIT"
fi

# Pause so the reviewer's eye can rest on the JSON response before the URL banner.
pause
pause

# -----------------------------------------------------------------------------
# SHOT 4 — the end URL, big and clear
# -----------------------------------------------------------------------------
# Extract the /shorts/<id> URL that the Python child printed on its
# "Video is live at:" line. Fall back to a generic instruction if the format
# ever drifts.
SHORTS_URL="$(grep -oE 'https://youtube\.com/shorts/[A-Za-z0-9_-]+' "$TMP_OUT" | head -1 || true)"

section "4. END RESULT — the video is now live on YouTube"
if [[ -n "$SHORTS_URL" ]]; then
    echo
    echo "    ┌────────────────────────────────────────────────────────────────┐"
    printf "    │  %-60s│\n" "$SHORTS_URL"
    echo "    └────────────────────────────────────────────────────────────────┘"
    echo
    echo "→ NEXT: switch to the browser tab and open the URL above."
    echo "  The video plays; URL bar shows the same video ID the API returned;"
    echo "  the 'Unlisted' badge matches our privacyStatus request."
else
    echo
    echo "(could not parse a /shorts/ URL from the upload output — see the"
    echo " RESPONSE block above for the videoId, then open"
    echo " https://youtube.com/shorts/<id> manually in the browser.)"
fi
echo
