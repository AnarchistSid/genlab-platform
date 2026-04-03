#!/bin/bash
# Launch cloudflared tunnel with token from macOS Keychain.
# Used by com.genlab.review-tunnel.plist.
TOKEN=$(security find-generic-password \
    -a "genlab-cloudflare-tunnel" \
    -s "CLOUDFLARE_TUNNEL_TOKEN" \
    -w 2>/dev/null)

if [ -z "$TOKEN" ]; then
    echo "ERROR: Could not retrieve tunnel token from Keychain" >&2
    exit 1
fi

exec /opt/homebrew/bin/cloudflared tunnel run --token "$TOKEN"
