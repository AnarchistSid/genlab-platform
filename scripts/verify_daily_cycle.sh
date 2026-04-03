#!/usr/bin/env bash
# verify_daily_cycle.sh — Run after pipelines complete to verify full cycle
# Usage: ./scripts/verify_daily_cycle.sh
# Designed to run at ~11:00 UTC (after all 5 pipelines finish)

set -euo pipefail
GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export GENLAB_ROOT="$GENLAB"
LOG="$GENLAB/.logs/daily_verify_$(date +%Y%m%d).log"
export BACKLOG_CONFIG_PATH="$GENLAB/genlab-core/config/lists_config.yaml"

exec > >(tee -a "$LOG") 2>&1
echo ""
echo "══════════════════════════════════════════════"
echo "  DAILY CYCLE VERIFICATION — $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "══════════════════════════════════════════════"

export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"
cd "$GENLAB"

"$HOME/.local/bin/uv" run --package genlab-core python3 << 'PYEOF'
import json, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

_GENLAB = os.environ.get("GENLAB_ROOT") or os.environ.get("GENLAB_PROJECT_ROOT", "")
sys.path.insert(0, os.path.join(_GENLAB, "genlab-core", "src"))
os.environ.setdefault("BACKLOG_CONFIG_PATH", os.path.join(_GENLAB, "genlab-core", "config", "lists_config.yaml"))

runs_dir = Path(_GENLAB) / ".tmp" / "runs"
today = datetime.now(timezone.utc).strftime("%Y%m%d")
cutoff = datetime.now(timezone.utc) - timedelta(hours=6)

print("\n── PIPELINE RUNS (today) ──")
all_ok = True
for niche in ["ai_creators", "anime", "gaming", "movies", "sports"]:
    today_runs = sorted([
        d for d in runs_dir.iterdir()
        if d.is_dir() and d.name.startswith(f"{niche}_{today}")
    ], key=lambda x: x.stat().st_mtime, reverse=True)

    if not today_runs:
        print(f"  ✗ {niche}: NO RUN TODAY")
        all_ok = False
        continue

    latest = today_runs[0]
    report_path = latest / "run_report.json"
    if not report_path.exists():
        print(f"  ✗ {niche}: run exists but no report — {latest.name}")
        all_ok = False
        continue

    d = json.loads(report_path.read_text())
    m = d.get("metrics", {})
    bp = m.get("blueprints_count", 0)
    overlaid = m.get("text_overlays", {}).get("overlaid", 0)
    status = d.get("status", "?")
    duration = d.get("duration_seconds", 0)

    icon = "✓" if bp > 0 and status == "success" else "⚠" if status == "success" else "✗"
    print(f"  {icon} {niche}: {bp} blueprints, {overlaid} overlaid, {duration:.0f}s — {status}")
    if bp == 0:
        all_ok = False

# Check insights
print("\n── INSIGHTS COLLECTION ──")
try:
    from genlab_core.http.backlog_client import BacklogClient
    client = BacklogClient()
    records = client.publishing_analytics.all(max_records=500)

    by_status = {}
    recent_insights = 0
    for r in records:
        f = r.get("fields", r)
        s = str(f.get("status", ""))
        by_status[s] = by_status.get(s, 0) + 1

    total = len(records)
    tracked = by_status.get("INSIGHTS_6H", 0) + by_status.get("INSIGHTS_24H", 0)
    untracked = by_status.get("SUCCESS", 0)

    print(f"  Total posts: {total}")
    print(f"  Tracked (INSIGHTS): {tracked} ({tracked*100//total if total else 0}%)")
    print(f"  Awaiting collection: {untracked}")
    print(f"  Skipped (no creds): {by_status.get('SKIPPED', 0)}")
    print(f"  Deleted by platform: {by_status.get('DELETED', 0)}")
except Exception as e:
    print(f"  ✗ Could not check insights: {e}")

# Check engagement
print("\n── ENGAGEMENT ──")
import subprocess
result = subprocess.run(["launchctl", "list"], capture_output=True, text=True)
pollers = [l for l in result.stdout.splitlines() if "poller" in l and l.split()[0] != "-"]
print(f"  Active pollers: {len(pollers)}")
for p in pollers:
    parts = p.split()
    print(f"    PID={parts[0]} {parts[2]}")

# Check services
print("\n── SERVICES ──")
for svc in ["prefect-server", "prefect-worker", "engagement.worker", "review-server"]:
    line = [l for l in result.stdout.splitlines() if svc in l]
    if line:
        parts = line[0].split()
        pid = parts[0]
        exit_code = parts[1]
        status = "RUNNING" if pid != "-" else f"STOPPED (exit={exit_code})"
        print(f"  {svc}: {status}")
    else:
        print(f"  {svc}: NOT REGISTERED")

# Summary
print("\n── VERDICT ──")
if all_ok:
    print("  ✓ ALL 5 PIPELINES PRODUCED BLUEPRINTS")
else:
    print("  ✗ SOME PIPELINES NEED ATTENTION")

print("")
PYEOF

echo "Verification complete. Log: $LOG"

# ── LaunchAgent schedule verification ──────────────────────
echo ""
echo "=== LaunchAgent Schedule Check ==="
if bash "$GENLAB/scripts/verify_launchd_schedules.sh" 2>/dev/null; then
    echo "  ✓ All schedules correct"
else
    echo "  ⚠ Schedule mismatch detected — check verify_launchd_schedules.sh output"
fi
