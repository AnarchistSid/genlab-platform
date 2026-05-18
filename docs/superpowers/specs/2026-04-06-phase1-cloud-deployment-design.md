# Phase 1 — Cloud Deployment Design (Always-On Services)

**Date:** 2026-04-06
**Server:** Hetzner CX23 — 2 vCPU, 4GB RAM, 40GB disk, Nuremberg (nbg1-dc3)
**IP:** 46.224.237.56
**OS:** Ubuntu (fresh install)

## Goal

Move GenLab's always-on services to the Hetzner cloud server so the platform
runs independently of any local machine. Phase 1 covers stateful infrastructure
(PostgreSQL, Redis), the operations dashboard, engagement pollers, and cron-based
maintenance. Pipeline execution (fetch, score, render, publish) stays local until
Phase 2.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Deployment model | Hybrid: Docker for data stores, native for app services | Saves ~200-400MB RAM vs full Docker on a 4GB box |
| Dashboard access | Public via Cloudflare + Caddy reverse proxy | `ops.aspirehub.ai` — accessible from anywhere, no SSH tunnel needed |
| Code deployment | Manual `git pull` + `uv sync` | Single operator, infrequent deploys, full control |
| Local services | Kill Phase 1 plists immediately on cloud confirmation | Clean cut, no risk of duplicate processing |
| DB access for local pipelines | Expose PostgreSQL 5432 to local IP via Hetzner firewall | Local pipeline services (publisher, ingestion) use cloud DB |

## Architecture

```
Internet
  │
  ▼
Cloudflare (ops.aspirehub.ai, SSL termination, proxy mode)
  │
  ▼ port 80
Hetzner CX23 (46.224.237.56)
  │
  ├─ Caddy (reverse proxy) ──→ localhost:5151 (Dashboard)
  │
  ├─ Docker Compose
  │   ├─ postgres:16-alpine  (port 5432, host-bound for local pipeline access)
  │   └─ redis:7-alpine      (port 6379, localhost only)
  │
  ├─ systemd services (native Python via uv)
  │   ├─ genlab-dashboard.service         (always-on, gunicorn)
  │   ├─ genlab-engagement-poller.service  (always-on, KeepAlive equivalent)
  │   ├─ genlab-metric-collector.timer     (every 60 min)
  │   ├─ genlab-token-refresh.timer        (daily 02:00 UTC)
  │   └─ genlab-db-maintenance.timer       (daily 08:45 UTC)
  │
  └─ UFW Firewall
      ├─ 22/tcp   (SSH — key-only)
      ├─ 80/tcp   (Caddy ← Cloudflare)
      ├─ 443/tcp  (Caddy ← Cloudflare)
      ├─ 5432/tcp (PostgreSQL ← local home IP only)
      └─ DROP all other inbound
```

## Server Layout

```
/opt/genlab/                        ← git clone of GenLab repo
├── .env                            ← merged secrets (root + per-channel)
├── docker-compose.prod.yml         ← Postgres + Redis only (cloud-specific)
├── genlab-core/
├── dashboard/
├── BlackboxBrief/
├── CriticalRush/
├── ClutchWire/
├── SpliceReel/
├── FrameDrift/
├── scripts/
└── .logs/                          ← all service logs

/etc/systemd/system/
├── genlab-dashboard.service
├── genlab-engagement-poller.service
├── genlab-metric-collector.service
├── genlab-metric-collector.timer
├── genlab-token-refresh.service
├── genlab-token-refresh.timer
├── genlab-db-maintenance.service
└── genlab-db-maintenance.timer

/etc/caddy/Caddyfile                ← reverse proxy config
```

## Services — Phase 1 Scope

### 1. PostgreSQL 16 (Docker)

New `docker-compose.prod.yml` alongside the existing `docker-compose.yml`.
The existing file includes the dashboard container (useful for local dev);
the prod file has Postgres + Redis only (dashboard runs natively on the server).

- Volume: `pgdata` named volume (persistent across restarts)
- Healthcheck: `pg_isready -U genlab` every 10s
- Port binding: `0.0.0.0:5432:5432` (for local pipeline access; secured by UFW)
- Memory: ~250MB shared_buffers (tuned for 4GB system)
- WAL: minimal, no replication

Data migration from local:
```bash
# Local machine
pg_dump -U genlab genlab > /tmp/genlab_dump.sql
scp /tmp/genlab_dump.sql root@46.224.237.56:/tmp/

# Server
docker exec -i genlab-postgres psql -U genlab genlab < /tmp/genlab_dump.sql
```

### 2. Redis 7 (Docker)

Existing service from docker-compose. Used by Dramatiq task queues and
engagement engine rate limiting.

- Port: `127.0.0.1:6379` (localhost only, not exposed to internet)
- No persistence needed (task queues are ephemeral)
- Memory: ~50MB baseline

### 3. Dashboard — `genlab-dashboard.service`

The Flask + React operations dashboard. Already has a production Dockerfile,
but we run natively to save RAM.

**systemd unit:**
```ini
[Unit]
Description=GenLab Operations Dashboard
After=network.target docker.service
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
# DATABASE_URL must be in .env (systemd can't expand EnvironmentFile vars in Environment= lines)
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

Dashboard binds to `127.0.0.1:5151` — Caddy handles external access.

Frontend must be pre-built: `cd /opt/genlab/dashboard/frontend && npm run build`

### 4. Engagement Poller — `genlab-engagement-poller.service`

Always-on service polling YouTube (30min), X (15min), Threads (10min) for
comments and mentions. Dispatches to Dramatiq workers via Redis.

**systemd unit:**
```ini
[Unit]
Description=GenLab Engagement Poller
After=network.target docker.service
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

### 5. Metric Collector — `genlab-metric-collector.timer`

Collects engagement metrics from all platforms every 60 minutes. Feeds the
learning loop (6h/24h/48h/168h windows).

**Timer unit:**
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

**Service unit:**
```ini
[Unit]
Description=GenLab Metric Collector
After=network.target docker.service

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

### 6. Token Health — `genlab-token-refresh.timer`

Daily check + refresh of platform tokens (Threads 60-day, YouTube OAuth, etc.).

**Timer unit:**
```ini
[Unit]
Description=GenLab Token Health Timer

[Timer]
OnCalendar=*-*-* 02:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

**Service unit:**
```ini
[Unit]
Description=GenLab Token Health Check
After=network.target

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

### 7. DB Maintenance — `genlab-db-maintenance.timer`

Daily vacuum, index maintenance, and cleanup of stale records.

**Timer unit:**
```ini
[Unit]
Description=GenLab DB Maintenance Timer

[Timer]
OnCalendar=*-*-* 08:45:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

**Service unit:**
```ini
[Unit]
Description=GenLab DB Maintenance
After=network.target docker.service

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
ExecStart=/opt/genlab/scripts/db_maintenance.sh
TimeoutSec=300
```

## Caddy Configuration

```
ops.aspirehub.ai {
    reverse_proxy localhost:5151
}
```

Cloudflare SSL mode should be set to **Full (Strict)** — Caddy auto-provisions
a Let's Encrypt cert, Cloudflare validates it. This gives end-to-end encryption.

## Security Hardening

### SSH
- Disable password authentication (`PasswordAuthentication no`)
- Disable root login via password (`PermitRootLogin prohibit-password`)
- Key-only authentication

### Firewall (UFW)
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp        # SSH
ufw allow 80/tcp        # Caddy (Cloudflare)
ufw allow 443/tcp       # Caddy (Cloudflare)
ufw allow from <HOME_IP> to any port 5432  # PostgreSQL for local pipelines (fill with actual IP at deploy time)
ufw enable
```

### Application
- `genlab` system user (no login shell) runs all services
- `.env` file: `chmod 600`, owned by `genlab`
- PostgreSQL: `pg_hba.conf` restricts connections to `genlab` user from
  localhost + allowed IPs
- Docker socket: only root and docker group access
- Fail2ban for SSH brute force protection

### Secrets
- Single flat `.env` at `/opt/genlab/.env` — all vars from root + per-channel files
  merged into one file (no multi-file loading like `launch_wrapper.sh`)
- Must include `DATABASE_URL=postgresql://genlab:<password>@127.0.0.1:5432/genlab`
  (systemd can't expand vars across EnvironmentFile → Environment directives)
- Never committed to git (`.gitignore` already excludes `.env`)
- Deployed manually via `scp` on first setup

## Docker Compose (Production)

Trimmed version for cloud — Postgres + Redis only (dashboard runs natively):

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

## Local Changes After Phase 1

### Point local pipelines at cloud database
Update local `GenLab/.env`:
```
DATABASE_URL=postgresql://genlab:<password>@46.224.237.56:5432/genlab
```

Local pipeline services (publisher, ingestion) will read/write the cloud
PostgreSQL. The dashboard on the cloud sees the same data in real-time.

### Unload local launchd plists
```bash
launchctl unload ~/Library/LaunchAgents/com.genlab.review-server.plist
launchctl unload ~/Library/LaunchAgents/com.genlab.review-tunnel.plist
launchctl unload ~/Library/LaunchAgents/com.genlab.engagement-poller.plist
launchctl unload ~/Library/LaunchAgents/com.genlab.metric-collector.plist
launchctl unload ~/Library/LaunchAgents/com.genlab.token-refresh.plist
launchctl unload ~/Library/LaunchAgents/com.genlab.db-maintenance.plist
```

Keep these local plists active (Phase 2 scope):
- `com.genlab.publisher` — publishes approved content
- `com.genlab.shared-ingestion` — runs pipeline stages
- `com.genlab.quota-monitor` — monitors local disk
- `com.genlab.daily-intel` — BB intelligence
- `com.genlab.spike-detector` — CR spike detection
- `com.genlab.feedback-collector` — CR feedback

## Server Provisioning Steps (High-Level)

1. SSH into server, create `genlab` system user
2. Install system packages: `uv`, `git`, `caddy`, `docker`, `docker-compose`, `ufw`, `fail2ban`, `node` (for frontend build)
3. Configure SSH hardening + UFW firewall
4. Clone repo to `/opt/genlab/`, set ownership
5. Deploy `.env` file via scp
6. Run `uv sync` to create venv
7. Build dashboard frontend (`npm ci && npm run build`)
8. Start Docker Compose (Postgres + Redis)
9. Migrate database from local
10. Install systemd units, enable timers
11. Configure Caddy + Cloudflare DNS
12. Verify all services healthy
13. Update local `.env` to point at cloud DB
14. Unload local launchd plists for migrated services
15. Monitor for 24h — check dashboard, engagement poller logs, metric collector runs

## Estimated RAM Budget

| Component | Steady State |
|---|---|
| OS + system services | ~350 MB |
| Docker daemon | ~100 MB |
| PostgreSQL (in Docker) | ~250 MB |
| Redis (in Docker) | ~50 MB |
| Dashboard (gunicorn + eventlet) | ~200 MB |
| Engagement Poller | ~150 MB |
| Metric Collector (transient) | ~200 MB (when running) |
| Token Health (transient) | ~100 MB (when running) |
| Caddy | ~30 MB |
| **Total steady state** | **~1.1 GB** |
| **Peak (collector + poller + dashboard)** | **~1.5 GB** |

Leaves ~2.5 GB headroom for Phase 2 pipeline work.

## Success Criteria

- [ ] `ops.aspirehub.ai` loads dashboard with HTTPS
- [ ] Dashboard shows real-time data from cloud PostgreSQL
- [ ] Engagement poller logs show comment fetches across all 5 niches
- [ ] Metric collector fires every 60min (check `systemctl list-timers`)
- [ ] Token health runs at 02:00 UTC (check journal)
- [ ] Local publisher can read/write cloud DB (test with `--dry-run`)
- [ ] All local Phase 1 plists unloaded
- [ ] UFW active, only ports 22/80/443/5432(restricted) open
- [ ] Server survives a reboot (all services auto-start)

## Out of Scope (Phase 2+)

- Pipeline execution (fetch, score, render, publish)
- FFmpeg video rendering
- yt-dlp video downloads
- Dramatiq worker processes (engagement replies)
- Hetzner Volume for media storage
- Automated deploys (GitHub Actions)
- Server monitoring/alerting (Grafana, Prometheus)
- Database backups to object storage
