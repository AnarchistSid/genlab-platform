#!/usr/bin/env bash
# Install an ElevenLabs API key into the prod .env atomically.
#
# Usage:
#   scripts/install_elevenlabs_api_key.sh <KEY>
#   echo "<KEY>" | scripts/install_elevenlabs_api_key.sh -
#
# Why a script instead of `echo "ELEVENLABS_API_KEY=..." >> .env`?
#   - .env is permissions-sensitive (0600 owned by genlab); a naive
#     append from root leaves a root-owned line that systemd can't
#     read after EnvironmentFile parse.
#   - We need to REPLACE if the key already exists, not stack new
#     lines.
#   - The line should land alphabetically near the other tokens
#     so future operators can find it.
#
# All writes go through a temp file + atomic mv so the .env never
# half-written. Same pattern as install_threads_cache_entry_v2.py.
set -euo pipefail

KEY=""
if [[ "${1:-}" == "-" ]]; then
    read -r KEY
elif [[ -n "${1:-}" ]]; then
    KEY="$1"
fi

if [[ -z "${KEY:-}" ]]; then
    echo "usage: $0 <ELEVENLABS_API_KEY>  (or pipe via -)" >&2
    exit 1
fi

# ElevenLabs keys are 32-char alphanumeric/dashed; pass-through validation
# just rejects obviously-empty/garbage values.
if [[ ${#KEY} -lt 16 ]]; then
    echo "ERROR: key looks too short (${#KEY} chars) — refusing to install" >&2
    exit 2
fi

ENV_PATH="${GENLAB_ENV:-/opt/genlab/.env}"
if [[ ! -f "$ENV_PATH" ]]; then
    echo "ERROR: env file not found at $ENV_PATH" >&2
    exit 3
fi

TMP_FILE=$(mktemp "${ENV_PATH}.XXXXXX")
trap 'rm -f "$TMP_FILE"' EXIT

# Strip any existing ELEVENLABS_API_KEY line, then append the fresh one.
grep -v '^ELEVENLABS_API_KEY=' "$ENV_PATH" > "$TMP_FILE"
echo "ELEVENLABS_API_KEY=${KEY}" >> "$TMP_FILE"

# Preserve mode + owner from original
chmod --reference="$ENV_PATH" "$TMP_FILE"
chown --reference="$ENV_PATH" "$TMP_FILE"

mv -f "$TMP_FILE" "$ENV_PATH"
trap - EXIT

echo "Installed ELEVENLABS_API_KEY (length=${#KEY}) into $ENV_PATH"
echo "Next: restart consumers — typically the pipeline timers + dramatiq workers"
echo "  systemctl restart genlab-publisher.service"
echo "  systemctl restart genlab-engagement-worker.service"
