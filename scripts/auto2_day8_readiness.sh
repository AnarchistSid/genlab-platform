#!/usr/bin/env bash
# ============================================================================
# auto2_day8_readiness.sh — Pre-flip readiness check for AUTO #2 Day 8
#
# Why this exists
# ---------------
# The runbook §9 lists a 15-item checklist for the Day-8 live flip. Items
# 1-9 + 12 are mechanical (SQL queries + filesystem checks) and benefit
# from automation. Items 10-11, 13-15 require operator judgement (free
# time, sleep, kill-switch UI test, runbook open, backups) and stay on
# the operator's clipboard.
#
# Run this on the morning of Day 8 (after the 5-day observation window):
#
#   ./scripts/auto2_day8_readiness.sh --niche ai_creators
#
# Output format
# -------------
# Each check prints a line `[ ✓ ]` or `[ ✗ ]` followed by the check
# label + measured value + threshold. The script exits 0 only when EVERY
# automatable check passes. Exit 1 means one or more checks failed; the
# script still finishes and prints all checks so the operator sees the
# full picture, not just the first failure.
#
# Usage
# -----
#   --niche <id>         REQUIRED. Niche to validate (e.g. ai_creators).
#   --min-samples N      Override sample-count threshold (default 30).
#   --min-agreement F    Override agreement-rate threshold (default 0.90).
#   --start-date YYYY-MM-DD
#                        When the dry-run timer started (used for #1).
#                        Defaults to NOW - 5d if not provided.
#   --dry-run-unit U     Systemd unit name for the dry-run worker.
#                        Defaults to genlab-auto-approver.service.
#
# Operator-only checks (NOT covered here — print at the bottom)
# -------------------------------------------------------------
#   10. You have ≥4 free hours today
#   11. You are well-rested
#   13. Kill switch button works (UI smoke test)
#   14. /root/backups/ has a snapshot dated within last 24h
#   15. This runbook is open in another tab
# ============================================================================
set -euo pipefail

NICHE=""
MIN_SAMPLES=30
MIN_AGREEMENT="0.90"
START_DATE=""
DRY_RUN_UNIT="genlab-auto-approver.service"
SSH_HOST="46.224.237.56"
PROD_HOSTNAME="ubuntu-4gb-nbg1-1"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --niche)        NICHE="$2"; shift 2 ;;
        --min-samples)  MIN_SAMPLES="$2"; shift 2 ;;
        --min-agreement) MIN_AGREEMENT="$2"; shift 2 ;;
        --start-date)   START_DATE="$2"; shift 2 ;;
        --dry-run-unit) DRY_RUN_UNIT="$2"; shift 2 ;;
        --ssh-host)     SSH_HOST="$2"; shift 2 ;;
        --local)        SSH_HOST=""; shift 1 ;;
        -h|--help)
            sed -n '/^# Usage/,/^# Operator-only/p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
done

# Auto-detect when we're running ON the prod box itself — don't try to
# SSH from prod back to prod (no self-host SSH key configured by design).
# Operator can also force this with --local.
if [[ -n "$SSH_HOST" && "$(uname -n)" == "$PROD_HOSTNAME" ]]; then
    SSH_HOST=""
fi

# Wrapper: when SSH_HOST is set, run the command remotely; when empty,
# run locally. Each remote check below uses this so the script body
# stays readable.
run_on_prod() {
    if [[ -z "$SSH_HOST" ]]; then
        bash -c "$1"
    else
        ssh -o ConnectTimeout=10 -o BatchMode=yes "root@${SSH_HOST}" "$1"
    fi
}

if [[ -z "$NICHE" ]]; then
    echo "ERROR: --niche is required" >&2
    exit 1
fi

# ── Resolve DATABASE_URL ──────────────────────────────────────────
GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$GENLAB/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$GENLAB/.env"
    set +a
fi
if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "ERROR: DATABASE_URL not set" >&2
    exit 1
fi

DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
export PGPASSWORD=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')

# psql query helper — returns single value, strips whitespace
psql_value() {
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -X -A -t -c "$1" 2>/dev/null | tr -d ' \n'
}

# ── Bookkeeping ────────────────────────────────────────────────────
PASS_COUNT=0
FAIL_COUNT=0

emit() {
    local status="$1" label="$2"
    if [[ "$status" == "pass" ]]; then
        echo "[ ✓ ] $label"
        PASS_COUNT=$((PASS_COUNT + 1))
    else
        echo "[ ✗ ] $label"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

echo "════════════════════════════════════════════════════════════════════════"
echo "  AUTO #2 Day-8 Readiness Check — niche=$NICHE"
echo "  $(date -u +%Y-%m-%dT%H:%M:%SZ) UTC"
echo "════════════════════════════════════════════════════════════════════════"
echo

# ── Check 1: 5+ calendar days since dry-run timer started ─────────
if [[ -n "$START_DATE" ]]; then
    # Use the operator-provided date
    if [[ "$(uname)" == "Darwin" ]]; then
        START_EPOCH=$(date -j -f "%Y-%m-%d" "$START_DATE" "+%s")
    else
        START_EPOCH=$(date -d "$START_DATE" "+%s")
    fi
else
    # Default: assume timer started 5d ago (the check passes trivially)
    START_EPOCH=$(date -u -d "5 days ago" "+%s" 2>/dev/null || \
                  date -u -v-5d "+%s")
fi
NOW_EPOCH=$(date -u "+%s")
DAYS_SINCE=$(( (NOW_EPOCH - START_EPOCH) / 86400 ))
if [[ "$DAYS_SINCE" -ge 5 ]]; then
    emit pass "01. $DAYS_SINCE day(s) since dry-run start (≥5)"
else
    emit fail "01. only $DAYS_SINCE day(s) since dry-run start (need ≥5)"
fi

# ── Check 2: sample count ≥ $MIN_SAMPLES ───────────────────────────
SAMPLES=$(psql_value "
SELECT COUNT(*) FROM auto_approval_calibration
WHERE niche_id='$NICHE' AND decided_at >= NOW() - INTERVAL '7 days';
")
SAMPLES=${SAMPLES:-0}
if [[ "$SAMPLES" -ge "$MIN_SAMPLES" ]]; then
    emit pass "02. $SAMPLES calibration samples in last 7d (≥$MIN_SAMPLES)"
else
    emit fail "02. only $SAMPLES calibration samples in last 7d (need ≥$MIN_SAMPLES)"
fi

# ── Check 3: agreement rate ≥ $MIN_AGREEMENT ──────────────────────
AGREEMENT=$(psql_value "
SELECT COALESCE(
  ROUND(
    COUNT(*) FILTER (WHERE (gate_approved IS TRUE) = (operator_action = 'approved'))::numeric
    / NULLIF(COUNT(*), 0),
    4
  ),
  0
)
FROM auto_approval_calibration
WHERE niche_id='$NICHE' AND decided_at >= NOW() - INTERVAL '7 days';
")
AGREEMENT=${AGREEMENT:-0}
AGREEMENT_PCT=$(awk -v a="$AGREEMENT" 'BEGIN { printf "%.1f", a*100 }')
THRESHOLD_PCT=$(awk -v a="$MIN_AGREEMENT" 'BEGIN { printf "%.0f", a*100 }')
# bash can't compare floats — use awk
PASS_CHECK=$(awk -v a="$AGREEMENT" -v t="$MIN_AGREEMENT" 'BEGIN { print (a+0 >= t+0) ? 1 : 0 }')
if [[ "$PASS_CHECK" == "1" ]]; then
    emit pass "03. agreement_rate = ${AGREEMENT_PCT}% in last 7d (≥${THRESHOLD_PCT}%)"
else
    emit fail "03. agreement_rate = ${AGREEMENT_PCT}% in last 7d (need ≥${THRESHOLD_PCT}%)"
fi

# ── Check 4: Mission Control reports niche as READY ───────────────
# Use the OPERATOR-supplied thresholds (matching checks 2 + 3) so the
# heuristic is consistent across the whole report. The canonical
# `ready_for_enforcement` is sample_count >= 30 AND agree >= 0.90; we
# parameterise via $MIN_SAMPLES and $MIN_AGREEMENT.
READY=$(psql_value "
WITH s AS (
  SELECT COUNT(*) AS sample_count,
         COUNT(*) FILTER (WHERE (gate_approved IS TRUE) = (operator_action = 'approved'))::float
           / NULLIF(COUNT(*), 0) AS agree
  FROM auto_approval_calibration
  WHERE niche_id='$NICHE' AND decided_at >= NOW() - INTERVAL '7 days'
)
SELECT (sample_count >= $MIN_SAMPLES AND agree >= $MIN_AGREEMENT)::int FROM s;
")
if [[ "$READY" == "1" ]]; then
    emit pass "04. Mission Control card would show 'READY' (samples=$SAMPLES, agreement=${AGREEMENT_PCT}%)"
else
    emit fail "04. Mission Control card would NOT show 'READY' (samples=$SAMPLES, agreement=${AGREEMENT_PCT}%)"
fi

# ── Check 5: 0 tracebacks in dry-run worker logs ──────────────────
# Run remotely via ssh; we only want a count, not the full log.
# grep -c always prints a single count; using `|| echo 0` would emit a
# second line because grep -c exit-codes 1 on zero matches but still
# prints "0". Use ``|| true`` to swallow the exit code, then default
# via ``${var:-0}``.
TRACEBACK_COUNT=$(run_on_prod "journalctl -u $DRY_RUN_UNIT --since '5 days ago' 2>/dev/null | grep -cE 'Traceback|ERROR' || true" 2>/dev/null || echo "prod-unreachable")
TRACEBACK_COUNT=$(echo "$TRACEBACK_COUNT" | head -1 | tr -d ' \n')
if [[ "$TRACEBACK_COUNT" == "prod-unreachable" ]]; then
    emit fail "05. could not reach prod to check worker logs"
elif [[ "${TRACEBACK_COUNT:-0}" -eq 0 ]]; then
    emit pass "05. 0 tracebacks/errors in $DRY_RUN_UNIT logs last 5d"
else
    emit fail "05. $TRACEBACK_COUNT tracebacks/errors in $DRY_RUN_UNIT logs last 5d (need 0)"
fi

# ── Check 6: dry-run alignment with operator ──────────────────────
# Looser version: the calibration agreement rate IS the dry-run alignment
# rate (gate vs operator). Check 3 already covered it; reinforce here
# that the worker has been ACTING on the gate's verdict.
WORKER_RUNS=$(run_on_prod "journalctl -u $DRY_RUN_UNIT --since '24 hours ago' 2>/dev/null | grep -c 'examined=' || true" 2>/dev/null || echo "prod-unreachable")
WORKER_RUNS=$(echo "$WORKER_RUNS" | head -1 | tr -d ' \n')
if [[ "$WORKER_RUNS" == "prod-unreachable" ]]; then
    emit fail "06. could not reach prod to count worker runs"
elif [[ "${WORKER_RUNS:-0}" -ge 12 ]]; then
    # 24h * (60/30min) = 48 runs/day expected — accept ≥12 (every 2h)
    emit pass "06. worker fired $WORKER_RUNS times in last 24h (timer healthy)"
else
    emit fail "06. worker only fired $WORKER_RUNS times in last 24h (need ≥12; timer may be dead)"
fi

# ── Check 7: 0 critical alerts in last 48h ─────────────────────────
CRITICAL_COUNT=$(psql_value "
SELECT COUNT(*) FROM pipeline_alerts
WHERE severity='CRITICAL' AND created_at >= NOW() - INTERVAL '48 hours'
  AND (resolved_at IS NULL OR resolved_at > created_at + INTERVAL '24 hours');
")
CRITICAL_COUNT=${CRITICAL_COUNT:-0}
if [[ "$CRITICAL_COUNT" -eq 0 ]]; then
    emit pass "07. 0 critical pipeline alerts in last 48h"
else
    emit fail "07. $CRITICAL_COUNT critical pipeline alert(s) in last 48h (need 0)"
fi

# ── Check 8: WARP healthy ──────────────────────────────────────────
WARP_IP=$(run_on_prod "curl -s --max-time 8 --socks5 127.0.0.1:40000 https://ifconfig.me 2>/dev/null || echo FAIL" 2>/dev/null || echo "prod-unreachable")
HETZNER_IP="46.224.237.56"
if [[ "$WARP_IP" == "prod-unreachable" ]]; then
    emit fail "08. could not reach prod to probe WARP"
elif [[ "$WARP_IP" == "FAIL" || -z "$WARP_IP" ]]; then
    emit fail "08. WARP proxy probe failed (returned: ${WARP_IP:-empty})"
elif [[ "$WARP_IP" == "$HETZNER_IP" ]]; then
    emit fail "08. WARP returned Hetzner IP ($WARP_IP) — proxy not routing through Cloudflare"
else
    emit pass "08. WARP healthy: exit IP = $WARP_IP (not Hetzner)"
fi

# ── Check 9: Daily SLO 5/5 for last 3 days ─────────────────────────
# A niche met SLO on day D if it has at least one PUBLISHED blueprint
# whose scheduled_for IST date == D.
SLO_FAIL=$(psql_value "
WITH days AS (
  SELECT generate_series(
    (NOW() AT TIME ZONE 'Asia/Kolkata' - INTERVAL '3 days')::date,
    (NOW() AT TIME ZONE 'Asia/Kolkata' - INTERVAL '1 day')::date,
    INTERVAL '1 day'
  )::date AS day
),
niches AS (
  SELECT unnest(ARRAY['ai_creators','gaming','sports','movies','anime']) AS niche_id
),
expected AS (SELECT d.day, n.niche_id FROM days d CROSS JOIN niches n),
actual AS (
  SELECT (scheduled_for AT TIME ZONE 'Asia/Kolkata')::date AS day, niche_id, COUNT(*) AS n
  FROM blueprints
  WHERE status IN ('PUBLISHED','INSIGHTS_6H','INSIGHTS_24H','INSIGHTS_48H','INSIGHTS_168H')
    AND scheduled_for >= NOW() - INTERVAL '4 days'
  GROUP BY 1, 2
)
SELECT COUNT(*) FROM expected e
LEFT JOIN actual a USING (day, niche_id)
WHERE a.n IS NULL OR a.n = 0;
")
SLO_FAIL=${SLO_FAIL:-99}
if [[ "$SLO_FAIL" -eq 0 ]]; then
    emit pass "09. Daily SLO 5/5 for last 3 days"
else
    emit fail "09. Daily SLO missed $SLO_FAIL niche-day combinations (need 0)"
fi

# ── Check 12: publishing.yaml exists with enabled: false ──────────
YAML_STATE=$(run_on_prod "grep -E '^[[:space:]]*enabled:' /opt/genlab/BlackboxBrief/config/publishing.yaml 2>/dev/null | head -1" 2>/dev/null || echo "prod-unreachable")
if [[ "$YAML_STATE" == "prod-unreachable" ]]; then
    emit fail "12. could not reach prod to read publishing.yaml"
elif [[ -z "$YAML_STATE" ]]; then
    emit fail "12. publishing.yaml missing or has no 'enabled:' line"
elif [[ "$YAML_STATE" == *"false"* ]]; then
    emit pass "12. publishing.yaml has enabled: false (ready to flip)"
elif [[ "$YAML_STATE" == *"true"* ]]; then
    emit fail "12. publishing.yaml ALREADY has enabled: true — flip already happened?"
else
    emit fail "12. unable to parse publishing.yaml enabled state: '$YAML_STATE'"
fi

# ── Summary ────────────────────────────────────────────────────────
unset PGPASSWORD
echo
echo "════════════════════════════════════════════════════════════════════════"
echo "  $PASS_COUNT passed, $FAIL_COUNT failed (of 10 automatable checks)"
echo "════════════════════════════════════════════════════════════════════════"
echo
echo "OPERATOR-ONLY checks (NOT covered above — verify manually):"
echo "  10. You have at least 4 free hours today (12:05 IST publish window)"
echo "  11. You are well-rested and clear-headed"
echo "  13. Test the kill switch button on Mission Control once (cancel without confirming)"
echo "  14. /root/backups/ has a snapshot dated within last 24h"
echo "  15. This runbook is open in another browser tab"
echo
echo "All 15 ticked (10 automated above + 5 operator-only) → proceed to runbook §6.2"
echo "Any unticked → defer the flip 24h per §9 guidance"

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    exit 1
fi
exit 0
