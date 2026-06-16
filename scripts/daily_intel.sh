#!/usr/bin/env bash
# Unified daily_intel.sh — runs any channel's pipeline
# Called from channel runbooks/ via symlink or from LaunchAgent
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CHANNEL_ROOT="$(dirname "$SCRIPT_DIR")"
CHANNEL_NAME="$(basename "$CHANNEL_ROOT")"
GENLAB_ROOT="$(dirname "$CHANNEL_ROOT")"

# Resolve the uv binary. Prefer PATH; fall back to canonical install
# locations. The original `UV="${HOME}/.local/bin/uv"` was a single
# hardcoded path that broke when systemd ran the script with
# Environment=HOME=/opt/genlab (the genlab user's home, which has no
# .local/bin). 2026-06-14 deploy-pipeline-gap recovery surfaced this
# when prod's systemd unit modification flipped the latent bug from
# "works" to "broken" — see session-2026-06-14-deploy-pipeline-gap.
UV="$(command -v uv 2>/dev/null || true)"
if [[ -z "$UV" ]]; then
    for candidate in "${HOME}/.local/bin/uv" /usr/local/bin/uv /root/.local/bin/uv "${GENLAB_ROOT}/.local/bin/uv"; do
        if [[ -x "$candidate" ]]; then UV="$candidate"; break; fi
    done
fi
if [[ -z "$UV" ]]; then
    echo "FATAL: uv binary not found on PATH or canonical install locations" >&2
    echo "  PATH=$PATH" >&2
    echo "  Tried: \${HOME}/.local/bin/uv, /usr/local/bin/uv, /root/.local/bin/uv, \${GENLAB_ROOT}/.local/bin/uv" >&2
    exit 127
fi

# Derive package name from channel directory
PACKAGE_MAP="BlackboxBrief:blackbox-brief CriticalRush:criticalrush ClutchWire:clutchwire SpliceReel:splicereel FrameDrift:framedrift"
PACKAGE=""
for entry in $PACKAGE_MAP; do
    key="${entry%%:*}"
    val="${entry##*:}"
    [ "$CHANNEL_NAME" = "$key" ] && PACKAGE="$val" && break
done
[ -z "$PACKAGE" ] && echo "Unknown channel: $CHANNEL_NAME" && exit 1

# Source credentials: root → BB (shared) → channel
for envfile in "${GENLAB_ROOT}/.env" "${GENLAB_ROOT}/BlackboxBrief/.env" "${CHANNEL_ROOT}/.env"; do
    [ -f "$envfile" ] && { set -a; source "$envfile"; set +a; }
done

# Log setup
LOG_DIR="${GENLAB_ROOT}/.tmp/logs/${PACKAGE}"
mkdir -p "$LOG_DIR"
RUN_ID="${PACKAGE}_$(date -u +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/${RUN_ID}.log"

echo "=== ${CHANNEL_NAME} daily_intel — ${RUN_ID} ===" | tee "$LOG_FILE"
echo "Started: $(date -u '+%Y-%m-%d %H:%M:%S UTC')" | tee -a "$LOG_FILE"

cd "$CHANNEL_ROOT"
"$UV" run --frozen --package "$PACKAGE" python run_pipeline.py "$@" 2>&1 | tee -a "$LOG_FILE"
EXIT_CODE=${PIPESTATUS[0]}

echo "Finished: $(date -u '+%Y-%m-%d %H:%M:%S UTC') | exit=$EXIT_CODE" | tee -a "$LOG_FILE"
exit $EXIT_CODE
