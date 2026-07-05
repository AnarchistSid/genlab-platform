#!/usr/bin/env bash
# activate_intelligence_engines.sh (2026-07-02, phased)
#
# One-shot activator for the intelligence engines shipped in the
# 2026-07-01→02 sprint. Sets a subset of intelligence-related flags
# in ``/opt/genlab/.env`` to ``true``, verifies the write, and restarts
# the services that need to see the new state.
#
# Run this on the Hetzner VPS as the operator, NOT locally:
#   ssh operator@genlab.aspirehub.ai
#   sudo -u genlab PHASE=1 bash /opt/genlab/scripts/activate_intelligence_engines.sh
#
# Idempotent — safe to re-run. Backs up the current .env first.
# Prints a summary + suggests the follow-up systemctl commands.
#
# Phased rollout (recommended):
#
#   PHASE=1  Observability — no runtime steering, only cards populate.
#            Flags: cross-niche transfer, DR replay runner, ensemble
#            preview payload, anticipation accuracy runner, 3 paid
#            anticipation signals (YT/Reddit/News).
#
#   PHASE=2  Quality boost — LLM judge + per-niche Bayesian gate +
#            multi-window reward push. Requires PHASE=1 flags already
#            active. Costs ~$0.10/day.
#
#   PHASE=3  Anticipation steering — pipeline reorders candidates by
#            anticipated peak. Requires TrendAnticipationAccuracyCard
#            to show Spearman r ≥ 0.4 sustained 2+ weeks per niche.
#            Use FORCE=1 to bypass the "no metrics yet" check.
#
#   PHASE=4  Conformal router — operator queue routing. Requires
#            PHASE=2 flags active for ≥1 week so confidence signals
#            are trustworthy.
#
#   PHASE=all  All PHASE=1..4 flags at once. Same behaviour as the
#              pre-phased 2026-07-02 activator. Not recommended for
#              first activation — you lose the observation window.
#
# FLAGS_BLOCKED is kept as a defence-in-depth framework — populated
# only if a real, verified schema/runtime dependency blocks activation.
# The framework guards flip_flag() from ever mutating a listed flag
# even if it accidentally landed in a phase array. Currently empty.
#
# Exit codes:
#   0 — success (all flags flipped, services restarted)
#   1 — .env not found at expected path
#   2 — user cancelled after review
#   3 — service restart failed (flags flipped, but consumers may
#       still be on the old env — investigate journalctl)
#   4 — pre-flight failed (earlier phase not yet active; use FORCE=1
#       to bypass or run the earlier phase first)

set -euo pipefail

ENV_FILE="${GENLAB_ENV_FILE:-/opt/genlab/.env}"
BACKUP_DIR="/opt/genlab/.backups"
DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"
PHASE="${PHASE:-}"

# ── The phased flag matrix ──────────────────────────────────────
# Each phase groups flags by rollout risk. Phase N cannot activate
# unless Phase 1..N-1 flags are already true in .env (bypass with
# FORCE=1). This is the observation-before-steering discipline.

# Phase 1 — Observability. Zero runtime-steering risk. Cards populate.
declare -a FLAGS_PHASE_1=(
  # Cross-niche transfer (Wire 1) — cold-start-only, existing arms
  # unaffected. New bandit_arms inherit moment-matched Beta priors.
  "GENLAB_CROSS_NICHE_TRANSFER_ENABLED"

  # DR estimator (Wire 3) — monthly replay populates real dr_reward
  # fields instead of null stubs. Strategist prompt reads them.
  "GENLAB_COUNTERFACTUAL_REPLAY_ENABLED"

  # Ensemble decision — populates the ``ensemble`` payload on the
  # auto-approval-preview endpoint. Frontend EnsembleBadge renders
  # accordingly. Does NOT gate publishing.
  "GENLAB_ENSEMBLE_DECISION_ENABLED"

  # Anticipation accuracy — weekly Spearman validation runner writes
  # to $GENLAB_TMP/anticipation-accuracy/. Gates PHASE=3 activation.
  "GENLAB_ANTICIPATION_ACCURACY_ENABLED"

  # Anticipation paid signals — needed for the composite score to
  # improve past search-velocity-only. Watch YouTube quota.
  "GENLAB_ANTICIPATION_YT_ENABLED"
  "GENLAB_ANTICIPATION_REDDIT_ENABLED"
  "GENLAB_ANTICIPATION_NEWS_ENABLED"
)

# Phase 2 — Quality boost. Modifies gate decisions + bandit updates.
declare -a FLAGS_PHASE_2=(
  # LLM-as-judge — ~$0.10/day cost. Immediate ~85% agreement lift on
  # gaming niche (currently at 53.4%). Fails open if API is down.
  "GENLAB_LLM_JUDGE_ENABLED"

  # Bayesian gate — per-niche posterior gate. Requires 50+
  # calibration observations per niche to activate; falls back to
  # heuristic otherwise. Current calibration per niche:
  #   ai_creators=38, gaming=116, anime=59, movies=56, sports=36
  "GENLAB_BAYESIAN_GATE_ENABLED"

  # Multi-window reward — daily late_reward.py runner has been
  # writing telemetry to late_reward_deltas since it shipped.
  # Flipping this flag turns on the bandit-push side: arms with
  # |delta_pct| > 20% at 7-day window get incremental Beta updates
  # in bandit_arms.alpha/beta. No schema migration needed — the
  # audit table is created lazily via CREATE TABLE IF NOT EXISTS
  # on first runner fire; bandit_arms.alpha/beta already exist.
  "GENLAB_MULTI_WINDOW_REWARD_ENABLED"
)

# Phase 3 — Anticipation steering. Real pipeline behavior change.
declare -a FLAGS_PHASE_3=(
  # Trend anticipation steering (Wire 2) — TrendingVideoFetcher
  # reorders extra_keywords by anticipation composite_score. Publish
  # 6-24h ahead of peak instead of at peak.
  # PRE-REQ: TrendAnticipationAccuracyCard must show Spearman r ≥ 0.4
  # sustained ≥2 weeks per niche. Use FORCE=1 to bypass.
  "GENLAB_TREND_ANTICIPATION_ENABLED"
)

# Phase 4 — Operator queue routing.
declare -a FLAGS_PHASE_4=(
  # Conformal router — routes low-confidence decisions to operator
  # queue. Expected ~25% queue shrink. Highest gate-modification risk.
  # PRE-REQ: Bayesian gate active ≥1 week so confidence signals learned.
  "GENLAB_CONFORMAL_ROUTER_ENABLED"
)

# BLOCKED — defence-in-depth framework, currently empty. See file
# header. Populate only when a verified schema/runtime dependency
# would break the runner on flag flip.
declare -a FLAGS_BLOCKED=()

# ── Services to restart after flag flip ─────────────────────────
# Each service's env is re-read on start; systemctl restart is
# necessary for the new flag values to take effect.
declare -a SERVICES=(
  "genlab-strategist.service"          # picks up DR flag + Strategist prompt wire
  "genlab-review-server.service"       # picks up ensemble/LLM-judge/gate flags for preview endpoint
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

ensure_phase_valid() {
  case "$PHASE" in
    1|2|3|4|all) : ;;
    "")
      error "PHASE env var required. Use PHASE=1|2|3|4|all."
      error "See file header for phase descriptions."
      exit 4
      ;;
    *)
      error "Invalid PHASE=$PHASE. Use 1|2|3|4|all."
      exit 4
      ;;
  esac
}

# Return the flag list for a given phase name.
flags_for_phase() {
  case "$1" in
    1) printf "%s\n" "${FLAGS_PHASE_1[@]}" ;;
    2) printf "%s\n" "${FLAGS_PHASE_2[@]}" ;;
    3) printf "%s\n" "${FLAGS_PHASE_3[@]}" ;;
    4) printf "%s\n" "${FLAGS_PHASE_4[@]}" ;;
    all)
      printf "%s\n" "${FLAGS_PHASE_1[@]}"
      printf "%s\n" "${FLAGS_PHASE_2[@]}"
      printf "%s\n" "${FLAGS_PHASE_3[@]}"
      printf "%s\n" "${FLAGS_PHASE_4[@]}"
      ;;
  esac
}

# Check that every flag in the given phase is currently set to 'true'
# in .env. Prints missing flags. Returns 0 if all present, 1 if not.
phase_is_active() {
  local phase="$1"
  local missing=()
  local flag
  while IFS= read -r flag; do
    [[ -z "$flag" ]] && continue
    local value
    # sed -n instead of grep to avoid pipefail=1 on no-match
    value=$(sed -n "s|^${flag}=||p" "$ENV_FILE" 2>/dev/null | tail -1 | tr -d '[:space:]')
    if [[ "$value" != "true" ]]; then
      missing+=("$flag=${value:-<unset>}")
    fi
  done < <(flags_for_phase "$phase")

  if [[ ${#missing[@]} -eq 0 ]]; then
    return 0
  fi

  printf "\n"
  for m in "${missing[@]}"; do
    printf "  * %s\n" "$m"
  done
  return 1
}

# Pre-flight — later phases require earlier phases to be active
# unless FORCE=1 is set. Prints what's missing and exits 4 if so.
preflight_check() {
  case "$PHASE" in
    1|all)
      # No prerequisite phases.
      return 0
      ;;
    2)
      log "Pre-flight: checking PHASE=1 flags..."
      if ! phase_is_active 1; then
        error "PHASE=1 flags not fully active. Missing:"
        phase_is_active 1 || true
        [[ "$FORCE" == "1" ]] || {
          error "Run PHASE=1 first, or use FORCE=1 to bypass."
          exit 4
        }
        warn "FORCE=1 set — proceeding despite missing PHASE=1 flags."
      fi
      ;;
    3)
      log "Pre-flight: checking PHASE=1 flags..."
      if ! phase_is_active 1; then
        error "PHASE=1 flags not fully active. Missing:"
        phase_is_active 1 || true
        [[ "$FORCE" == "1" ]] || {
          error "Run PHASE=1 first, or use FORCE=1 to bypass."
          exit 4
        }
        warn "FORCE=1 set — proceeding despite missing PHASE=1 flags."
      fi
      warn "PHASE=3 activates anticipation STEERING (pipeline reorders)."
      warn "Verify TrendAnticipationAccuracyCard shows Spearman r ≥ 0.4"
      warn "sustained ≥2 weeks per niche. Otherwise pipeline may steer"
      warn "toward WORSE candidates than baseline."
      if [[ "$FORCE" != "1" ]]; then
        printf "  Have you verified the accuracy card is ready? (y/N) > "
        read -r ready
        [[ "$ready" =~ ^[Yy]$ ]] || {
          error "Cancelled — verify the accuracy card, then re-run."
          exit 4
        }
      fi
      ;;
    4)
      log "Pre-flight: checking PHASE=1..2 flags..."
      local fail=0
      if ! phase_is_active 1; then
        error "PHASE=1 flags not fully active."
        phase_is_active 1 || true
        fail=1
      fi
      if ! phase_is_active 2; then
        error "PHASE=2 flags not fully active."
        phase_is_active 2 || true
        fail=1
      fi
      if [[ "$fail" -eq 1 ]]; then
        [[ "$FORCE" == "1" ]] || {
          error "Run PHASE=1 + PHASE=2 first, or use FORCE=1 to bypass."
          exit 4
        }
        warn "FORCE=1 set — proceeding despite missing prerequisites."
      fi
      warn "PHASE=4 activates conformal router — highest gate-modification"
      warn "risk in the activator. Watch operator queue depth over 3 days."
      warn "If depth doubles or halves outside [0.5×, 1.5×] baseline for"
      warn "24h, revert immediately."
      ;;
  esac
}

# Warn if a BLOCKED flag is currently true in .env — someone flipped it
# manually. We do not flip it back to false (that could break something
# that depends on the schema being present), just surface it.
check_blocked_flags() {
  # ${FLAGS_BLOCKED[@]+"${FLAGS_BLOCKED[@]}"} expands to nothing when
  # the array is empty — required under `set -u`, which errors on
  # naked ${empty_array[@]} in bash pre-4.4.
  for flag in ${FLAGS_BLOCKED[@]+"${FLAGS_BLOCKED[@]}"}; do
    local value
    value=$(sed -n "s|^${flag}=||p" "$ENV_FILE" 2>/dev/null | tail -1 | tr -d '[:space:]')
    if [[ "$value" == "true" ]]; then
      warn "$flag=true in .env but this flag is BLOCKED — requires"
      warn "  schema migration (reward_6h/24h/168h columns on pending_feedback)."
      warn "  Verify late_reward.py runner isn't erroring. Consider setting"
      warn "  back to false until the migration lands."
    fi
  done
}

backup_env() {
  mkdir -p "$BACKUP_DIR"
  local stamp
  stamp=$(date -u +%Y%m%d-%H%M%S)
  local target="$BACKUP_DIR/env.pre-activate-phase${PHASE}-$stamp"
  cp "$ENV_FILE" "$target"
  log "Backed up $ENV_FILE → $target"
}

flip_flag() {
  # Args: flag_name
  # If the flag exists in .env, replaces its value with 'true'.
  # If not present, appends it.
  local flag="$1"

  # Guard against accidentally flipping a BLOCKED flag if it somehow
  # made it into a phase array (defense in depth). Empty-array guard
  # required under `set -u`; see check_blocked_flags() for the idiom.
  for blocked in ${FLAGS_BLOCKED[@]+"${FLAGS_BLOCKED[@]}"}; do
    if [[ "$flag" == "$blocked" ]]; then
      warn "Refusing to flip BLOCKED flag $flag — see file header."
      return 0
    fi
  done

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

# Effect summary printed in the confirm dialog — describes what the
# operator will observe over the next 24h for the chosen phase.
phase_effect_summary() {
  case "$PHASE" in
    1)
      cat <<-EFF
	  * Wire 1 — new bandit_arms rows inherit cross-niche moment-
	    matched Beta priors instead of Beta(1,1). Existing 67 arms
	    unaffected.
	  * Wire 3 — Strategist prompts include a COUNTERFACTUAL REPLAY
	    section with top-DR arms after the next monthly runner fires.
	  * EnsembleBadge renders on Focus/Bulk Review dashboards; may
	    surface ``worth_your_look`` verdicts for high-disagreement
	    blueprints. Does NOT gate publishing.
	  * TrendAnticipationCard adds YT/Reddit/News signals to composite.
	  * TrendAnticipationAccuracyCard flips from "no data" to Spearman
	    scores on next Monday 05:00 UTC run.
	  * No pipeline steering, no gate change. Purely observational.
EFF
      ;;
    2)
      cat <<-EFF
	  * LLM judge (~\$0.10/day) fires on borderline gate decisions
	    (rule-gate confidence 0.4-0.7). Gaming niche calibration
	    should climb from 53.4% → ~85% over 2-3 weeks.
	  * Bayesian gate activates per-niche posterior learning for
	    niches with ≥50 calibration observations (gaming, anime, movies).
	    ai_creators + sports fall back to heuristic threshold.
	  * Preview endpoint gate confidence becomes data-driven per niche.
	  * Multi-window reward: recompute_late_rewards.py (daily 04:00 UTC)
	    starts pushing bandit posterior updates for arms with |delta_pct|
	    > 20% at the 7-day window. Telemetry table late_reward_deltas
	    already populates regardless of flag; only the bandit push
	    activates. Watch bandit_arms.updated_at for delta events.
EFF
      ;;
    3)
      cat <<-EFF
	  * Wire 2 — TrendingVideoFetcher reorders extra_keywords by
	    anticipation composite_score. Pipeline publishes 6-24h AHEAD
	    of peak instead of AT peak.
	  * Reward_48h distribution over next 2 weeks is the test — if
	    running-mean drops, the anticipation model is steering wrong.
	    Revert by setting GENLAB_TREND_ANTICIPATION_ENABLED=false.
EFF
      ;;
    4)
      cat <<-EFF
	  * Conformal router routes low-confidence decisions to operator
	    queue instead of auto-approve/reject. Expected ~25% queue
	    shrink.
	  * Watch operator queue depth over 3 days. If outside
	    [0.5×, 1.5×] baseline for 24h, revert flag immediately.
EFF
      ;;
    all)
      cat <<-EFF
	  * ALL PHASES ACTIVATED SIMULTANEOUSLY. You lose the 24h-per-
	    phase observation window. Only use PHASE=all after each phase
	    has been individually tested in staging, or you accept the
	    risk of debugging a multi-flag failure surface.
EFF
      ;;
  esac
}

confirm() {
  local phase_flags
  phase_flags=$(flags_for_phase "$PHASE")

  cat <<-EOF

	═══════════════════════════════════════════════════════════════
	PHASE=$PHASE — activate the following flags in $ENV_FILE:
	═══════════════════════════════════════════════════════════════

$(printf "%s\n" "$phase_flags" | sed 's|^|  * |')

	And restart:
$(for s in "${SERVICES[@]}"; do printf "  * %s\n" "$s"; done)

	Effects to watch for:
$(phase_effect_summary)

	Rollback: re-run activator with the same PHASE and each flag
	set to false, or delete the ``FLAG=true`` lines from $ENV_FILE
	+ restart services.

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
log "PHASE=$PHASE"
log "DRY_RUN=$DRY_RUN"
log "FORCE=$FORCE"

ensure_phase_valid
ensure_env_exists
check_blocked_flags
preflight_check

if [[ "$DRY_RUN" != "1" ]]; then
  confirm
  backup_env
fi

# Materialize the target flag list from the chosen phase.
declare -a TARGET_FLAGS=()
while IFS= read -r f; do
  [[ -n "$f" ]] && TARGET_FLAGS+=("$f")
done < <(flags_for_phase "$PHASE")

log "Flipping ${#TARGET_FLAGS[@]} flags to true..."
for flag in "${TARGET_FLAGS[@]}"; do
  flip_flag "$flag"
done

if [[ "$DRY_RUN" != "1" ]]; then
  log "Verifying flag state:"
  for flag in "${TARGET_FLAGS[@]}"; do
    value=$(sed -n "s|^${flag}=||p" "$ENV_FILE" 2>/dev/null | tail -1 | tr -d '[:space:]')
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

log "PHASE=$PHASE activation complete."
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
  * Next phase (when ready):
EOF
case "$PHASE" in
  1) printf "      PHASE=2 sudo -u genlab bash %s\n" "$0" ;;
  2) printf "      PHASE=3 sudo -u genlab bash %s   # requires accuracy-card ready\n" "$0" ;;
  3) printf "      PHASE=4 sudo -u genlab bash %s   # requires Bayesian gate active ≥1wk\n" "$0" ;;
  4) printf "      (all phases complete — monitor Mission Control cards)\n" ;;
  all) printf "      (all phases activated simultaneously — monitor closely)\n" ;;
esac

if [[ "$restart_ok" -ne 0 ]]; then
  error "One or more services failed to restart; investigate before assuming full activation."
  exit 3
fi

exit 0
