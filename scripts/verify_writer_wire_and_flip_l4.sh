#!/usr/bin/env bash
# verify_writer_wire_and_flip_l4.sh — post-fire verification for W1 fix.
#
# Runs on prod, checks the last 6h of publishes for attribution markers.
# If ≥80% of publishes carry a credit marker, offers to flip
# GENLAB_ATTRIBUTION_LAYER4_BLOCK=1 in /opt/genlab/.env.
#
# Timing: run at 12:35 IST (06:35 UTC + 30 min buffer for publisher run
# + Meta insight callback). Publisher fires at 12:00 IST → 6h window
# gives us today's 5-15 fresh publishes + yesterday's tail.
#
# Usage:
#   ./scripts/verify_writer_wire_and_flip_l4.sh          # verify only, prompt
#   ./scripts/verify_writer_wire_and_flip_l4.sh --yes    # verify + auto-flip if healthy
#   ./scripts/verify_writer_wire_and_flip_l4.sh --dry    # verify + print what WOULD change
#
# Exit codes:
#   0 — wire healthy AND flag flipped (or --dry succeeded)
#   1 — wire NOT healthy — flag NOT flipped, operator must trace
#   2 — script error (DB unreachable, missing env, etc.)
#
# Context: PR #779 (2026-07-13) fixed the writer wire that had been
# broken for weeks; Layer 5 tightening (PR #776) exposed the failure
# the same day. Layer 4 BLOCK was flipped ON then OFF pending this
# verification. See [[session-2026-07-13-audit-followup-writer-wire]].

set -euo pipefail

MODE="prompt"
if [[ "${1:-}" == "--yes" ]]; then
    MODE="auto"
elif [[ "${1:-}" == "--dry" ]]; then
    MODE="dry"
elif [[ -n "${1:-}" ]]; then
    echo "Usage: $0 [--yes|--dry]" >&2
    exit 2
fi

GENLAB=/opt/genlab
VENV=$GENLAB/.venv/bin/python
ENV_FILE=$GENLAB/.env
FLAG_LINE="GENLAB_ATTRIBUTION_LAYER4_BLOCK=1"
FLAG_KEY="^GENLAB_ATTRIBUTION_LAYER4_BLOCK="

if [ ! -f "$ENV_FILE" ]; then
    echo "[fatal] $ENV_FILE not found — are we on the prod box?" >&2
    exit 2
fi

# 2026-07-14 fix: the Python subprocess spawned via `sudo -u genlab`
# doesn't inherit our shell environment, so DATABASE_URL was empty
# and psycopg's fallback DSN (``dbname=genlab``) tried the local Unix
# socket at `/var/run/postgresql/` which doesn't exist on this box —
# script exited 2 with "socket file not found". Extract DATABASE_URL
# from the env file + pass it via sudo -E preserving-env so the
# subprocess connects to the right host:port.
DATABASE_URL=$(grep -E "^DATABASE_URL=" "$ENV_FILE" | head -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'\$//")
if [ -z "$DATABASE_URL" ]; then
    echo "[fatal] DATABASE_URL not found in $ENV_FILE" >&2
    exit 2
fi
export DATABASE_URL

echo "[verify] Querying Layer 5 metric for last 6h of publishes..."
# Same SQL shape as check #8 in post_deploy_verify.sh + Layer 5
# endpoint. Duplicated inline so this script has zero import deps.
RESULT=$(sudo -u genlab -E $VENV - <<'PY' 2>&1 || echo "PY_ERROR"
import os, psycopg
dsn = os.environ.get("DATABASE_URL") or "dbname=genlab"
try:
    with psycopg.connect(dsn) as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*), COUNT(*) FILTER (
                    WHERE COALESCE(caption,'') LIKE '%🎬 Original:%'
                       OR COALESCE(caption,'') LIKE '%Footage:%'
                       OR COALESCE(extra->>'facebook_content','') LIKE '%🎬 Original:%'
                       OR COALESCE(extra->>'facebook_content','') LIKE '%Footage:%'
                       OR COALESCE(extra->>'threads_content','') LIKE '%🎬 Original:%'
                       OR COALESCE(extra->>'threads_content','') LIKE '%Footage:%'
                       OR COALESCE(extra->>'youtube_content','') LIKE '%🎬 Original:%'
                       OR COALESCE(extra->>'youtube_content','') LIKE '%Footage:%'
                       OR COALESCE(extra->>'twitter_content','') LIKE '%🎬 Original:%'
                       OR COALESCE(extra->>'twitter_content','') LIKE '%Footage:%'
                       OR COALESCE(extra->>'tiktok_content','') LIKE '%🎬 Original:%'
                       OR COALESCE(extra->>'tiktok_content','') LIKE '%Footage:%'
                ),
                COUNT(DISTINCT niche_id)
                FROM blueprints
                WHERE status = 'PUBLISHED'
                  AND updated_at > NOW() - INTERVAL '6 hours'
            """)
            row = cur.fetchone()
    print(f"{row[0]}:{row[1]}:{row[2]}")
except Exception as e:
    print(f"ERR:{e}")
PY
)

if [[ "$RESULT" == "PY_ERROR" ]] || [[ "$RESULT" == ERR:* ]]; then
    echo "[fatal] Layer 5 query failed: ${RESULT#ERR:}" >&2
    exit 2
fi

TOTAL="${RESULT%%:*}"
REST="${RESULT#*:}"
WITH_CREDIT="${REST%%:*}"
NICHES="${REST##*:}"

echo "[verify] Last 6h: $WITH_CREDIT/$TOTAL publishes carry credit marker across $NICHES niches"

if [ "$TOTAL" -eq 0 ]; then
    echo "[fatal] No publishes in last 6h — can't verify. Check pipeline logs; publisher fires 06:30 UTC / 12:00 IST." >&2
    exit 2
fi

PCT=$(( WITH_CREDIT * 100 / TOTAL ))
echo "[verify] Attribution rate: ${PCT}% (threshold 80%)"

if [ "$PCT" -lt 80 ]; then
    cat <<END
[fail] Writer wire STILL broken — only ${PCT}% of publishes have markers.

Layer 4 BLOCK will remain OFF. Trace path:
  1. Check pipeline logs:
     journalctl -u 'genlab-pipeline-*.service' --since '2h ago' | grep -Ei 'source_attribution|channel_name'
  2. Query a fresh blueprint's content field:
     psql \$DATABASE_URL -c "SELECT extra->'content' FROM blueprints
        WHERE status='PUBLISHED' AND updated_at > NOW() - INTERVAL '2h' LIMIT 1"
  3. If content.source_attribution is empty, base_writing._write_story_llm
     didn't propagate it (Bug C regression) — check PR #779's fix is in
     the deployed HEAD.
  4. If content.source_attribution is set but caption lacks the marker,
     push_to_backlog._credit helper didn't run — check
     genlab_core/pipeline/stages/push_to_backlog.py:1929.

See [[session-2026-07-13-audit-followup-writer-wire]] for the full trace.
END
    exit 1
fi

echo "[pass] Writer wire healthy at ${PCT}% — safe to flip LAYER4_BLOCK=1"

# ── Flip logic ──────────────────────────────────────────────────
if grep -q "$FLAG_KEY" "$ENV_FILE"; then
    CURRENT_VALUE=$(grep "$FLAG_KEY" "$ENV_FILE" | head -1 | cut -d= -f2)
    if [[ "$CURRENT_VALUE" == "1" ]]; then
        echo "[skip] LAYER4_BLOCK already =1 in .env — nothing to do"
        exit 0
    fi
    echo "[found] LAYER4_BLOCK=$CURRENT_VALUE in .env; will replace with =1"
    ACTION="replace"
else
    echo "[found] LAYER4_BLOCK absent from .env; will append =1"
    ACTION="append"
fi

case "$MODE" in
    dry)
        echo "[dry] would $ACTION LAYER4_BLOCK=1 in $ENV_FILE + restart genlab-dashboard.service"
        exit 0
        ;;
    prompt)
        printf "[prompt] Proceed with flip? [y/N] "
        read -r ANSWER
        if [[ "$ANSWER" != "y" && "$ANSWER" != "Y" ]]; then
            echo "[abort] Not flipping. Re-run with --yes to skip prompt."
            exit 0
        fi
        ;;
    auto)
        echo "[auto] Proceeding without prompt (--yes)"
        ;;
esac

# Perform the flip. Use sed for replace, echo>> for append. Do NOT
# read the .env contents into a shell var — write ops only.
if [[ "$ACTION" == "replace" ]]; then
    sed -i "s/^GENLAB_ATTRIBUTION_LAYER4_BLOCK=.*/$FLAG_LINE/" "$ENV_FILE"
else
    echo "$FLAG_LINE" >> "$ENV_FILE"
fi

# Verify the specific line is now =1 (no other .env content shown)
if grep -q "^${FLAG_LINE}$" "$ENV_FILE"; then
    echo "[flip] LAYER4_BLOCK=1 written to $ENV_FILE"
else
    echo "[fatal] Write appeared to succeed but grep can't find the line — inspect $ENV_FILE manually" >&2
    exit 2
fi

systemctl restart genlab-dashboard.service
echo "[flip] genlab-dashboard.service restarted"

# Health-check the restart
sleep 2
if systemctl is-active --quiet genlab-dashboard.service; then
    echo "[flip] dashboard is active"
else
    echo "[fatal] dashboard failed to restart — check journalctl -u genlab-dashboard.service" >&2
    exit 2
fi

echo ""
echo "[done] Layer 4 BLOCK is now ACTIVE across all 6 wired platforms"
echo "       (Facebook, Instagram, YouTube, Threads, X/Twitter, TikTok)"
echo ""
echo "Next: monitor attribution_health_monitor.timer + compliance_events for"
echo "the next 24h. If no false-blocks, safe to flip LAYER3_ENFORCE=1 too."
