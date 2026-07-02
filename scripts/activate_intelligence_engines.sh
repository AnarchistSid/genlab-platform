#!/usr/bin/env bash
# activate_intelligence_engines.sh (2026-07-02)
#
# One-shot activator for the intelligence engines shipped in the
# 2026-07-01→02 sprint. Sets every intelligence-related flag in
# ``/opt/genlab/.env`` to ``true``, verifies the write, and restarts
# the services that need to see the new state.
#
# Run this on the Hetzner VPS as the operator, NOT locally:
#   ssh operator@genlab.aspirehub.ai
#   sudo -u genlab bash /opt/genlab/scripts/activate_intelligence_engines.sh
#
# Idempotent — safe to re-run. Backs up the current .env first.
# Prints a summary + suggests the follow-up systemctl commands.
#
# Exit codes:
#   0 — success (all flags flipped, services restarted)
#   1 — .env not found at expected path
#   2 — user cancelled after review
#   3 — service restart failed (flags flipped, but consumers may
#       still be on the old env — investigate journalctl)

set -euo pipefail

ENV_FILE="${GENLAB_ENV_FILE:-/opt/genlab/.env}"
BACKUP_DIR="/opt/genlab/.backups"
DRY_RUN="${DRY_RUN:-0}"

# ── The flag matrix ─────────────────────────────────────────────
# Ordered by rollout-safety: safest first, most-load-bearing last.
# Each row: FLAG_NAME | intervention | validation card | risk
declare -a FLAGS=(
  # Intervention 10 gap-fill — percentile targets already wired; no
  # runtime flag beyond the module-level fn instantiation.

  # Cross-niche transfer (Wire 1) — cold-start-only, existing arms
  # unaffected. Lowest risk.
  "GENLAB_CROSS_NICHE_TRANSFER_ENABLED"

  # DR estimator (Wire 3) — runner-side flag; when on, monthly
  # replay populates real dr_reward fields instead of null stubs.
  # Strategist prompt reads whatever the runner writes.
  "GENLAB_COUNTERFACTUAL_REPLAY_ENABLED"

  # Multi-window reward re-eval — daily runner rewards material
  # 48h→7d deltas above 20% threshold.
  "GENLAB_MULTI_WINDOW_REWARD_ENABLED"

  # Ensemble decision — populates the ``ensemble`` payload on the
  # auto-approval-preview endpoint. Frontend EnsembleBadge renders
  # accordingly. Doesn't yet gate publishing decisions runtime.
  "GENLAB_ENSEMBLE_DECISION_ENABLED"

  # Anticipation accuracy runner — weekly Spearman validation
  # writes to $GENLAB_TMP/anticipation-accuracy/.
  "GENLAB_ANTICIPATION_ACCURACY_ENABLED"

  # Anticipation paid signals — needed for the composite score to
  # improve past search-velocity-only.
  "GENLAB_ANTICIPATION_YT_ENABLED"
  "GENLAB_ANTICIPATION_REDDIT_ENABLED"
  "GENLAB_ANTICIPATION_NEWS_ENABLED"

  # Trend anticipation steering (Wire 2) — actually reorders
  # pipeline candidates by anticipation composite_score. Highest
  # runtime impact of the wires.
  "GENLAB_TREND_ANTICIPATION_ENABLED"

  # LLM-as-judge — ~$0.10/day cost, lifts gaming-niche agreement.
  "GENLAB_LLM_JUDGE_ENABLED"

  # Bayesian gate + conformal router — additive quality signals
  # for the auto-approval cascade.
  "GENLAB_BAYESIAN_GATE_ENABLED"
  "GENLAB_CONFORMAL_ROUTER_ENABLED"
)

# ── Services to restart after flag flip ─────────────────────────
# Each service's env is re-read on start; systemctl restart is
# necessary for the new flag values to take effect.
declare -a SERVICES=(
  "genlab-strategist.service"          # picks up DR flag + Strategist prompt wire
  "genlab-review-server.service"       # picks up ensemble flag for preview endpoint
  # Note: publisher + pipeline runs are triggered by their own
  # timers/schedules; each will pick up the new env on next fire.
)

# ── Helpers ─────────────────────────────────────────────────────

log()   { printf "\033[36m[activate]\033[0m %s\n" "$*"; }
warn()  { printf "\033[33m[activate WARN]\033[0m %s\n" "$*" >&2; }
error() { printf "\033[31m[activate ERROR]\033[0m %s\n" "$*" >&2; }

ensure_env_exists() {
  if [[ ! -f "$ENV_FILE" ]]; then
    error "$ENV_FILE not found. Set GENLAB_ENV_FILE if using a non-default path."
    exit 1
  fi
}

backup_env() {
  mkdir -p "$BACKUP_DIR"
  local stamp
  stamp=$(date -u +%Y%m%d-%H%M%S)
  local target="$BACKUP_DIR/env.pre-activate-$stamp"
  cp "$ENV_FILE" "$target"
  log "Backed up $ENV_FILE → $target"
}

flip_flag() {
  # Args: flag_name
  # If the flag exists in .env, replaces its value with 'true'.
  # If not present, appends it.
  local flag="$1"
  if grep -qE "^${flag}=" "$ENV_FILE"; then
    if [[ "$DRY_RUN" == "1" ]]; then
      log "[dry-run] Would flip existing $flag to true"
    else
      # Portable sed — uses a suffix that's OS-independent.
      sed -i.tmp "s|^${flag}=.*|${flag}=true|" "$ENV_FILE"
      rm -f "${ENV_FILE}.tmp"
    fi
  else
    if [[ "$DRY_RUN" == "1" ]]; then
      log "[dry-run] Would append $flag=true"
    else
      printf "\n%s=true\n" "$flag" >> "$ENV_FILE"
    fi
  fi
}

restart_service() {
  local svc="$1"
  if [[ "$DRY_RUN" == "1" ]]; then
    log "[dry-run] Would restart $svc"
    return 0
  fi
  if systemctl list-unit-files --type=service --state=enabled "$svc" &>/dev/null; then
    log "Restarting $svc"
    sudo systemctl restart "$svc" || {
      warn "Failed to restart $svc — check 'journalctl -u $svc' and re-run manually"
      return 1
    }
  else
    warn "$svc is not enabled/installed on this host — skipping restart"
  fi
}

confirm() {
  cat <<-EOF

	This script will flip the following flags to true in
	$ENV_FILE:

$(for f in "${FLAGS[@]}"; do printf "  * %s\n" "$f"; done)

	And restart the following services:

$(for s in "${SERVICES[@]}"; do printf "  * %s\n" "$s"; done)

	Consumer-wire effects that activate:
	  * Wire 1 — new bandit_arms rows inherit cross-niche moment-
	    matched Beta priors instead of Beta(1,1). Existing arms
	    unaffected.
	  * Wire 2 — pipeline candidate lists reorder by anticipation
	    composite_score (highest-first). Google Trends order
	    preserved for un-anticipated topics.
	  * Wire 3 — Strategist prompts include a COUNTERFACTUAL
	    REPLAY section with top-DR arms. Runner produces real
	    DR values instead of null stubs.

	Rollback: re-run with each flag set to false, or delete the
	relevant ``FLAG=true`` lines from $ENV_FILE + restart services.

	Continue? (y/N)
EOF
  read -r -p "> " answer
  if [[ ! "$answer" =~ ^[Yy]$ ]]; then
    log "Cancelled."
    exit 2
  fi
}

# ── Main ────────────────────────────────────────────────────────

log "activate_intelligence_engines starting"
log "ENV_FILE=$ENV_FILE"
log "DRY_RUN=$DRY_RUN"

ensure_env_exists

if [[ "$DRY_RUN" != "1" ]]; then
  confirm
  backup_env
fi

log "Flipping ${#FLAGS[@]} flags to true..."
for flag in "${FLAGS[@]}"; do
  flip_flag "$flag"
done

if [[ "$DRY_RUN" != "1" ]]; then
  log "Verifying flag state:"
  for flag in "${FLAGS[@]}"; do
    value=$(grep -E "^${flag}=" "$ENV_FILE" | tail -1 | cut -d= -f2)
    if [[ "$value" == "true" ]]; then
      printf "  \033[32m✓\033[0m %s=%s\n" "$flag" "$value"
    else
      printf "  \033[31m✗\033[0m %s=%s (expected true)\n" "$flag" "$value"
    fi
  done
fi

log "Restarting services to pick up new env..."
restart_ok=0
for svc in "${SERVICES[@]}"; do
  restart_service "$svc" || restart_ok=1
done

log "Activation complete."
cat <<-EOF

Follow-up:
  * Watch Mission Control cards over the next 1-2 hours — the
    "active" vs "observation only" badge should flip for each
    engine when it next fires.
  * The systemd timers below will pick up the new env on their
    NEXT scheduled run (not immediately):
      - genlab-cross-niche-transfer.timer  (Mon 05:30 UTC)
      - genlab-anticipate-trends.timer     (Daily 03:30 UTC)
      - genlab-anticipation-accuracy.timer (Mon 05:00 UTC)
      - genlab-counterfactual-replay.timer (1st of month 04:30 UTC)
      - genlab-late-reward.timer           (Daily 04:00 UTC)
      - genlab-strategist.timer            (Sun 02:00 UTC)
  * If you want to test a runner immediately:
      sudo systemctl start genlab-anticipate-trends.service
  * Follow the "observability before consumer wire" discipline —
    if a card shows unexpected values within 24h, revert the
    corresponding flag by setting it back to false + restart the
    same services.
EOF

if [[ "$restart_ok" -ne 0 ]]; then
  error "One or more services failed to restart; investigate before assuming full activation."
  exit 3
fi

exit 0
