#!/usr/bin/env bash
# ============================================================================
# install.sh — end-to-end fresh-box setup for Gen Lab production
#
# Wraps deploy/scripts/server-bootstrap.sh (apt/system/user) + the 11 manual
# follow-up steps DEPLOY.md previously left to the operator (clone repo, uv
# sync, docker compose, alembic, systemd units, dashboard frontend build,
# the 2026-06-14 uv legacy-symlink fix, smoke tests).
#
# Audience: a fresh Ubuntu 22.04+ box that has nothing on it except an SSH
# server. Becomes a fully-running Gen Lab Phase-1 install in ~10 minutes.
#
# Usage (run as root on a fresh box):
#
#   curl -fsSL https://raw.githubusercontent.com/AnarchistSid/genlab-platform/main/scripts/install.sh | bash
#
#   # or (after manual clone):
#   ssh root@new-box
#   git clone https://github.com/AnarchistSid/genlab-platform.git /opt/genlab
#   cd /opt/genlab && bash scripts/install.sh
#
# Phases:
#   0. Pre-flight (root + Ubuntu 22.04+ + network reachability)
#   1. System bootstrap (apt packages, docker, caddy, uv, node, genlab user,
#      ufw, fail2ban) — delegates to deploy/scripts/server-bootstrap.sh
#   2. Repo + dependencies (clone if missing, fetch + reset if present,
#      uv sync, npm install + dashboard frontend build)
#   3. .env preflight (refuses to continue without a populated .env; prints
#      template + required keys if missing)
#   4. Docker compose services (postgres + redis via deploy/docker-compose.prod.yml)
#   5. Database schema (wait for postgres healthy, run alembic upgrade head)
#   6. Legacy symlinks (the /opt/genlab/.local/bin/uv → /usr/local/bin/uv
#      symlink that the 2026-06-14 deploy-pipeline-gap recovery surfaced as
#      missing; also covered by ensure_yt_dlp_environment.sh per-pipeline,
#      but bake here for fresh-box cleanliness)
#   7. Systemd units (cp deploy/systemd-phase2/*.service|*.timer to
#      /etc/systemd/system/, daemon-reload, DO NOT auto-start timers —
#      operator opts in per-niche after smoke-testing)
#   8. Mount /mnt/genlab-media (if Hetzner volume present; idempotent)
#   9. Smoke tests (postgres connect, uv invokable as genlab user,
#      alembic current, sample service unit parses cleanly)
#  10. Summary + remaining operator actions
#
# Safe to re-run. Each phase detects existing state and skips if already done.
# `--apply` is the default; `--dry-run` shows what would happen without
# touching the box.
# ============================================================================
set -euo pipefail

GENLAB_ROOT="${GENLAB_PROJECT_ROOT:-/opt/genlab}"
REPO_URL="https://github.com/AnarchistSid/genlab-platform.git"
LOG_DIR="$GENLAB_ROOT/.logs"
LOG="$LOG_DIR/install_$(date +%Y%m%d_%H%M%S).log"

DRY_RUN=0
SKIP_BOOTSTRAP=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)       DRY_RUN=1 ;;
        --skip-bootstrap) SKIP_BOOTSTRAP=1 ;;
        --help|-h)
            head -47 "$0" | tail -45
            exit 0
            ;;
        *) echo "ERROR: unknown arg '$arg' (try --help)"; exit 1 ;;
    esac
done

mkdir -p "$LOG_DIR" 2>/dev/null || LOG=/dev/null

log()  {
    local msg
    msg="[$(date '+%H:%M:%S')] $*"
    echo "$msg"
    [[ "$LOG" != "/dev/null" ]] && echo "$msg" >> "$LOG" 2>/dev/null || true
}
fail() { log "ERROR: $*"; exit 1; }
phase() { log ""; log "═══ PHASE $* ═══"; }
run()  {
    if [[ $DRY_RUN -eq 1 ]]; then
        log "  (dry-run) WOULD: $*"
    else
        log "  → $*"
        "$@" 2>&1 | tee -a "$LOG" || return $?
    fi
}

# ────────────────────────────────────────────────────────────────────────────
# Phase 0 — Pre-flight
# ────────────────────────────────────────────────────────────────────────────
phase "0 — Pre-flight"

[[ $EUID -eq 0 ]] || fail "must run as root"
[[ -f /etc/os-release ]] || fail "/etc/os-release missing; not an Ubuntu/Debian system"
# shellcheck disable=SC1091
source /etc/os-release
[[ "$ID" == "ubuntu" || "$ID" == "debian" ]] || fail "OS '$ID' not supported (need ubuntu or debian)"
log "OS: $PRETTY_NAME"

# Network sanity — abort early if package mirrors aren't reachable
if ! curl -fsS --max-time 5 https://github.com >/dev/null 2>&1; then
    fail "cannot reach github.com — check network/DNS"
fi
log "Network: ✓"

log "Mode: $([[ $DRY_RUN -eq 1 ]] && echo DRY-RUN || echo APPLY)"
log "Log:  $LOG"

# ────────────────────────────────────────────────────────────────────────────
# Phase 1 — System bootstrap
# ────────────────────────────────────────────────────────────────────────────
phase "1 — System bootstrap"

if [[ $SKIP_BOOTSTRAP -eq 1 ]]; then
    log "Skipping bootstrap (--skip-bootstrap set)"
elif id genlab &>/dev/null && command -v uv &>/dev/null && command -v docker &>/dev/null; then
    log "Bootstrap already done (genlab user + uv + docker present) — skipping"
else
    if [[ -f "$GENLAB_ROOT/deploy/scripts/server-bootstrap.sh" ]]; then
        log "Delegating to deploy/scripts/server-bootstrap.sh..."
        run bash "$GENLAB_ROOT/deploy/scripts/server-bootstrap.sh"
    else
        log "deploy/scripts/server-bootstrap.sh not yet present (repo not cloned)"
        log "Inline bootstrap: apt-get + uv + docker + node only"
        run apt-get update -qq
        run apt-get install -y -qq git curl ca-certificates jq libpq-dev build-essential
        if ! command -v uv &>/dev/null; then
            run bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
            ln -sf /root/.local/bin/uv /usr/local/bin/uv
        fi
        if ! command -v docker &>/dev/null; then
            run bash -c "curl -fsSL https://get.docker.com | sh"
            run systemctl enable docker
            run systemctl start docker
        fi
        if ! command -v node &>/dev/null; then
            run bash -c "curl -fsSL https://deb.nodesource.com/setup_22.x | bash -"
            run apt-get install -y -qq nodejs
        fi
        if ! id genlab &>/dev/null; then
            run useradd --system --create-home --home-dir "$GENLAB_ROOT" --shell /usr/sbin/nologin genlab
            run usermod -aG docker genlab
        fi
    fi
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 2 — Repo + dependencies
# ────────────────────────────────────────────────────────────────────────────
phase "2 — Repo + dependencies"

if [[ ! -d "$GENLAB_ROOT/.git" ]]; then
    log "Cloning repo into $GENLAB_ROOT..."
    # Preserve anything the operator dropped there (e.g. .env, .youtube_cookies.txt)
    # by cloning into a temp dir then rsync-merging.
    if [[ -d "$GENLAB_ROOT" ]] && [[ -n "$(ls -A "$GENLAB_ROOT" 2>/dev/null)" ]]; then
        log "  $GENLAB_ROOT non-empty — cloning to /tmp and merging"
        run git clone "$REPO_URL" "/tmp/genlab_clone_$$"
        run rsync -a --ignore-existing "/tmp/genlab_clone_$$/" "$GENLAB_ROOT/"
        run rm -rf "/tmp/genlab_clone_$$"
        # The .git dir didn't merge (rsync --ignore-existing only copies missing)
        if [[ ! -d "$GENLAB_ROOT/.git" ]]; then
            run cp -a "/tmp/genlab_clone_$$/.git" "$GENLAB_ROOT/.git"
        fi
    else
        run git clone "$REPO_URL" "$GENLAB_ROOT"
    fi
    run chown -R genlab:genlab "$GENLAB_ROOT"
else
    log "Repo already present — fetching latest"
    run sudo -u genlab git -C "$GENLAB_ROOT" fetch --quiet origin main
    CURRENT=$(sudo -u genlab git -C "$GENLAB_ROOT" rev-parse HEAD)
    REMOTE=$(sudo -u genlab git -C "$GENLAB_ROOT" rev-parse origin/main)
    if [[ "$CURRENT" != "$REMOTE" ]]; then
        log "  $CURRENT → $REMOTE (fast-forwarding)"
        run sudo -u genlab git -C "$GENLAB_ROOT" pull --ff-only origin main
    else
        log "  already at $CURRENT"
    fi
fi

log "Installing Python dependencies via uv sync..."
run sudo -u genlab bash -c "cd '$GENLAB_ROOT' && /usr/local/bin/uv sync --frozen"

if [[ -d "$GENLAB_ROOT/dashboard/frontend" ]]; then
    log "Building dashboard frontend..."
    run sudo -u genlab bash -c "cd '$GENLAB_ROOT/dashboard/frontend' && npm ci --silent && npm run build"
else
    log "  dashboard/frontend not present — skipping frontend build"
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 3 — .env preflight
# ────────────────────────────────────────────────────────────────────────────
phase "3 — .env preflight"

ENV_FILE="$GENLAB_ROOT/.env"
REQUIRED_KEYS=(
    DATABASE_URL POSTGRES_PASSWORD
    ANTHROPIC_API_KEY OPENAI_API_KEY
    META_ACCESS_TOKEN YOUTUBE_API_KEY
    SHAREPOINT_TENANT_ID SHAREPOINT_CLIENT_ID SHAREPOINT_CLIENT_SECRET
)

if [[ ! -f "$ENV_FILE" ]]; then
    log "WARN: $ENV_FILE missing"
    log "  Required keys for first run:"
    for k in "${REQUIRED_KEYS[@]}"; do log "    $k"; done
    log "  Copy from your secrets vault (1Password / .env.template) and re-run."
    log "  Stopping after Phase 3 since later phases depend on .env values."
    exit 1
fi

missing=()
for k in "${REQUIRED_KEYS[@]}"; do
    grep -qE "^${k}=." "$ENV_FILE" 2>/dev/null || missing+=("$k")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    log "WARN: .env is missing keys: ${missing[*]}"
    log "  Edit $ENV_FILE then re-run."
    exit 1
fi
log ".env present with all ${#REQUIRED_KEYS[@]} required keys"
run chown genlab:genlab "$ENV_FILE"
run chmod 640 "$ENV_FILE"

# ────────────────────────────────────────────────────────────────────────────
# Phase 4 — Docker compose services
# ────────────────────────────────────────────────────────────────────────────
phase "4 — Docker compose services (postgres + redis)"

COMPOSE_FILE="$GENLAB_ROOT/deploy/docker-compose.prod.yml"
if [[ ! -f "$COMPOSE_FILE" ]]; then
    fail "$COMPOSE_FILE missing — repo incomplete"
fi

log "Starting compose services..."
run bash -c "cd '$GENLAB_ROOT' && docker compose -f deploy/docker-compose.prod.yml up -d"

# Wait for postgres healthy (max 60s)
log "Waiting for postgres healthy..."
if [[ $DRY_RUN -eq 0 ]]; then
    for i in {1..30}; do
        status=$(docker inspect --format='{{.State.Health.Status}}' genlab-postgres 2>/dev/null || echo "unknown")
        if [[ "$status" == "healthy" ]]; then
            log "  postgres: healthy ✓"
            break
        fi
        sleep 2
        [[ $i -eq 30 ]] && fail "postgres did not reach healthy state within 60s (status: $status)"
    done
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 5 — Database schema
# ────────────────────────────────────────────────────────────────────────────
phase "5 — Database schema (alembic upgrade head)"

log "Running alembic upgrade head..."
run sudo -u genlab bash -c "cd '$GENLAB_ROOT' && /usr/local/bin/uv run --package genlab-core alembic -c genlab-core/alembic.ini upgrade head"

if [[ $DRY_RUN -eq 0 ]]; then
    REV=$(sudo -u genlab bash -c "cd '$GENLAB_ROOT' && /usr/local/bin/uv run --package genlab-core alembic -c genlab-core/alembic.ini current 2>/dev/null | tail -1")
    log "  alembic_version: $REV"
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 6 — Legacy symlinks
# ────────────────────────────────────────────────────────────────────────────
phase "6 — Legacy symlinks"

# /opt/genlab/.local/bin/uv → /usr/local/bin/uv
# Required because scripts/daily_intel.sh historically resolved
# uv via ${HOME}/.local/bin/uv. PR #187 fixed the script to use
# `command -v uv` first, but the symlink is belt-and-suspenders
# and is also created by ensure_yt_dlp_environment.sh ExecStartPre
# on every pipeline fire. Baking here for fresh-box cleanliness.
# See [[session-2026-06-14-deploy-pipeline-gap]].
LEGACY_UV="$GENLAB_ROOT/.local/bin/uv"
if [[ ! -L "$LEGACY_UV" ]] || [[ "$(readlink "$LEGACY_UV" 2>/dev/null)" != "/usr/local/bin/uv" ]]; then
    log "Creating $LEGACY_UV → /usr/local/bin/uv"
    run mkdir -p "$(dirname "$LEGACY_UV")"
    run ln -sfn /usr/local/bin/uv "$LEGACY_UV"
    run chown -h genlab:genlab "$LEGACY_UV" "$(dirname "$LEGACY_UV")"
else
    log "  $LEGACY_UV already correct"
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 7 — Systemd units
# ────────────────────────────────────────────────────────────────────────────
phase "7 — Systemd units"

UNITS_SRC="$GENLAB_ROOT/deploy/systemd-phase2"
UNITS_DST="/etc/systemd/system"

if [[ ! -d "$UNITS_SRC" ]]; then
    fail "$UNITS_SRC missing — repo incomplete"
fi

INSTALLED=0
SKIPPED=0
for unit in "$UNITS_SRC"/*.service "$UNITS_SRC"/*.timer; do
    [[ -e "$unit" ]] || continue
    name=$(basename "$unit")
    dest="$UNITS_DST/$name"
    if [[ -f "$dest" ]] && cmp -s "$unit" "$dest"; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi
    if [[ $DRY_RUN -eq 0 ]]; then
        cp "$unit" "$dest"
    fi
    INSTALLED=$((INSTALLED + 1))
done
log "Installed $INSTALLED unit(s); $SKIPPED already current"

if [[ $INSTALLED -gt 0 ]]; then
    run systemctl daemon-reload
fi

# Auto-start infrastructure services only (postgres/redis are already up via
# docker compose; dashboard is the only systemd "infra" service). Pipeline
# timers stay disabled — operator opts in per-niche after smoke-testing.
if systemctl list-unit-files genlab-dashboard.service &>/dev/null; then
    if [[ "$(systemctl is-enabled genlab-dashboard.service 2>/dev/null)" != "enabled" ]]; then
        run systemctl enable --now genlab-dashboard.service
    fi
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 8 — Optional: Hetzner volume mount for /mnt/genlab-media
# ────────────────────────────────────────────────────────────────────────────
phase "8 — Media volume (optional)"

if mountpoint -q /mnt/genlab-media 2>/dev/null; then
    log "/mnt/genlab-media already mounted"
elif [[ -x "$GENLAB_ROOT/deploy/scripts/setup-volume.sh" ]]; then
    log "Running deploy/scripts/setup-volume.sh (Hetzner volume setup)..."
    run bash "$GENLAB_ROOT/deploy/scripts/setup-volume.sh"
else
    log "No volume mount and no setup-volume.sh — media will live under $GENLAB_ROOT/.tmp"
    log "  (acceptable for small installs; for Hetzner Phase 1 prod see deploy/scripts/setup-volume.sh)"
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 9 — Smoke tests
# ────────────────────────────────────────────────────────────────────────────
phase "9 — Smoke tests"

if [[ $DRY_RUN -eq 0 ]]; then
    # uv works as genlab user with HOME=/opt/genlab (the bug we just fixed)
    if sudo -u genlab HOME="$GENLAB_ROOT" bash -c '${HOME}/.local/bin/uv --version' &>/dev/null; then
        log "  uv invokable as genlab user via legacy path ✓"
    else
        log "  WARN: uv not invokable via $GENLAB_ROOT/.local/bin/uv as genlab user"
    fi

    # Postgres reachable
    if docker exec genlab-postgres pg_isready -U genlab &>/dev/null; then
        log "  postgres reachable ✓"
    else
        log "  WARN: postgres not reachable via docker exec"
    fi

    # Sample unit parses cleanly
    if systemd-analyze verify /etc/systemd/system/genlab-dashboard.service &>/dev/null; then
        log "  genlab-dashboard.service parses cleanly ✓"
    else
        log "  WARN: genlab-dashboard.service has unit-file warnings"
    fi
fi

# ────────────────────────────────────────────────────────────────────────────
# Phase 10 — Summary + next steps
# ────────────────────────────────────────────────────────────────────────────
phase "10 — Summary + next steps"

HEAD_SHORT=$(git -C "$GENLAB_ROOT" rev-parse --short HEAD 2>/dev/null || echo "unknown")
cat <<EOF | tee -a "$LOG"

╔════════════════════════════════════════════════════════════╗
║   GenLab installation complete                              ║
╠════════════════════════════════════════════════════════════╣
║                                                              ║
║   Installed:                                                 ║
║     • System packages + uv + docker + node                  ║
║     • Repo at $GENLAB_ROOT (HEAD: $HEAD_SHORT)              ║
║     • Postgres + Redis (docker compose)                      ║
║     • Schema at alembic head                                 ║
║     • Dashboard service (running)                            ║
║     • $INSTALLED systemd unit(s) (timers NOT auto-started)   ║
║                                                              ║
║   Remaining operator actions:                                ║
║                                                              ║
║     1. Open PostgreSQL to your dev IP (NOT 0.0.0.0):         ║
║          ufw allow from YOUR_HOME_IP to any port 5432        ║
║                                                              ║
║     2. Set up Caddy reverse proxy (TLS + dashboard host):    ║
║          cp deploy/caddy/Caddyfile /etc/caddy/Caddyfile      ║
║          systemctl reload caddy                              ║
║                                                              ║
║     3. Per-niche smoke test before enabling pipeline timer:  ║
║          systemctl start genlab-pipeline-gaming.service      ║
║          journalctl -u genlab-pipeline-gaming.service -f     ║
║                                                              ║
║     4. Once each niche runs clean, enable its timer:         ║
║          systemctl enable --now genlab-pipeline-gaming.timer ║
║          (repeat for: ai, sports, movies, anime)             ║
║                                                              ║
║     5. Enable backup + health + cleanup timers:              ║
║          systemctl enable --now genlab-pg-backup.timer       ║
║          systemctl enable --now genlab-health-monitor.timer  ║
║          systemctl enable --now genlab-cleanup.timer         ║
║                                                              ║
║   Future deploys: ./scripts/deploy.sh --apply (PR #186)      ║
║                                                              ║
╚════════════════════════════════════════════════════════════╝

Full log: $LOG
EOF
