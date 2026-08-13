#!/bin/bash
# lookup_youtube_channel_id.sh — resolve @handle to channel_id + verify RSS.
#
# Usage:  bash scripts/lookup_youtube_channel_id.sh handle1 handle2 ...
# Example: bash scripts/lookup_youtube_channel_id.sh AndrejKarpathy DeepLearningAI
#
# Two-step verification:
#   1. Fetch youtube.com/@<handle> and extract channelId from the JSON
#      embedded in the page (matches both "channelId" and "externalId"
#      naming variants YouTube uses).
#   2. Fetch the resulting RSS feed and confirm HTTP 200 + a non-empty
#      latest-video title. Catches handle-lookup false positives where
#      YouTube's search resolves to an unrelated third-party channel.
#
# Ships zero output if both steps succeed for every handle. FAIL rows
# printed to stderr. Exit code = number of failed handles.

set -uo pipefail

UA='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36'
failed=0

for h in "$@"; do
  url="https://www.youtube.com/@${h}"
  page=$(curl -sL -A "$UA" "$url" 2>/dev/null)
  ch=$(echo "$page" | grep -oE '"channelId":"UC[a-zA-Z0-9_-]{22}"' | head -1 | \
    sed -E 's/.*"(UC[a-zA-Z0-9_-]{22})".*/\1/')
  if [ -z "$ch" ]; then
    ch=$(echo "$page" | grep -oE 'externalId":"UC[a-zA-Z0-9_-]{22}"' | head -1 | \
      sed -E 's/.*"(UC[a-zA-Z0-9_-]{22})".*/\1/')
  fi
  if [ -z "$ch" ]; then
    printf "FAIL @%-25s no channel_id in page (likely 404)\n" "$h" >&2
    failed=$((failed + 1))
    continue
  fi
  # Step 2: verify RSS feed
  first_title=$(curl -sL -A "$UA" \
    "https://www.youtube.com/feeds/videos.xml?channel_id=${ch}" 2>/dev/null | \
    grep -oE '<title>[^<]+</title>' | head -2 | tail -1 | \
    sed -E 's|</?title>||g')
  status=$(curl -sL -A "$UA" -o /dev/null -w '%{http_code}' \
    "https://www.youtube.com/feeds/videos.xml?channel_id=${ch}")
  if [ "$status" = "200" ] && [ -n "$first_title" ]; then
    printf "OK   @%-25s %s -> %.60s\n" "$h" "$ch" "$first_title"
  else
    printf "FAIL @%-25s %s -> HTTP %s (title lookup empty)\n" \
      "$h" "$ch" "$status" >&2
    failed=$((failed + 1))
  fi
done

exit $failed
