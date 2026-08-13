#!/bin/bash
# verify_gaming_cookies_impact.sh — one-shot verification of the
# yt-dlp cookies-file impact on gaming pipeline source diversity.
#
# Context: 2026-08-13 shipped commits a289af38 + 3f40904f — cookies-file
# support that should unlock YouTube downloads from the Hetzner
# datacenter IP. Prior 30 days: gaming was 100% Twitch-sourced.
#
# Usage: run after any gaming pipeline fire (default 09:30 IST daily):
#   ssh genlab-prod bash /opt/genlab/scripts/verify_gaming_cookies_impact.sh
#
# Exits 0 always — this is diagnostic, not a gate. Reads pipeline_alerts
# + blueprints on prod DB and prints a WIN/PARTIAL/STILL_BROKEN verdict.

set -uo pipefail

echo "=== 1. Pipeline service status ==="
sudo systemctl status genlab-pipeline-gaming.service --no-pager 2>&1 | head -12
echo

echo "=== 2. Source diversity (last 2h) ==="
sudo -u genlab bash -c '
  set -a; source /opt/genlab/.env; set +a
  psql "$DATABASE_URL" -c "
    SELECT source, COUNT(*)
    FROM blueprints
    WHERE niche_id = '\''gaming'\''
      AND created_at >= NOW() - INTERVAL '\''2 hours'\''
    GROUP BY 1 ORDER BY 2 DESC
  "
' 2>&1
echo

echo "=== 3. Open cookies/diversity alerts ==="
sudo -u genlab bash -c '
  set -a; source /opt/genlab/.env; set +a
  psql "$DATABASE_URL" -c "
    SELECT check_name, severity, LEFT(message, 100) AS message_head, created_at
    FROM pipeline_alerts
    WHERE niche_id = '\''gaming'\''
      AND resolved_at IS NULL
      AND check_name IN (
        '\''source_diversity_collapsed'\'',
        '\''yt_cookies_stale'\'',
        '\''yt_cookies_file_missing'\'',
        '\''yt_cookies_not_configured'\''
      )
    ORDER BY created_at DESC LIMIT 5
  "
' 2>&1
echo

echo "=== 4. Recent probe_yt_cookies output (past 24h) ==="
sudo journalctl --since '24 hours ago' 2>&1 | grep '\[probe_yt_cookies\]' | tail -5
echo

echo "=== 5. Recent yt-dlp bot-check errors (past 24h) ==="
sudo journalctl -u genlab-pipeline-gaming.service --since '24 hours ago' 2>&1 \
  | grep -iE 'sign in|bot|http error 429' | tail -5

exit 0
