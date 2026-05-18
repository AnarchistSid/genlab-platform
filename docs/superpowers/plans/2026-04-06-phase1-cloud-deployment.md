# Phase 1 — Cloud Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy GenLab's always-on services (PostgreSQL, Redis, Dashboard, Engagement Poller, Metric Collector, Token Health, DB Maintenance) to the Hetzner CX23 server at 46.224.237.56.

**Architecture:** Hybrid deployment — PostgreSQL and Redis run in Docker containers, all Python services run natively via systemd using the uv-managed venv. Caddy reverse-proxies the dashboard behind Cloudflare HTTPS on `ops.aspirehub.ai`.

**Tech Stack:** Ubuntu, Docker Compose, systemd, Caddy, uv, Python 3.12+, PostgreSQL 16, Redis 7, Cloudflare DNS

**Spec:** `docs/superpowers/specs/2026-04-06-phase1-cloud-deployment-design.md`

---

## File Structure

All deployment artifacts live in a new `deploy/` directory in the repo:

```
deploy/
├── docker-compose.prod.yml              # Postgres + Redis only (cloud)
├── systemd/
│   ├── genlab-dashboard.service
│   ├── genlab-engagement-poller.service
│   ├── genlab-metric-collector.service
│   ├── genlab-metric-collector.timer
│   ├── genlab-token-refresh.service
│   ├── genlab-token-refresh.timer
│   ├── genlab-db-maintenance.service
│   └── genlab-db-maintenance.timer
├── caddy/
│   └── Caddyfile
└── scripts/
    ├── server-bootstrap.sh              # One-time server provisioning
    └── merge-env.sh                     # Merge local .env files into single flat file
```

No existing files are modified in Tasks 1-3 (deployment artifact creation).
Tasks 4+ are server-side operations (SSH commands).

---

## Task 1: Create deployment artifacts — Docker + systemd

**Files:**
- Create: `deploy/docker-compose.prod.yml`
- Create: `deploy/systemd/genlab-dashboard.service`
- Create: `deploy/systemd/genlab-engagement-poller.service`
- Create: `deploy/systemd/genlab-metric-collector.service`
- Create: `deploy/systemd/genlab-metric-collector.timer`
- Create: `deploy/systemd/genlab-token-refresh.service`
- Create: `deploy/systemd/genlab-token-refresh.timer`
- Create: `deploy/systemd/genlab-db-maintenance.service`
- Create: `deploy/systemd/genlab-db-maintenance.timer`

- [ ] **Step 1: Create `deploy/docker-compose.prod.yml`**

```yaml
version: "3.9"

services:
  postgres:
    image: postgres:16-alpine
    container_name: genlab-postgres
    environment:
      POSTGRES_USER: genlab
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: genlab
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U genlab"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 512M

  redis:
    image: redis:7-alpine
    container_name: genlab-redis
    ports:
      - "127.0.0.1:6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 128M

volumes:
  pgdata:
```

- [ ] **Step 2: Create `deploy/systemd/genlab-dashboard.service`**

```ini
[Unit]
Description=GenLab Operations Dashboard
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=exec
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=BACKLOG_CONFIG_PATH=/opt/genlab/genlab-core/config/lists_config.yaml
Environment=GENLAB_USE_POSTGRES=true
Environment=REDIS_HOST=127.0.0.1
Environment=REDIS_PORT=6379
ExecStart=/opt/genlab/.venv/bin/gunicorn \
    --worker-class eventlet \
    --workers 1 \
    --timeout 120 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --bind 127.0.0.1:5151 \
    --access-logfile /opt/genlab/.logs/dashboard_access.log \
    --error-logfile /opt/genlab/.logs/dashboard_error.log \
    --capture-output \
    server.review_server:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Note: `DATABASE_URL` is set in `/opt/genlab/.env` (systemd cannot expand
`EnvironmentFile` vars in `Environment=` directives).

- [ ] **Step 3: Create `deploy/systemd/genlab-engagement-poller.service`**

```ini
[Unit]
Description=GenLab Engagement Poller
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=exec
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/genlab-core
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=BACKLOG_CONFIG_PATH=/opt/genlab/genlab-core/config/lists_config.yaml
Environment=REDIS_HOST=127.0.0.1
Environment=REDIS_PORT=6379
Environment=ENGAGEMENT_DISPATCH=dramatiq
ExecStart=/opt/genlab/.venv/bin/python \
    /opt/genlab/genlab-core/scripts/run_engagement_poller.py \
    --niche all --platform all
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Create metric collector service + timer**

`deploy/systemd/genlab-metric-collector.service`:
```ini
[Unit]
Description=GenLab Metric Collector
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=BACKLOG_CONFIG_PATH=/opt/genlab/genlab-core/config/lists_config.yaml
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.learning.metric_collector
TimeoutSec=600
```

`deploy/systemd/genlab-metric-collector.timer`:
```ini
[Unit]
Description=GenLab Metric Collector Timer

[Timer]
OnBootSec=5min
OnUnitActiveSec=60min
AccuracySec=1min

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Create token refresh service + timer**

`deploy/systemd/genlab-token-refresh.service`:
```ini
[Unit]
Description=GenLab Token Health Check
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python /opt/genlab/scripts/token_health.py
TimeoutSec=300
```

`deploy/systemd/genlab-token-refresh.timer`:
```ini
[Unit]
Description=GenLab Token Health Timer

[Timer]
OnCalendar=*-*-* 02:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Create DB maintenance service + timer**

`deploy/systemd/genlab-db-maintenance.service`:
```ini
[Unit]
Description=GenLab DB Maintenance
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
ExecStart=/opt/genlab/scripts/db_maintenance.sh
TimeoutSec=300
```

`deploy/systemd/genlab-db-maintenance.timer`:
```ini
[Unit]
Description=GenLab DB Maintenance Timer

[Timer]
OnCalendar=*-*-* 08:45:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 7: Verify all files created**

Run: `ls -la deploy/docker-compose.prod.yml deploy/systemd/ deploy/caddy/`

Expected: 9 files total — 1 docker-compose, 5 services, 3 timers.

- [ ] **Step 8: Commit**

```bash
git add deploy/
git commit -m "feat(deploy): add Phase 1 cloud deployment artifacts

Systemd units for dashboard, engagement poller, metric collector,
token health, and DB maintenance. Docker Compose prod config for
Postgres + Redis. Caddy reverse proxy config."
```

---

## Task 2: Create Caddy config and env merge script

**Files:**
- Create: `deploy/caddy/Caddyfile`
- Create: `deploy/scripts/merge-env.sh`

- [ ] **Step 1: Create `deploy/caddy/Caddyfile`**

```
ops.aspirehub.ai {
    reverse_proxy localhost:5151
    encode gzip

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
    }

    log {
        output file /opt/genlab/.logs/caddy_access.log
        format json
    }
}
```

- [ ] **Step 2: Create `deploy/scripts/merge-env.sh`**

This script merges all local .env files into a single flat file for the server.
Run locally before scp-ing to the server.

```bash
#!/usr/bin/env bash
# merge-env.sh — Merge all GenLab .env files into a single flat file for cloud deployment.
#
# Usage: ./deploy/scripts/merge-env.sh > /tmp/genlab-cloud.env
#        scp /tmp/genlab-cloud.env root@46.224.237.56:/opt/genlab/.env
#
# This replaces the multi-file loading pattern in launch_wrapper.sh with a single
# flat file that systemd's EnvironmentFile= can consume directly.

set -euo pipefail

GENLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

ENV_FILES=(
    "$GENLAB_ROOT/.env"
    "$GENLAB_ROOT/BlackboxBrief/.env"
    "$GENLAB_ROOT/CriticalRush/.env"
    "$GENLAB_ROOT/ClutchWire/.env"
    "$GENLAB_ROOT/SpliceReel/.env"
    "$GENLAB_ROOT/FrameDrift/.env"
)

echo "# GenLab Cloud Environment — merged $(date -u '+%Y-%m-%d %H:%M UTC')"
echo "# Source files: ${ENV_FILES[*]}"
echo ""

declare -A SEEN

for envfile in "${ENV_FILES[@]}"; do
    if [[ ! -f "$envfile" ]]; then
        echo "# SKIPPED (not found): $envfile"
        continue
    fi
    echo "# --- $(basename "$(dirname "$envfile")")/$(basename "$envfile") ---"
    while IFS= read -r line; do
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        key="${line%%=*}"
        key=$(echo "$key" | xargs)
        [[ -z "$key" ]] && continue
        # Later files override earlier ones (per-niche overrides root)
        # Track which keys we've seen to flag duplicates
        if [[ -n "${SEEN[$key]+x}" ]]; then
            echo "# OVERRIDE: $key (was from ${SEEN[$key]})"
        fi
        SEEN[$key]="$envfile"
        echo "$line"
    done < "$envfile"
    echo ""
done

# Append cloud-specific overrides
echo "# --- Cloud-specific overrides ---"
echo "DATABASE_URL=postgresql://genlab:\${POSTGRES_PASSWORD}@127.0.0.1:5432/genlab"
echo "GENLAB_PROJECT_ROOT=/opt/genlab"
echo "BACKLOG_CONFIG_PATH=/opt/genlab/genlab-core/config/lists_config.yaml"
echo "GENLAB_USE_POSTGRES=true"
echo "REDIS_HOST=127.0.0.1"
echo "REDIS_PORT=6379"
```

**Important:** After running, manually review the output and replace the
`DATABASE_URL` line with the actual password (the `\${POSTGRES_PASSWORD}` is a
placeholder — systemd won't expand it). The final line should look like:
```
DATABASE_URL=postgresql://genlab:YOUR_ACTUAL_PASSWORD@127.0.0.1:5432/genlab
```

- [ ] **Step 3: Make merge script executable**

Run: `chmod +x deploy/scripts/merge-env.sh`

- [ ] **Step 4: Commit**

```bash
git add deploy/caddy/ deploy/scripts/
git commit -m "feat(deploy): add Caddy config and env merge script"
```

---

## Task 3: Create server bootstrap script

**Files:**
- Create: `deploy/scripts/server-bootstrap.sh`

- [ ] **Step 1: Create `deploy/scripts/server-bootstrap.sh`**

One-time script to provision a fresh Ubuntu server. Run as root via SSH.

```bash
#!/usr/bin/env bash
# server-bootstrap.sh — One-time server provisioning for GenLab Phase 1.
#
# Run as root on a fresh Ubuntu 22.04+ server:
#   ssh root@46.224.237.56 'bash -s' < deploy/scripts/server-bootstrap.sh
#
# After this script completes, proceed with manual steps:
#   1. scp .env and clone repo
#   2. uv sync
#   3. Start Docker Compose
#   4. Install systemd units

set -euo pipefail

echo "=== GenLab Server Bootstrap ==="
echo "Started: $(date -u)"

# ── 1. System updates ──────────────────────────────────────────────
echo "[1/8] Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq

# ── 2. Install system packages ─────────────────────────────────────
echo "[2/8] Installing system packages..."
apt-get install -y -qq \
    git \
    curl \
    wget \
    unzip \
    build-essential \
    libpq-dev \
    ufw \
    fail2ban \
    jq

# Install PostgreSQL client (needed by db_maintenance.sh)
# Add PostgreSQL apt repo for latest client tools
apt-get install -y -qq gnupg lsb-release
echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    > /etc/apt/sources.list.d/pgdg.list
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | \
    gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg
apt-get update -qq
apt-get install -y -qq postgresql-client-16

# ── 3. Install Docker ─────────────────────────────────────────────
echo "[3/8] Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi
systemctl enable docker
systemctl start docker

# Install Docker Compose plugin
if ! docker compose version &>/dev/null; then
    apt-get install -y -qq docker-compose-plugin
fi

# ── 4. Install Caddy ──────────────────────────────────────────────
echo "[4/8] Installing Caddy..."
if ! command -v caddy &>/dev/null; then
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
        gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
        tee /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
fi

# ── 5. Install uv ─────────────────────────────────────────────────
echo "[5/8] Installing uv..."
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi
# Make uv available system-wide
ln -sf /root/.local/bin/uv /usr/local/bin/uv 2>/dev/null || true

# ── 6. Install Node.js (for dashboard frontend build) ─────────────
echo "[6/8] Installing Node.js 22..."
if ! command -v node &>/dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y -qq nodejs
fi

# ── 7. Create genlab system user ──────────────────────────────────
echo "[7/8] Creating genlab user..."
if ! id genlab &>/dev/null; then
    useradd --system --create-home --home-dir /opt/genlab --shell /usr/sbin/nologin genlab
    usermod -aG docker genlab
fi

mkdir -p /opt/genlab/.logs
chown -R genlab:genlab /opt/genlab

# ── 8. Configure firewall ─────────────────────────────────────────
echo "[8/8] Configuring UFW firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'Caddy HTTP'
ufw allow 443/tcp comment 'Caddy HTTPS'
# PostgreSQL access for local pipeline — add your home IP:
# ufw allow from YOUR_HOME_IP to any port 5432 comment 'PostgreSQL local pipeline'
echo "y" | ufw enable

# ── SSH hardening ──────────────────────────────────────────────────
echo "Hardening SSH..."
sed -i 's/#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload sshd

# ── Fail2ban ───────────────────────────────────────────────────────
echo "Enabling fail2ban..."
systemctl enable fail2ban
systemctl start fail2ban

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next steps:"
echo "  1. Clone repo:     git clone <repo-url> /opt/genlab"
echo "  2. Deploy .env:    scp genlab-cloud.env root@server:/opt/genlab/.env"
echo "  3. Fix ownership:  chown -R genlab:genlab /opt/genlab"
echo "  4. Install deps:   cd /opt/genlab && uv sync --frozen"
echo "  5. Build frontend: cd /opt/genlab/dashboard/frontend && npm ci && npm run build"
echo "  6. Start Docker:   cd /opt/genlab && docker compose -f deploy/docker-compose.prod.yml up -d"
echo "  7. Migrate DB:     docker exec -i genlab-postgres psql -U genlab genlab < dump.sql"
echo "  8. Install units:  cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/"
echo "  9. Enable units:   systemctl daemon-reload && systemctl enable --now genlab-*.service genlab-*.timer"
echo " 10. Setup Caddy:    cp deploy/caddy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy"
echo " 11. Add PG access:  ufw allow from YOUR_HOME_IP to any port 5432"
```

- [ ] **Step 2: Make bootstrap script executable**

Run: `chmod +x deploy/scripts/server-bootstrap.sh`

- [ ] **Step 3: Commit**

```bash
git add deploy/scripts/server-bootstrap.sh
git commit -m "feat(deploy): add server bootstrap provisioning script"
```

---

## Task 4: Run server bootstrap

**Prerequisites:** SSH access to root@46.224.237.56 with key auth.

- [ ] **Step 1: Run the bootstrap script on the server**

From local machine:
```bash
ssh root@46.224.237.56 'bash -s' < /Users/anarchistsid/GenLab/deploy/scripts/server-bootstrap.sh
```

Expected: Script prints `[1/8]` through `[8/8]`, ends with "Bootstrap complete" and next steps.

- [ ] **Step 2: Verify installed packages**

```bash
ssh root@46.224.237.56 'docker --version && docker compose version && caddy version && uv --version && node --version && psql --version && ufw status'
```

Expected: Version strings for all tools, UFW showing active with ports 22, 80, 443.

- [ ] **Step 3: Verify genlab user exists**

```bash
ssh root@46.224.237.56 'id genlab && ls -la /opt/genlab/'
```

Expected: `uid=XXX(genlab)` and `/opt/genlab/` owned by genlab:genlab with `.logs/` dir.

---

## Task 5: Clone repo and deploy secrets

- [ ] **Step 1: Generate and add deploy key for the server**

On the server:
```bash
ssh root@46.224.237.56 'sudo -u genlab ssh-keygen -t ed25519 -N "" -f /opt/genlab/.ssh/id_ed25519 -C "genlab-deploy@hetzner"'
ssh root@46.224.237.56 'cat /opt/genlab/.ssh/id_ed25519.pub'
```

Copy the public key and add it as a deploy key in GitHub repo settings
(Settings → Deploy keys → Add deploy key, read-only access).

- [ ] **Step 2: Clone the repo**

```bash
ssh root@46.224.237.56 'sudo -u genlab bash -c "
    cd /opt/genlab
    GIT_SSH_COMMAND=\"ssh -o StrictHostKeyChecking=accept-new\" \
    git clone git@github.com:YOUR_ORG/GenLab.git .
"'
```

Replace `YOUR_ORG/GenLab` with the actual repo URL.

Expected: Full repo cloned to `/opt/genlab/`.

- [ ] **Step 3: Merge and deploy .env**

On local machine:
```bash
cd /Users/anarchistsid/GenLab
./deploy/scripts/merge-env.sh > /tmp/genlab-cloud.env
```

Review the output — ensure `DATABASE_URL` has the actual password (not a
variable reference). Edit `/tmp/genlab-cloud.env` to fix if needed:
```bash
# Replace the DATABASE_URL line with actual credentials:
# DATABASE_URL=postgresql://genlab:YOUR_ACTUAL_PASSWORD@127.0.0.1:5432/genlab
```

Deploy to server:
```bash
scp /tmp/genlab-cloud.env root@46.224.237.56:/opt/genlab/.env
ssh root@46.224.237.56 'chown genlab:genlab /opt/genlab/.env && chmod 600 /opt/genlab/.env'
```

- [ ] **Step 4: Verify repo and .env on server**

```bash
ssh root@46.224.237.56 'ls /opt/genlab/genlab-core/pyproject.toml && stat -c "%a %U" /opt/genlab/.env'
```

Expected: `pyproject.toml` exists, `.env` shows `600 genlab`.

- [ ] **Step 5: Fix ownership of entire repo**

```bash
ssh root@46.224.237.56 'chown -R genlab:genlab /opt/genlab'
```

---

## Task 6: Install Python deps and build frontend

- [ ] **Step 1: Run uv sync to create the venv**

```bash
ssh root@46.224.237.56 'cd /opt/genlab && uv sync --frozen'
```

Expected: venv created at `/opt/genlab/.venv/`, all workspace members installed.
This may take 2-3 minutes on first run.

- [ ] **Step 2: Verify Python and key packages are available**

```bash
ssh root@46.224.237.56 '/opt/genlab/.venv/bin/python -c "import genlab_core; print(genlab_core.__file__)"'
```

Expected: prints path like `/opt/genlab/genlab-core/src/genlab_core/__init__.py`

- [ ] **Step 3: Build dashboard frontend**

```bash
ssh root@46.224.237.56 'cd /opt/genlab/dashboard/frontend && npm ci && npm run build'
```

Expected: `dist/` directory created with `index.html` and JS/CSS bundles.

- [ ] **Step 4: Verify frontend build**

```bash
ssh root@46.224.237.56 'ls /opt/genlab/dashboard/frontend/dist/index.html'
```

Expected: file exists.

- [ ] **Step 5: Fix ownership after npm install**

```bash
ssh root@46.224.237.56 'chown -R genlab:genlab /opt/genlab'
```

---

## Task 7: Start Docker Compose (PostgreSQL + Redis)

- [ ] **Step 1: Start Postgres and Redis**

```bash
ssh root@46.224.237.56 'cd /opt/genlab && docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d'
```

Expected: Two containers start — `genlab-postgres` and `genlab-redis`.

- [ ] **Step 2: Verify containers are healthy**

```bash
ssh root@46.224.237.56 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'
```

Expected:
```
NAMES              STATUS                   PORTS
genlab-postgres    Up X seconds (healthy)   0.0.0.0:5432->5432/tcp
genlab-redis       Up X seconds (healthy)   127.0.0.1:6379->6379/tcp
```

- [ ] **Step 3: Verify PostgreSQL accepts connections**

```bash
ssh root@46.224.237.56 'docker exec genlab-postgres psql -U genlab -d genlab -c "SELECT version();"'
```

Expected: PostgreSQL 16.x version string.

- [ ] **Step 4: Verify Redis accepts connections**

```bash
ssh root@46.224.237.56 'docker exec genlab-redis redis-cli ping'
```

Expected: `PONG`

- [ ] **Step 5: Set Docker Compose to start on boot**

Docker Compose with `restart: unless-stopped` handles this automatically as
long as Docker starts on boot (already enabled in bootstrap). Verify:

```bash
ssh root@46.224.237.56 'systemctl is-enabled docker'
```

Expected: `enabled`

---

## Task 8: Migrate database from local

- [ ] **Step 1: Dump local database**

On local machine:
```bash
pg_dump -U genlab genlab > /tmp/genlab_dump.sql
```

If the local dump is large, compress it:
```bash
pg_dump -U genlab genlab | gzip > /tmp/genlab_dump.sql.gz
```

- [ ] **Step 2: Transfer dump to server**

```bash
scp /tmp/genlab_dump.sql root@46.224.237.56:/tmp/
# Or if compressed:
# scp /tmp/genlab_dump.sql.gz root@46.224.237.56:/tmp/
```

- [ ] **Step 3: Restore on cloud PostgreSQL**

```bash
ssh root@46.224.237.56 'docker exec -i genlab-postgres psql -U genlab genlab < /tmp/genlab_dump.sql'
# Or if compressed:
# ssh root@46.224.237.56 'gunzip -c /tmp/genlab_dump.sql.gz | docker exec -i genlab-postgres psql -U genlab genlab'
```

Expected: SQL commands execute, tables created and populated.

- [ ] **Step 4: Verify migration — check table count**

```bash
ssh root@46.224.237.56 'docker exec genlab-postgres psql -U genlab -d genlab -c "
SELECT schemaname, tablename FROM pg_tables WHERE schemaname = '\''public'\'' ORDER BY tablename;
"'
```

Expected: All GenLab tables listed (blueprints, stories, publishing_analytics,
analytics, content_memory, bandit_arms, pending_feedback, pending_engagement, etc.)

- [ ] **Step 5: Verify row counts match**

```bash
ssh root@46.224.237.56 'docker exec genlab-postgres psql -U genlab -d genlab -c "
SELECT relname AS table, n_live_tup AS rows
FROM pg_stat_user_tables
ORDER BY n_live_tup DESC;
"'
```

Compare with local:
```bash
psql -U genlab -d genlab -c "SELECT relname AS table, n_live_tup AS rows FROM pg_stat_user_tables ORDER BY n_live_tup DESC;"
```

Expected: Row counts match (within a few rows — VACUUM may differ).

- [ ] **Step 6: Clean up dump file**

```bash
ssh root@46.224.237.56 'rm /tmp/genlab_dump.sql*'
```

---

## Task 9: Install and start systemd services

- [ ] **Step 1: Copy systemd units to system directory**

```bash
ssh root@46.224.237.56 'cp /opt/genlab/deploy/systemd/*.service /opt/genlab/deploy/systemd/*.timer /etc/systemd/system/'
```

- [ ] **Step 2: Reload systemd and enable all units**

```bash
ssh root@46.224.237.56 'systemctl daemon-reload'
```

- [ ] **Step 3: Start the dashboard**

```bash
ssh root@46.224.237.56 'systemctl enable --now genlab-dashboard.service'
```

Verify:
```bash
ssh root@46.224.237.56 'systemctl status genlab-dashboard.service --no-pager -l'
```

Expected: `active (running)`, logs show gunicorn starting on 127.0.0.1:5151.

- [ ] **Step 4: Test dashboard responds locally**

```bash
ssh root@46.224.237.56 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5151/'
```

Expected: `200` or `401` (Basic Auth challenge — both mean gunicorn is serving).

- [ ] **Step 5: Start the engagement poller**

```bash
ssh root@46.224.237.56 'systemctl enable --now genlab-engagement-poller.service'
```

Verify:
```bash
ssh root@46.224.237.56 'systemctl status genlab-engagement-poller.service --no-pager -l'
```

Expected: `active (running)`, logs show poller starting for all niches.

- [ ] **Step 6: Enable the timers**

```bash
ssh root@46.224.237.56 'systemctl enable --now genlab-metric-collector.timer genlab-token-refresh.timer genlab-db-maintenance.timer'
```

Verify timers are scheduled:
```bash
ssh root@46.224.237.56 'systemctl list-timers genlab-* --no-pager'
```

Expected: Three timers listed with next trigger times:
- metric-collector: ~60min from now
- token-refresh: next 02:00 UTC
- db-maintenance: next 08:45 UTC

- [ ] **Step 7: Test metric collector fires manually**

```bash
ssh root@46.224.237.56 'systemctl start genlab-metric-collector.service'
ssh root@46.224.237.56 'journalctl -u genlab-metric-collector.service --no-pager -n 20'
```

Expected: Logs show metric collection running (may show warnings if no recent
publishes exist yet — that's fine).

---

## Task 10: Configure Caddy + Cloudflare DNS

- [ ] **Step 1: Install Caddyfile**

```bash
ssh root@46.224.237.56 'cp /opt/genlab/deploy/caddy/Caddyfile /etc/caddy/Caddyfile'
```

- [ ] **Step 2: Validate Caddy config**

```bash
ssh root@46.224.237.56 'caddy validate --config /etc/caddy/Caddyfile'
```

Expected: `Valid configuration`

- [ ] **Step 3: Reload Caddy**

```bash
ssh root@46.224.237.56 'systemctl reload caddy'
ssh root@46.224.237.56 'systemctl status caddy --no-pager'
```

Expected: `active (running)`

- [ ] **Step 4: Add DNS record in Cloudflare**

In Cloudflare dashboard for `aspirehub.ai`:
1. Go to DNS → Records
2. Add record:
   - Type: `A`
   - Name: `ops`
   - IPv4: `46.224.237.56`
   - Proxy status: **Proxied** (orange cloud)
   - TTL: Auto
3. Save

- [ ] **Step 5: Set Cloudflare SSL mode to Full (Strict)**

In Cloudflare dashboard:
1. Go to SSL/TLS → Overview
2. Set encryption mode to **Full (strict)**

This tells Cloudflare to validate Caddy's auto-provisioned Let's Encrypt cert.

- [ ] **Step 6: Wait for DNS propagation and test**

Wait 1-2 minutes for DNS, then:
```bash
curl -I https://ops.aspirehub.ai/
```

Expected: `HTTP/2 200` or `HTTP/2 401` (Basic Auth) with valid SSL certificate.
If 522/523 error, wait a few more minutes for Caddy to obtain its cert.

- [ ] **Step 7: Test dashboard login via browser**

Open `https://ops.aspirehub.ai/` in browser. Should see Basic Auth prompt.
Log in with the credentials from `.env` (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`).

Expected: Dashboard loads with real data from cloud PostgreSQL.

---

## Task 11: Open PostgreSQL for local pipeline access

- [ ] **Step 1: Get your current home IP**

```bash
curl -s ifconfig.me
```

Note the IP (e.g., `49.36.xxx.xxx`).

- [ ] **Step 2: Add UFW rule for your IP**

```bash
ssh root@46.224.237.56 "ufw allow from YOUR_HOME_IP to any port 5432 comment 'PostgreSQL local pipeline'"
```

Replace `YOUR_HOME_IP` with the actual IP from step 1.

- [ ] **Step 3: Verify PostgreSQL is reachable from local machine**

```bash
psql -h 46.224.237.56 -U genlab -d genlab -c "SELECT 1;"
```

Expected: Returns `1`. If connection refused, check that Docker binds
5432 to 0.0.0.0 (not 127.0.0.1).

- [ ] **Step 4: Update local `.env` to point at cloud database**

Edit `/Users/anarchistsid/GenLab/.env`:
```bash
# Change DATABASE_URL from local to cloud:
DATABASE_URL=postgresql://genlab:YOUR_PASSWORD@46.224.237.56:5432/genlab
```

- [ ] **Step 5: Verify local pipeline can use cloud DB**

```bash
cd /Users/anarchistsid/GenLab
uv run --package genlab-core python -c "
from genlab_core.http.backlog_client import BacklogClient
client = BacklogClient()
print('Connected to:', client._db_url[:50] + '...')
"
```

Expected: Prints connection string pointing to `46.224.237.56`.

---

## Task 12: Unload local launchd plists and verify

- [ ] **Step 1: Unload migrated local services**

```bash
launchctl unload ~/Library/LaunchAgents/com.genlab.review-server.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.genlab.review-tunnel.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.genlab.engagement-poller.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.genlab.metric-collector.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.genlab.token-refresh.plist 2>/dev/null || true
launchctl unload ~/Library/LaunchAgents/com.genlab.db-maintenance.plist 2>/dev/null || true
```

- [ ] **Step 2: Verify local services are stopped**

```bash
launchctl list | grep genlab
```

Expected: Only pipeline-related services remain:
- `com.genlab.publisher`
- `com.genlab.shared-ingestion`
- `com.genlab.quota-monitor`
- `com.genlab.daily-intel`
- `com.genlab.spike-detector`
- `com.genlab.feedback-collector`

- [ ] **Step 3: Verify local publisher still works with cloud DB**

```bash
cd /Users/anarchistsid/GenLab
uv run --package genlab-core python -m genlab_core.publishing.publish_all_platforms --niche gaming --dry-run
```

Expected: Connects to cloud DB, lists blueprints (or reports none ready).

---

## Task 13: Full verification and reboot test

- [ ] **Step 1: Run success criteria checklist**

```bash
# 1. Dashboard via HTTPS
curl -s -o /dev/null -w "%{http_code}" https://ops.aspirehub.ai/
# Expected: 200 or 401

# 2. Cloud services running
ssh root@46.224.237.56 'systemctl is-active genlab-dashboard genlab-engagement-poller'
# Expected: active / active

# 3. Timers scheduled
ssh root@46.224.237.56 'systemctl list-timers genlab-* --no-pager'
# Expected: 3 timers with next trigger times

# 4. Docker containers healthy
ssh root@46.224.237.56 'docker ps --format "{{.Names}}: {{.Status}}"'
# Expected: genlab-postgres: Up (healthy), genlab-redis: Up (healthy)

# 5. UFW active
ssh root@46.224.237.56 'ufw status numbered'
# Expected: 22, 80, 443, 5432(restricted) — everything else denied

# 6. Fail2ban active
ssh root@46.224.237.56 'fail2ban-client status sshd'
# Expected: shows jail info
```

- [ ] **Step 2: Reboot the server and verify auto-start**

```bash
ssh root@46.224.237.56 'reboot'
```

Wait 60 seconds, then:
```bash
ssh root@46.224.237.56 'docker ps && systemctl is-active genlab-dashboard genlab-engagement-poller && systemctl list-timers genlab-*'
```

Expected: All containers up, services active, timers scheduled.

- [ ] **Step 3: Check dashboard loads after reboot**

```bash
curl -s -o /dev/null -w "%{http_code}" https://ops.aspirehub.ai/
```

Expected: `200` or `401`.

- [ ] **Step 4: Check engagement poller logs for activity**

```bash
ssh root@46.224.237.56 'journalctl -u genlab-engagement-poller.service --no-pager -n 30 --since "5 minutes ago"'
```

Expected: Logs show poller running and fetching comments (or sleeping between
poll intervals).

---

## Post-Deployment Notes

### Updating code on the server
```bash
ssh root@46.224.237.56 'cd /opt/genlab && sudo -u genlab git pull && uv sync --frozen'
# Restart affected services:
ssh root@46.224.237.56 'systemctl restart genlab-dashboard genlab-engagement-poller'
```

### Viewing logs
```bash
# systemd journal (all services)
ssh root@46.224.237.56 'journalctl -u genlab-dashboard --no-pager -n 50'

# Application logs
ssh root@46.224.237.56 'tail -50 /opt/genlab/.logs/dashboard_access.log'

# Docker logs
ssh root@46.224.237.56 'docker logs genlab-postgres --tail 50'
```

### If your home IP changes
```bash
# Remove old rule
ssh root@46.224.237.56 'ufw status numbered'  # find the rule number
ssh root@46.224.237.56 'ufw delete RULE_NUMBER'

# Add new rule
ssh root@46.224.237.56 'ufw allow from NEW_IP to any port 5432'
```

### Emergency: restart everything
```bash
ssh root@46.224.237.56 'cd /opt/genlab && docker compose -f deploy/docker-compose.prod.yml restart && systemctl restart genlab-dashboard genlab-engagement-poller'
```
