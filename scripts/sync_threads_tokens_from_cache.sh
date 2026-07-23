#!/bin/bash
# Sync fresh Threads tokens from JSON file to per-niche env vars.
# Fixes 2026-07-23 "TOKEN_EXPIRED" alerts for gaming + ai_creators.
# Root cause: publisher uses auto-refreshed .threads_tokens.json (fresh),
# poller uses env vars (stale). Split adoption pattern.
set -euo pipefail

TOKENS_FILE="/opt/genlab/.threads_tokens.json"
ENV_FILE="/opt/genlab/.env"
BACKUP="${ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)_threads_sync"

# Backup
cp "$ENV_FILE" "$BACKUP"
echo "backup: $BACKUP"

# Update each niche → env var pair
for pair in "ai_creators:BLACKBOXBRIEF" "gaming:CRITICALRUSH" "sports:CLUTCHWIRE" "movies:SPLICEREEL" "anime:FRAMEDRIFT"; do
    niche="${pair%%:*}"
    prefix="${pair##*:}"
    var="${prefix}_THREADS_ACCESS_TOKEN"

    token=$(python3 -c "
import json
d = json.load(open('$TOKENS_FILE'))
print(d.get('$niche', {}).get('access_token', ''))
")
    if [ -z "$token" ]; then
        echo "  ! $niche: no token in JSON file, skipping"
        continue
    fi

    # Replace or append the env var
    if grep -q "^$var=" "$ENV_FILE"; then
        # Use | as delimiter to avoid conflict with = or / in tokens
        sed -i "s|^$var=.*|$var=$token|" "$ENV_FILE"
        echo "  ✓ $niche → $var (updated in place)"
    else
        echo "$var=$token" >> "$ENV_FILE"
        echo "  ✓ $niche → $var (appended)"
    fi
done

echo
echo "verify (first 20 chars each):"
grep -E "^(BLACKBOXBRIEF|CRITICALRUSH|CLUTCHWIRE|SPLICEREEL|FRAMEDRIFT)_THREADS_ACCESS_TOKEN=" "$ENV_FILE" | \
    sed 's|=\(.\{20\}\).*|=\1...|'
