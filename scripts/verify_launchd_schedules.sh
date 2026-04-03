#!/usr/bin/env bash
# Verify all GenLab LaunchAgent schedules are in IST (local time).
# launchd StartCalendarInterval uses LOCAL TIME, not UTC.
# Run this after any plist changes to catch timezone mistakes.
set -euo pipefail

echo "=== LaunchAgent Schedule Verification ==="
echo "System timezone: $(date +%Z) ($(date +%z))"
echo "All times below should be IST (Indian Standard Time)."
echo ""

EXPECTED=(
    # agent:hour:minute:description
    "daily-intel:8:0:BB pipeline (02:30 UTC)"
    "criticalrush:9:30:Gaming pipeline (04:00 UTC)"
    "framedrift:11:30:Anime pipeline (06:00 UTC)"
    "splicereel:13:30:Movies pipeline (08:00 UTC)"
    "clutchwire:15:30:Sports pipeline (10:00 UTC)"
    "publisher:12:5:Publish window (06:35 UTC)"
    "insights-collector:12:15:Insights (06:45 UTC)"
    "morning-briefing:8:15:Morning report (02:45 UTC)"
    "feedback-collector:19:0:Feedback (13:30 UTC)"
    "token-refresh:2:0:Token refresh (20:30 UTC)"
    "cleanup:3:30:Cleanup (22:00 UTC)"
    "daily-verify:22:0:Daily verify (16:30 UTC)"
    "db-maintenance:8:45:DB maintenance (03:15 UTC)"
    "affiliate-link-check:9:15:Affiliate check (03:45 UTC)"
)

ERRORS=0
for entry in "${EXPECTED[@]}"; do
    agent=$(echo "$entry" | cut -d: -f1)
    exp_hour=$(echo "$entry" | cut -d: -f2)
    exp_min=$(echo "$entry" | cut -d: -f3)
    desc=$(echo "$entry" | cut -d: -f4)
    
    plist="$HOME/Library/LaunchAgents/com.genlab.${agent}.plist"
    if [ ! -f "$plist" ]; then
        echo "  ✗ MISSING: $agent"
        ERRORS=$((ERRORS + 1))
        continue
    fi
    
    actual=$(plutil -convert json -o - "$plist" 2>/dev/null | python3 -c "
import sys, json
d = json.load(sys.stdin)
sci = d.get('StartCalendarInterval')
if isinstance(sci, list):
    print(f\"{sci[0].get('Hour',0)}:{sci[0].get('Minute',0)}\")
elif isinstance(sci, dict):
    print(f\"{sci.get('Hour',0)}:{sci.get('Minute',0)}\")
else:
    print('N/A')
" 2>/dev/null)
    
    expected="${exp_hour}:${exp_min}"
    if [ "$actual" = "$expected" ]; then
        printf "  ✓ %-24s %5s IST  %s\n" "$agent" "$actual" "$desc"
    elif [ "$actual" = "N/A" ]; then
        printf "  ⊘ %-24s interval/always-on\n" "$agent"
    else
        printf "  ✗ %-24s WRONG: got %s, expected %s IST  %s\n" "$agent" "$actual" "$expected" "$desc"
        ERRORS=$((ERRORS + 1))
    fi
done

echo ""
if [ "$ERRORS" -eq 0 ]; then
    echo "All schedules correct."
else
    echo "⚠ $ERRORS schedule(s) need fixing!"
    exit 1
fi
