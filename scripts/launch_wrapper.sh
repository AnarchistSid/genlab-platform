#!/bin/bash
# GenLab launch wrapper — loads env vars from .env before executing.
# Used by LaunchAgent plists to avoid hardcoding secrets in plist XML.
#
# Usage in plist ProgramArguments:
#   <string>/path/to/GenLab/scripts/launch_wrapper.sh</string>
#   <string>uv</string>
#   <string>run</string>
#   <string>...</string>

set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Load root .env (contains per-niche prefixed vars: CLUTCHWIRE_*, SPLICEREEL_*, etc.)
set -a
[ -f "$GENLAB_ROOT/.env" ] && source "$GENLAB_ROOT/.env"

# Load per-channel .env files — each channel's own credentials only.
# niche_credentials.py resolves {PREFIX}_{KEY} per niche, so loading all
# channel .env files is safe as long as each uses its own prefix.
for ch_env in \
  "$GENLAB_ROOT/BlackboxBrief/.env" \
  "$GENLAB_ROOT/CriticalRush/.env" \
  "$GENLAB_ROOT/ClutchWire/.env" \
  "$GENLAB_ROOT/SpliceReel/.env" \
  "$GENLAB_ROOT/FrameDrift/.env"; do
  [ -f "$ch_env" ] && source "$ch_env"
done
set +a

export GENLAB_PROJECT_ROOT="$GENLAB_ROOT"
export BACKLOG_CONFIG_PATH="$GENLAB_ROOT/genlab-core/config/lists_config.yaml"

# Wait for network connectivity before running (max 60s).
# Prevents DNS resolution failures when Mac is waking from sleep.
for i in $(seq 1 12); do
  curl -sf --max-time 3 https://www.google.com > /dev/null 2>&1 && break
  sleep 5
done

exec "$@"
