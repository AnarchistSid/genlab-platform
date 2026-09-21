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

# EVERY STEP ANNOUNCES ITS VERDICT, and the exit trap names where we died.
#
# 2026-09-19: step 4's curl had no --max-time. Against a socket that ACCEPTS and
# never answers — gunicorn's master listening while its worker failed to boot —
# it waited indefinitely. The run looked "stuck", was read as slow, and the
# dashboard stayed down for four hours while this script sat on a curl. A check
# that cannot time out cannot report.
STEP=""
note() { STEP="$*"; echo "[verify] $*"; }
pass() { echo "  ✓ $*"; }
fail() { echo "  ✗ $*"; EXITCODE=1; FAILURES+=("$*"); }

_on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "${_FINISHED:-0}" != "1" ]; then
    echo "  ✗ ABORTED during step: ${STEP:-<before step 1>} (exit $rc)"
    echo "[verify] FAILED — died in: ${STEP:-<before step 1>}"
  fi
}
trap _on_exit EXIT

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

# ANIME-07 §3. Every name in PROMOTED_COLUMNS must be a real column, checked
# against THIS database, on every deploy.
#
# 2026-09-21: `source_url` sat in PROMOTED_COLUMNS with no column behind it.
# Dormant until something wrote the field, then every insert died with
# `column "source_url" of relation "blueprints" does not exist` and four
# niches produced ZERO blueprints for 24 hours. Per-blueprint WARNING, stage
# "completed", run exit 0, timer green — nothing paged.
#
# This is rule #28's mirror. The rule guards column-without-promoted-name
# (writes vanish into `extra`); this guards promoted-name-without-column
# (writes fail outright). tests/storage/test_promoted_columns_vs_db_schema.py
# checks the same thing but is opt-in on GENLAB_SCHEMA_PIN_DSN, which is not
# set in CI — so it had never run. A guard that fires only when someone
# remembers to point it at a database is not a guard. This one runs on every
# deploy, against the database the code is about to talk to.
note "3b. PROMOTED_COLUMNS vs the live schema"
# Python emits the names (no DB needed); psql checks them against THIS
# database, the same way every other schema check in this file works. An
# earlier version opened the pool inside python and died on a missing
# DATABASE_URL under `sudo -u genlab` — the env is not inherited there.
promoted_pairs=$(cd "$GENLAB" && "$GENLAB/.venv/bin/python" - <<'PY'
import sys
sys.path.insert(0, "genlab-core/src")
from genlab_core.storage.postgres import PROMOTED_COLUMNS
for table, names in PROMOTED_COLUMNS.items():
    for name in names:
        print(f"{table}\t{name}")
PY
)
# KNOWN DRIFT, 2026-09-21. Each of these is a promoted name whose column is
# missing ON PROD ONLY, and the cause is one migration, not six mistakes:
# a1b2c3d4e5f6 creates `stories` with `CREATE TABLE IF NOT EXISTS`. Prod's
# stories table already existed with a narrower shape, so the migration RAN,
# alembic recorded it as applied, and it added nothing. Same shape as every
# other defect in this session: a statement reporting success while doing
# nothing.
#
# They are allowlisted rather than removed from PROMOTED_COLUMNS: R-63 added
# them deliberately because they ARE real columns wherever that migration
# actually executed, and dropping them would reintroduce the silent
# write-to-extra/read-from-NULL data loss R-63 fixed.
#
# Verified by direct read 2026-09-21, not inferred: as the app user
# (genlab_app on db `genlab`), search_path "$user",public, there is exactly
# ONE `stories` table, in schema `public`, with 13 columns and none of these
# four. So this check is not reading the wrong schema or database. They are
# dormant rather than broken because the only stories.create in the tree does
# not write them — which is precisely the difference from
# blueprints.source_url, where something HAD started writing it.
#
# The fix is DDL (ADD COLUMN IF NOT EXISTS) against a migration graph that
# currently has NINE heads, which is its own piece of work. Until then this
# list is the debt, written down and counted, and anything NOT on it fails
# the deploy.
PROMOTED_DRIFT_ALLOWED="stories.video_url stories.source_type stories.source_name stories.video_id pending_engagement.scheduled_at sources.last_fetched"

promoted_drift=""
while IFS=$'\t' read -r tbl col; do
    [ -z "$tbl" ] && continue
    case " $PROMOTED_DRIFT_ALLOWED " in *" $tbl.$col "*) continue;; esac
    exists=$(sudo -u genlab psql "$DB_URL" -tA -c \
        "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='$tbl' AND column_name='$col') OR NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='$tbl');" 2>/dev/null | tr -d ' ')
    [ "$exists" = "t" ] || promoted_drift="$promoted_drift $tbl.$col"
done <<< "$promoted_pairs"
if [ -z "$promoted_drift" ]; then
    pass "every PROMOTED_COLUMNS name exists as a column (6 known drifts allowlisted)"
else
    fail "PROMOTED_COLUMNS names with no column:$promoted_drift"
fi

note "4. Internal endpoint reachability (5151 = dashboard local bind)"
for path in compliance/stats scheduling/pauses source-discovery/proposals; do
    # --max-time is load-bearing, not hygiene: without it a listening socket
    # whose worker never booted blocks this loop forever (2026-09-19).
    url="http://127.0.0.1:5151/api/v1/$path"
    t0=$SECONDS
    # No `|| echo 000`: on timeout curl ALREADY prints 000 via -w and THEN exits
    # non-zero, so the fallback appends a second one and $http becomes "000000",
    # which matches no branch. `|| true` keeps set -e happy without doubling.
    http=$(curl -sS --max-time "${VERIFY_HTTP_TIMEOUT:-10}" -o /dev/null \
             -w "%{http_code}" "$url" 2>/dev/null || true)
    http=${http:-000}
    waited=$((SECONDS - t0))
    # 401 = auth wall (route wired); 404 = route missing
    if [ "$http" = "401" ] || [ "$http" = "200" ]; then
        pass "$url → $http (${waited}s)"
    elif [ "$http" = "000" ]; then
        # The shape that matters: connection made, no response. Name it plainly
        # so nobody reads it as "the check is slow".
        fail "$url → NO RESPONSE after ${waited}s (curl code 000 — the port is listening but nothing answered; check the worker booted, not just that the unit is active)"
    else
        fail "$url → $http after ${waited}s (expected 200 or 401, NOT 404 or 5xx)"
    fi
done

# 2026-07-21: version.env path was moved 2026-06-26 from
# /etc/genlab/version.env → /opt/genlab/.version.env (genlab user
# can't write to root-owned /etc/genlab). deploy.sh:330 writes the
# new path; this verify script was never updated. Result: post-deploy
# checks reported version drift every hour on every deploy → hourly
# systemd `service_down` alarm despite deploys being fine.
# Check the new path first; fall back to the legacy path for backward
# compat with any stale servers that haven't run the new deploy.sh.
note "4b. systemd unit files match their source"
# Phase 6.8 of deploy.sh copies deploy/systemd-phase2/* into /etc/systemd/system
# and daemon-reloads. If an installed unit still differs afterwards the copy did
# not happen, and the fix you shipped is sitting in the repo doing nothing —
# which is how a SuccessExitStatus fix went unapplied for weeks, and how a
# readiness probe would too.
_drift=0
for _src in "$GENLAB"/deploy/systemd-phase2/*.service "$GENLAB"/deploy/systemd-phase2/*.timer; do
    [ -f "$_src" ] || continue
    _bn=$(basename "$_src")
    _dep="/etc/systemd/system/$_bn"
    [ -f "$_dep" ] || continue          # not installed: Phase 6.8 leaves those alone
    if ! diff -q "$_src" "$_dep" >/dev/null 2>&1; then
        fail "unit DRIFT: $_bn differs from deploy/systemd-phase2 (deployed copy is stale)"
        _drift=$((_drift + 1))
    fi
done
[ "$_drift" -eq 0 ] && pass "all installed units match their source"

note "5. /opt/genlab/.version.env present and current"
VERSION_ENV_PATH=""
if [ -f /opt/genlab/.version.env ]; then
    VERSION_ENV_PATH="/opt/genlab/.version.env"
elif [ -f /etc/genlab/version.env ]; then
    VERSION_ENV_PATH="/etc/genlab/version.env"
fi
if [ -n "$VERSION_ENV_PATH" ]; then
    deployed_sha=$(grep ^GENLAB_GIT_COMMIT= "$VERSION_ENV_PATH" | cut -d= -f2- | tr -d "'\"")
    # 2026-08-10: use both short AND full SHA from HEAD, then prefix-match
    # either direction so `.version.env` written with either the short SHA
    # (deploy.sh's `git rev-parse --short HEAD` convention) or the full
    # 40-char SHA (any manual/scripted write) both pass equivalently. Old
    # code did strict `=` and false-fired when the file held a full SHA
    # but HEAD probe returned the short one — surfaced 2026-08-10 during
    # a drift-recovery deploy that hand-wrote .version.env with `git
    # rev-parse HEAD` (no --short).
    git_head_short=$(sudo -u genlab git -C $GENLAB rev-parse --short HEAD)
    git_head_full=$(sudo -u genlab git -C $GENLAB rev-parse HEAD)
    if [ -z "$deployed_sha" ]; then
        fail "$VERSION_ENV_PATH has empty GENLAB_GIT_COMMIT (re-run deploy.sh)"
    elif [ "$deployed_sha" = "$git_head_short" ] \
      || [ "$deployed_sha" = "$git_head_full" ] \
      || [ "${git_head_full#$deployed_sha}" != "$git_head_full" ] \
      || [ "${deployed_sha#$git_head_short}" != "$deployed_sha" ]; then
        pass "$VERSION_ENV_PATH matches git HEAD ($deployed_sha)"
    else
        fail "$VERSION_ENV_PATH=$deployed_sha but git HEAD=$git_head_short (re-run deploy.sh)"
    fi
else
    fail "/opt/genlab/.version.env MISSING (and legacy /etc/genlab/version.env also absent)"
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

note "7. Failure-alert template state (auto-reset if found in failed state)"
# 2026-06-27 — added after investigating a 4-day-stuck failed
# instance of genlab-service-failure-alert@*. The OnFailure handler
# IS the alert mechanism for other services — if IT enters failed
# state, every other service's alerts get masked silently. Detect +
# auto-reset because nothing recovers it otherwise.
failed_alert_instances=$(systemctl list-units 'genlab-service-failure-alert@*' --state=failed --no-legend --plain 2>/dev/null | awk '{print $1}')
if [ -z "$failed_alert_instances" ]; then
    pass "no failed genlab-service-failure-alert instances"
else
    for inst in $failed_alert_instances; do
        echo "  ⚠ resetting failed alert instance: $inst"
        systemctl reset-failed "$inst" 2>/dev/null || true
    done
    pass "auto-reset $(echo "$failed_alert_instances" | wc -w | tr -d ' ') failed alert instance(s)"
fi

note "8. Writer wire — recent captions carry credit marker"
# Added 2026-07-13 after the W1 audit trace caught the writer wire
# silently broken for weeks. Old Layer 5 metric masked the failure
# by counting source_channel_id (populated via a DIFFERENT path) as
# attribution — even though the caption itself shipped without a
# visible credit line. This check queries the SAME signal Layer 5
# uses today (caption marker only) so a broken writer wire fires
# within one publish cycle instead of surfacing via DMCA notice.
#
# Deliberately permissive on total_published: publish frequency is
# 5-15/day and the 24h window may catch a lull. If no publishes have
# happened, we can't judge — pass with a note. Below-threshold
# fires only when there ARE publishes AND >20% lack the marker.
# 2026-07-21: `sudo -u genlab` strips the parent env by default → the Python
# heredoc below fell through to `dbname=genlab`, hit the non-existent Unix
# socket, and silent-passed the check every time. Same class-of-bug as
# scripts/verify_writer_wire_and_flip_l4.sh fixed 2026-07-14 → `9ebc4023`.
#
# DASH-01 §0 (2026-09-15): the original fix passed the DSN as
# `sudo -u genlab env DATABASE_URL="$DB_URL" …`, and **sudo logs the full
# command line to journald**. That put the genlab_app password into the
# journal, into the dashboard's alert body, and into any screenshot of it —
# 2 lines found, emitted under genlab-pipeline-{sports,movies}.
#
# The child now sources the env file itself, so the secret never appears as
# an argument. `set -a` exports everything the file defines; the subshell
# keeps that out of this script's environment.
# Rule: a secret passed as an argv element is a secret in the process table
# and in every logger that records command lines.
attribution_check=$(sudo -u genlab bash -c '
  set -a; . '"$GENLAB"'/.env; set +a
  exec '"$VENV"' -
' <<'PY' 2>&1 || echo "PY_ERROR"
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
                )
                FROM blueprints
                WHERE status = 'PUBLISHED'
                  AND updated_at > NOW() - INTERVAL '24 hours'
            """)
            total, with_credit = cur.fetchone()
    print(f"{total}:{with_credit}")
except Exception as e:
    print(f"ERR:{e}")
PY
)
if [[ "$attribution_check" == "PY_ERROR" ]] || [[ "$attribution_check" == ERR:* ]]; then
    # DB unreachable — not a deploy failure, just a check we can't
    # run. Skip rather than false-fail. The dedicated
    # attribution_health_monitor.timer runs every 30m and will
    # cover the gap.
    pass "writer wire check skipped (DB unreachable: ${attribution_check#ERR:})"
else
    total="${attribution_check%%:*}"
    with_credit="${attribution_check##*:}"
    if [ "$total" -eq 0 ]; then
        pass "writer wire check: no recent publishes in 24h window"
    else
        # Percentage arithmetic in bash requires integer math tricks
        pct=$(( with_credit * 100 / total ))
        # 80% threshold — some fetchers (steam_spike, anilist) legitimately
        # can't populate channel_name, so 100% is unrealistic. Alert
        # threshold in the sibling monitor is 99% (single-miss visible);
        # deploy-verify uses 80% to catch class-of-bug regressions like
        # the 2026-07-13 W1 (which showed 0%) rather than page on a
        # single degraded niche.
        if [ "$pct" -ge 80 ]; then
            pass "writer wire: ${with_credit}/${total} recent publishes carry credit marker (${pct}%)"
        else
            fail "writer wire BROKEN: only ${with_credit}/${total} recent publishes carry credit marker (${pct}%, threshold 80%) — check base_writing._write_story_llm + _story_to_video_dict"
        fi
    fi
fi

# ── Step 9: the repo root is not a scratch folder ───────────────────────────
# CLEAN-01. 94 setup screenshots, a second copy of .env, an empty genlab.db and
# a 170-day-old firebase log had accumulated at the top level. None was ever
# referenced. A root .env copy is a leak waiting for a `git add -A`.
STEP="9. Repo root contains only what belongs there"
note "[verify] $STEP"
# Repo files, plus the RUNTIME STATE the services legitimately keep beside the
# checkout on prod: the deployed-version stamp, the Threads token file and its
# flock sidecar (rule #20), the cookie/session jars the fetchers refresh, the
# router's persisted state, and gunicorn's control file. These are not clutter
# and a guard that fails on them fails every prod deploy.
ROOT_ALLOWED="README.md CLAUDE.md CHANGELOG.md CONTRIBUTING.md CODE_OF_CONDUCT.md SECURITY.md \
LICENSE Makefile pyproject.toml uv.lock docker-compose.yml .gitignore .gitattributes \
.editorconfig .dockerignore .pre-commit-config.yaml .env.example .env \
.version.env .threads_tokens.json .threads_tokens.lock .reddit_cookies.txt \
.youtube_cookies.txt .youtube_session.json .conformal_router_state.json gunicorn.ctl"
ROOT_STRAY=0
for f in "${GENLAB_ROOT:-/opt/genlab}"/* "${GENLAB_ROOT:-/opt/genlab}"/.*; do
    b="$(basename "$f")"
    [ -d "$f" ] && continue
    [ "$b" = "." ] || [ "$b" = ".." ] && continue
    case " $ROOT_ALLOWED " in *" $b "*) continue ;; esac
    case "$b" in
        .env.bak*|.env.backup*)
            note "  [ x ] SECRET COPY at repo root: $b ($(stat -c %a "$f" 2>/dev/null))"
            ;;
        *) note "  [ x ] stray at repo root: $b" ;;
    esac
    ROOT_STRAY=$((ROOT_STRAY + 1))
done
if [ "$ROOT_STRAY" -eq 0 ]; then
    note "  [ ✓ ] repo root clean"
else
    fail "repo root has $ROOT_STRAY stray file(s) — env backups belong in .backups/env/ at 600, screenshots in .audit/screenshots/<date>/"
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
_FINISHED=1
exit $EXITCODE
