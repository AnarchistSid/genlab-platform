#!/usr/bin/env bash
# merge-env.sh — Merge all GenLab .env files into a single flat file for cloud deployment.
#
# Usage: ./deploy/scripts/merge-env.sh > /tmp/genlab-cloud.env
#        scp /tmp/genlab-cloud.env root@46.224.237.56:/opt/genlab/.env
#
# This replaces the multi-file loading pattern in launch_wrapper.sh with a single
# flat file that systemd's EnvironmentFile= can consume directly.

set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ENV_FILES=(
    "$GENLAB_ROOT/.env"
    "$GENLAB_ROOT/BlackboxBrief/.env"
    "$GENLAB_ROOT/CriticalRush/.env"
    "$GENLAB_ROOT/ClutchWire/.env"
    "$GENLAB_ROOT/SpliceReel/.env"
    "$GENLAB_ROOT/FrameDrift/.env"
)

echo "# GenLab Cloud Environment — merged $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "# Source files: ${ENV_FILES[*]}"
echo ""

declare -A SEEN

for envfile in "${ENV_FILES[@]}"; do
    if [[ ! -f "$envfile" ]]; then
        echo "# SKIPPED (not found): $envfile"
        continue
    fi
    echo "# --- $(basename "$(dirname "$envfile")")/$(basename "$envfile") ---"
    while IFS= read -r line; do
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        key="${line%%=*}"
        key=$(echo "$key" | xargs)
        [[ -z "$key" ]] && continue
        # Later files override earlier ones (per-niche overrides root)
        if [[ -n "${SEEN[$key]+x}" ]]; then
            echo "# OVERRIDE: $key (was from ${SEEN[$key]})"
        fi
        SEEN[$key]="$envfile"
        echo "$line"
    done < "$envfile"
    echo ""
done

# Append cloud-specific overrides
echo "# --- Cloud-specific overrides ---"
echo "DATABASE_URL=postgresql://genlab:\${POSTGRES_PASSWORD}@127.0.0.1:5432/genlab"
echo "GENLAB_PROJECT_ROOT=/opt/genlab"
echo "BACKLOG_CONFIG_PATH=/opt/genlab/genlab-core/config/lists_config.yaml"
echo "GENLAB_USE_POSTGRES=true"
echo "REDIS_HOST=127.0.0.1"
echo "REDIS_PORT=6379"
