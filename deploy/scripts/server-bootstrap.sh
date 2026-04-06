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
