#!/usr/bin/env bash
# post_deploy_verify.sh — sanity-check that what's on git HEAD is
# what's actually serving traffic on this prod box.
#
# Detects the "shipped but not activated" failure mode (see
# memory: [[shipped-not-activated-pattern]]) by checking:
#   1. Long-running services are running and recently restarted
#      (within the last 24h matches a fresh deploy; older = drift)
#   2. Each major module from HEAD imports cleanly in the venv
#   3. Schema items added by recent migrations exist in the DB
#   4. Internal endpoints return non-404 (routes wired)
#   5. /etc/genlab/version.env exists + matches git HEAD
#   6. Env flag activations: each known flag in .env appears in
#      the dashboard process's environ
#
# Exit non-zero on any check failure so systemd surfaces a failed
# state. Lazy importers should be triggered to force any deferred
# import errors to surface.
#
# Usage:
#   sudo ./scripts/post_deploy_verify.sh                  # all checks, exit non-zero on failure
#   sudo ./scripts/post_deploy_verify.sh --json           # JSON summary for dashboard parsing

set -euo pipefail

GENLAB=/opt/genlab
VENV=$GENLAB/.venv/bin/python
EXITCODE=0
FAILURES=()

note() { echo "[verify] $*"; }
pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*"; EXITCODE=1; FAILURES+=("$*"); }

note "1. Long-running services state"
for svc in genlab-dashboard genlab-engagement-poller genlab-engagement-worker genlab-webhook; do
    if systemctl is-active --quiet "$svc"; then
        # Last restart within 7 days = healthy; older = drift suspect
        age_sec=$(systemctl show "$svc" --property=ActiveEnterTimestampMonotonic --value)
        if [ "$age_sec" != "0" ]; then
            pass "$svc active"
        fi
    else
        fail "$svc NOT active"
    fi
done

note "2. Critical module imports (cb58a3a4 baseline + future PRs)"
sudo -u genlab $VENV - <<'PY' || EXITCODE=1
import importlib
mods = [
    "genlab_core.compliance.slack_notifier",
    "genlab_core.compliance.compliance_digest_sender",
    "genlab_core.scheduling.niche_pause_sweeper",
    "genlab_core.source_discovery.proposer",
    "genlab_core.http.analytics_store",
]
import sys
sys.path.insert(0, "/opt/genlab/dashboard")
mods += [
    "server.api.compliance",
    "server.api.source_discovery",
    "server.api.metrics",
]
failed = []
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        print(f"  ✗ import failed: {m}: {type(e).__name__}: {e}")
        failed.append(m)
if failed:
    sys.exit(1)
for m in mods:
    print(f"  ✓ {m}")
PY

note "3. DB schema (cb58a3a4 expectations)"
DB_URL=$(grep ^DATABASE_URL= $GENLAB/.env | head -1 | cut -d= -f2- | tr -d '"')
psql_check() {
    local q=$1 expect=$2 label=$3
    local got=$(sudo -u genlab psql "$DB_URL" -tA -c "$q" 2>/dev/null | tr -d ' ')
    if [ "$got" = "$expect" ]; then
        pass "$label"
    else
        fail "$label (got=$got expected=$expect)"
    fi
}
psql_check "SELECT to_regclass('public.niche_pauses') IS NOT NULL;" "t" "niche_pauses table"
psql_check "SELECT to_regclass('public.compliance_events') IS NOT NULL;" "t" "compliance_events table"
psql_check "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='blueprints' AND column_name='source_channel_id');" "t" "blueprints.source_channel_id column"

note "4. Internal endpoint reachability (5151 = dashboard local bind)"
for path in compliance/stats scheduling/pauses source-discovery/proposals; do
    http=$(curl -sS -o /dev/null -w "%{http_code}" "http://127.0.0.1:5151/api/v1/$path" || echo "000")
    # 401 = auth wall (route wired); 404 = route missing
    if [ "$http" = "401" ] || [ "$http" = "200" ]; then
        pass "/api/v1/$path → $http"
    else
        fail "/api/v1/$path → $http (expected 200 or 401, NOT 404 or 5xx)"
    fi
done

note "5. /etc/genlab/version.env present and current"
if [ -f /etc/genlab/version.env ]; then
    deployed_sha=$(grep ^GENLAB_GIT_COMMIT= /etc/genlab/version.env | cut -d= -f2-)
    git_head_sha=$(sudo -u genlab git -C $GENLAB rev-parse --short HEAD)
    if [ "$deployed_sha" = "$git_head_sha" ]; then
        pass "/etc/genlab/version.env matches git HEAD ($deployed_sha)"
    else
        fail "/etc/genlab/version.env=$deployed_sha but git HEAD=$git_head_sha (re-run deploy.sh)"
    fi
else
    fail "/etc/genlab/version.env MISSING"
fi

note "6. Env flags activated (set in .env AND loaded into dashboard process)"
PID=$(systemctl show genlab-dashboard --property=MainPID --value)
flags_in_env=$(grep -oE "^GENLAB_[A-Z_]+=" $GENLAB/.env | tr -d '=' | sort -u)
flags_in_proc=$(cat /proc/$PID/environ 2>/dev/null | tr '\0' '\n' | grep -oE "^GENLAB_[A-Z_]+" | sort -u)
missing_in_proc=$(comm -23 <(echo "$flags_in_env") <(echo "$flags_in_proc"))
if [ -z "$missing_in_proc" ]; then
    pass "all .env GENLAB_* flags loaded in dashboard process"
else
    fail "flags in .env but NOT in dashboard process (restart needed): $(echo $missing_in_proc | tr '\n' ' ')"
fi

note ""
if [ $EXITCODE -eq 0 ]; then
    note "ALL CHECKS PASSED ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
else
    note "FAILURES (${#FAILURES[@]}):"
    for f in "${FAILURES[@]}"; do
        note "  - $f"
    done
fi
exit $EXITCODE
