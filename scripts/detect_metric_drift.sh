#!/usr/bin/env bash
# ============================================================================
# detect_metric_drift.sh — Nightly drift detector for engagement_rate
#
# Why this exists
# ---------------
# The engagement_rate=0 bug (analytics_store.py:214) shipped in commit
# f32b6189 on 2026-06-09 and went undetected for 17 days because NO
# automated check was watching the distribution shape of the headline
# bandit-reward metric. When a load-bearing metric structurally goes
# to zero (or its variance collapses, or its mean cliffs), the bandit
# silently trains on garbage and downstream Top Performers / hook
# classifier / monetisation readiness all rot together.
#
# `docs/AGENT-AUTONOMY-RESEARCH.md` Tier 1 Move #2 calls this out as
# "the single highest-leverage observability addition possible given
# today's state." This script is the prevention layer.
#
# What it does
# ------------
# Per niche (gaming / sports / movies / anime / ai_creators):
#   1. Computes recent-7d vs baseline-30d (excluding last 7d) stats
#      on analytics.extra->>'engagement_rate' (composite rows, reach>0)
#   2. Fires WARN to stderr if any of 4 anomaly triggers match
#   3. journalctl tags WARN with priority=warning (logger -p user.warning),
#      so it surfaces in the operator's morning IST review window
#
# Triggers (any one fires WARN — drift IS the signal, not failure)
# ----------------------------------------------------------------
#   1. publishing collapsed:    recent_n < 5 AND baseline_n >= 5
#   2. all-zero engagement:     recent_zeros >= 0.5 * recent_n AND
#                               baseline_zeros < 0.5 * baseline_n
#                               (the exact pattern of the original bug)
#   3. mean collapsed:          recent_mean < 0.1 * baseline_mean AND
#                               baseline_mean > 0
#   4. variance vanished:       recent_stddev = 0 AND baseline_stddev > 0
#
# Exit code
# ---------
#   0 — ALWAYS. Drift detection is informational; alert IS the success
#       criterion, not failure. systemd should not flag this unit as
#       failed when an alert fires.
# ============================================================================
set -uo pipefail

GENLAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_FILE="$GENLAB/.logs/detect_metric_drift.log"

NICHES=(gaming sports movies anime ai_creators)

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true

if [[ -f "$GENLAB/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$GENLAB/.env"
    set +a
fi

log_info() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

# WARN goes to stderr with "WARN:" prefix so journalctl picks it up at
# warning priority. The morning compliance digest / Slack notifier
# (when wired) greps for this prefix to forward operator-visible alerts.
log_warn() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] WARN: $*"
    echo "$msg" >&2
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

if [[ -z "${DATABASE_URL:-}" ]]; then
    log_info "SKIP: DATABASE_URL not set (dev/CI environment)"
    exit 0
fi
if ! command -v psql >/dev/null 2>&1; then
    log_info "SKIP: psql not on PATH"
    exit 0
fi

DB_HOST=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_PORT=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
DB_NAME=$(echo "$DATABASE_URL" | sed -n 's|.*/\([^?]*\).*|\1|p')
DB_USER=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')
export PGPASSWORD=$(echo "$DATABASE_URL" | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')

run_query_for_niche() {
    local niche="$1"
    # 2026-07-14: dropped `--csv=off` — invalid in PostgreSQL 18 (prod's
    # version). Under PG18, psql errors with "option '--csv' doesn't
    # allow an argument" before running the query; the `2>/dev/null`
    # below swallowed the error and the empty stdout triggered "no
    # data (skipping)" for all 5 niches. Analytics had 2184 composite
    # rows but the drift detector was blind to them for weeks. `--csv`
    # defaults to off; dropping the flag is a no-op on older versions
    # that accepted the argument form.
    psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        -X -A -t -F '|' -v ON_ERROR_STOP=1 \
        -v niche="$niche" <<'SQL' 2>/dev/null
WITH recent AS (
  SELECT (extra->>'engagement_rate')::numeric AS rate
  FROM analytics
  WHERE metric_type = 'composite'
    AND niche_id = :'niche'
    AND collected_at >= NOW() - INTERVAL '7 days'
    AND COALESCE((extra->>'reach')::numeric, 0) > 0
),
baseline AS (
  SELECT (extra->>'engagement_rate')::numeric AS rate
  FROM analytics
  WHERE metric_type = 'composite'
    AND niche_id = :'niche'
    AND collected_at BETWEEN NOW() - INTERVAL '37 days' AND NOW() - INTERVAL '7 days'
    AND COALESCE((extra->>'reach')::numeric, 0) > 0
)
SELECT
    (SELECT COUNT(*) FROM recent) AS recent_n,
    (SELECT COUNT(*) FROM baseline) AS baseline_n,
    (SELECT COUNT(*) FILTER (WHERE rate = 0) FROM recent) AS recent_zeros,
    (SELECT COUNT(*) FILTER (WHERE rate = 0) FROM baseline) AS baseline_zeros,
    COALESCE((SELECT AVG(rate) FROM recent), 0) AS recent_mean,
    COALESCE((SELECT AVG(rate) FROM baseline), 0) AS baseline_mean,
    COALESCE((SELECT STDDEV(rate) FROM recent), 0) AS recent_stddev,
    COALESCE((SELECT STDDEV(rate) FROM baseline), 0) AS baseline_stddev;
SQL
}

# Floating-point comparison helper. Returns 0 if $1 OP $2 holds, 1 otherwise.
# Uses awk to avoid bash's integer-only arithmetic.
fcmp() {
    awk -v a="$1" -v op="$2" -v b="$3" 'BEGIN{
        if (op=="<") exit !(a < b);
        if (op=="<=") exit !(a <= b);
        if (op==">") exit !(a > b);
        if (op==">=") exit !(a >= b);
        if (op=="==") exit !(a == b);
        exit 1;
    }'
}

log_info "Starting drift scan for ${#NICHES[@]} niches"

for niche in "${NICHES[@]}"; do
    row=$(run_query_for_niche "$niche" | tr -d ' ')
    if [[ -z "$row" ]]; then
        log_info "niche=$niche no data (skipping)"
        continue
    fi

    # Row format: recent_n|baseline_n|recent_zeros|baseline_zeros|recent_mean|baseline_mean|recent_stddev|baseline_stddev
    IFS='|' read -r recent_n baseline_n recent_zeros baseline_zeros \
        recent_mean baseline_mean recent_stddev baseline_stddev <<< "$row"

    # Default missing/empty values to 0 — defensive against NULL-from-empty-aggregate
    recent_n=${recent_n:-0}
    baseline_n=${baseline_n:-0}
    recent_zeros=${recent_zeros:-0}
    baseline_zeros=${baseline_zeros:-0}
    recent_mean=${recent_mean:-0}
    baseline_mean=${baseline_mean:-0}
    recent_stddev=${recent_stddev:-0}
    baseline_stddev=${baseline_stddev:-0}

    log_info "niche=$niche recent_n=$recent_n baseline_n=$baseline_n recent_mean=$recent_mean baseline_mean=$baseline_mean"

    # ── Trigger 1: publishing collapsed ──
    if [[ "$recent_n" -lt 5 ]] && [[ "$baseline_n" -ge 5 ]]; then
        log_warn "[drift] niche=$niche reason=publishing_collapsed recent_n=$recent_n baseline_n=$baseline_n recent_mean=$recent_mean baseline_mean=$baseline_mean"
    fi

    # ── Trigger 2: engagement_rate suddenly all-zero (the original bug pattern) ──
    # Require recent_n > 0 to avoid division when recent is empty (covered by trigger 1).
    if [[ "$recent_n" -gt 0 ]] && [[ "$baseline_n" -gt 0 ]]; then
        recent_zero_ratio=$(awk -v z="$recent_zeros" -v n="$recent_n" 'BEGIN{printf "%.4f", z/n}')
        baseline_zero_ratio=$(awk -v z="$baseline_zeros" -v n="$baseline_n" 'BEGIN{printf "%.4f", z/n}')
        if fcmp "$recent_zero_ratio" ">=" "0.5" && fcmp "$baseline_zero_ratio" "<" "0.5"; then
            log_warn "[drift] niche=$niche reason=all_zero_engagement recent_zero_ratio=$recent_zero_ratio baseline_zero_ratio=$baseline_zero_ratio recent_mean=$recent_mean baseline_mean=$baseline_mean"
        fi
    fi

    # ── Trigger 3: mean collapsed (recent < 10% of baseline) ──
    if fcmp "$baseline_mean" ">" "0"; then
        threshold=$(awk -v b="$baseline_mean" 'BEGIN{printf "%.6f", b * 0.1}')
        if fcmp "$recent_mean" "<" "$threshold"; then
            log_warn "[drift] niche=$niche reason=mean_collapsed recent_mean=$recent_mean baseline_mean=$baseline_mean (recent < 10% of baseline)"
        fi
    fi

    # ── Trigger 4: variance vanished ──
    if fcmp "$recent_stddev" "==" "0" && fcmp "$baseline_stddev" ">" "0"; then
        log_warn "[drift] niche=$niche reason=variance_vanished recent_stddev=$recent_stddev baseline_stddev=$baseline_stddev recent_mean=$recent_mean baseline_mean=$baseline_mean"
    fi
done

log_info "Drift scan complete"
unset PGPASSWORD
exit 0
