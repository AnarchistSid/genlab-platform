# Phase 2 — Full Cloud Migration Design

**Date:** 2026-04-06
**Server:** Hetzner CX23 — 2 vCPU, 4GB RAM, 40GB root disk + 50GB Volume
**Depends on:** Phase 1 (complete) — PostgreSQL, Redis, Dashboard, engagement poller, metric collector, token health, DB maintenance already running on cloud.

## Goal

Migrate all remaining 24 local launchd services to the Hetzner cloud server. After Phase 2, the local Mac runs zero GenLab services. Everything is autonomous in the cloud.

## Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Scope | All 24 remaining services | Full cutover, local does nothing |
| Render strategy | Queue-based single worker | Decouples pipeline from CPU-bound FFmpeg; scales later if needed |
| Storage | Hetzner Volume (50GB, ~€2.50/mo) | Root disk too small for video; volume survives rebuilds |
| Webhook exposure | Caddy path route `/webhooks/meta` | No extra subdomain, reuses existing Caddy + Cloudflare |
| Pipeline scheduling | Staggered 90min apart (UTC) | No concurrent pipeline overlap on 2 vCPU |

## Architecture (after Phase 2)

```
Internet
  │
  ▼
Cloudflare (ops.aspirehub.ai, SSL)
  │
  ▼ port 80/443
Hetzner CX23 (46.224.237.56)
  │
  ├─ Caddy
  │   ├─ / → localhost:5151 (Dashboard)
  │   └─ /webhooks/meta → localhost:8765 (Meta webhook)
  │
  ├─ Docker Compose (Phase 1)
  │   ├─ genlab-postgres (port 5432)
  │   └─ genlab-redis (port 6379)
  │
  ├─ Always-on services (systemd)
  │   ├─ genlab-dashboard.service          (Phase 1)
  │   ├─ genlab-engagement-poller.service  (Phase 1)
  │   ├─ genlab-webhook.service            (NEW — uvicorn :8765)
  │   ├─ genlab-engagement-worker.service  (NEW — dramatiq 1 proc / 2 threads)
  │   ├─ genlab-render-worker.service      (NEW — single FFmpeg worker)
  │   └─ genlab-quota-monitor.service      (NEW — disk quota daemon)
  │
  ├─ Scheduled timers (systemd)
  │   ├─ Phase 1: metric-collector, token-refresh, db-maintenance
  │   ├─ Pipeline (5 niches, staggered):
  │   │   ├─ genlab-pipeline-ai.timer         (02:30 UTC / 08:00 IST)
  │   │   ├─ genlab-pipeline-gaming.timer     (04:00 UTC / 09:30 IST)
  │   │   ├─ genlab-pipeline-anime.timer      (06:00 UTC / 11:30 IST)
  │   │   ├─ genlab-pipeline-movies.timer     (08:00 UTC / 13:30 IST)
  │   │   └─ genlab-pipeline-sports.timer     (10:00 UTC / 15:30 IST)
  │   ├─ Publisher:
  │   │   └─ genlab-publisher.timer           (06:35 + 10:30 UTC)
  │   ├─ Ingestion:
  │   │   └─ genlab-shared-ingestion.timer    (05:00 UTC)
  │   ├─ Analytics/Learning:
  │   │   ├─ genlab-insights-collector.timer   (06:45 + 12:30 UTC)
  │   │   ├─ genlab-spike-detector.timer       (every 5min)
  │   │   ├─ genlab-viral-detector.timer       (every 2h)
  │   │   ├─ genlab-feedback-collector.timer   (13:30 UTC)
  │   │   ├─ genlab-audience-collector.timer   (14:30 UTC)
  │   │   ├─ genlab-hook-trainer.timer         (weekly Sun 05:00 UTC)
  │   │   ├─ genlab-config-updater.timer       (weekly Mon 09:00 UTC)
  │   │   └─ genlab-preference-collector.timer (weekly Sun 04:00 UTC)
  │   ├─ Ops:
  │   │   ├─ genlab-cleanup.timer              (daily 01:00 UTC, NEW: media volume)
  │   │   ├─ genlab-pg-backup.timer            (daily 01:00 UTC)
  │   │   ├─ genlab-daily-verify.timer         (16:30 UTC / 22:00 IST)
  │   │   └─ genlab-morning-briefing.timer     (02:45 UTC / 08:15 IST)
  │   └─ Affiliate:
  │       ├─ genlab-affiliate-scraper.timer     (12:00 UTC / 17:30 IST)
  │       └─ genlab-affiliate-link-check.timer  (03:45 UTC / 09:15 IST)
  │
  └─ /mnt/genlab-media/ (50GB Hetzner Volume)
      ├─ clips/       ← yt-dlp downloads
      ├─ rendered/    ← FFmpeg output
      ├─ assets/      ← logos, overlays
      └─ .scores/     ← QuotaManager
```

## New Services Detail

### 1. Render Worker — `genlab-render-worker.service`

New always-on service. Blocks on Redis queue, processes one video at a time.

**Queue protocol:**
- Pipeline's `render_visuals` stage pushes job to Redis list `render_jobs` as JSON:
  ```json
  {"blueprint_id": "1234", "niche_id": "gaming", "video_path": "/mnt/genlab-media/clips/abc.mp4", "config": {...}}
  ```
- Worker does `BRPOP render_jobs 0` (blocking pop, infinite wait)
- On pop: runs `VideoCompositor.render()` with the config
- On success: writes output to `/mnt/genlab-media/rendered/`, updates blueprint to `VISUAL_READY`
- On failure: logs error, marks blueprint `RENDER_FAILED`, continues to next job

**Code change required:** `genlab_core.pipeline.stages.render_visuals` needs a ~20-line modification to push to Redis queue instead of rendering inline. New file `genlab_core.media.render_worker` (~100 lines) implements the BRPOP loop.

```ini
[Unit]
Description=GenLab Render Worker
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=exec
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=REDIS_HOST=127.0.0.1
Environment=REDIS_PORT=6379
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.media.render_worker
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 2. Engagement Webhook — `genlab-webhook.service`

Existing code (`genlab_core.engagement.webhook`), just needs a systemd unit.

```ini
[Unit]
Description=GenLab Meta Webhook Receiver
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/uvicorn \
    genlab_core.engagement.webhook:app \
    --host 127.0.0.1 --port 8765
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 3. Engagement Worker — `genlab-engagement-worker.service`

Dramatiq worker for comment reply processing.

```ini
[Unit]
Description=GenLab Engagement Worker (Dramatiq)
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=exec
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/genlab-core
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=REDIS_HOST=127.0.0.1
Environment=REDIS_PORT=6379
ExecStart=/opt/genlab/.venv/bin/python -m dramatiq \
    genlab_core.engagement.tasks \
    --queues engagement_high engagement_normal engagement_low \
    --processes 1 --threads 2
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

Note: reduced from 2 processes / 4 threads to 1 process / 2 threads to save RAM on 4GB box.

### 4. Quota Monitor — `genlab-quota-monitor.service`

```ini
[Unit]
Description=GenLab Disk Quota Monitor
After=network-online.target

[Service]
Type=exec
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.storage.quota_daemon \
    --config /opt/genlab/genlab-core/config/disk_quota.yaml \
    --interval 60
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

`disk_quota.yaml` will need the media path updated to `/mnt/genlab-media/`.

## Pipeline Timer Template

All 5 niche pipeline timers follow the same pattern. Example for gaming:

**Timer:**
```ini
[Unit]
Description=GenLab Gaming Pipeline Timer

[Timer]
OnCalendar=*-*-* 04:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

**Service:**
```ini
[Unit]
Description=GenLab Gaming Pipeline
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/CriticalRush
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
ExecStart=/bin/bash /opt/genlab/CriticalRush/runbooks/daily_intel.sh
TimeoutSec=7200
```

Each niche's `daily_intel.sh` sets up its own working directory and runs the pipeline stages. The `WorkingDirectory` matches what the local plist used.

### Pipeline schedule (all times UTC)

| UTC | IST | Service |
|-----|-----|---------|
| 01:00 | 06:30 | cleanup, pg-backup |
| 02:30 | 08:00 | pipeline-ai (BlackboxBrief) |
| 02:45 | 08:15 | morning-briefing |
| 03:45 | 09:15 | affiliate-link-check |
| 04:00 | 09:30 | pipeline-gaming (CriticalRush) |
| 05:00 | 10:30 | shared-ingestion |
| 06:00 | 11:30 | pipeline-anime (FrameDrift) |
| 06:35 | 12:05 | publisher (run 1) |
| 06:45 | 12:15 | insights-collector (run 1) |
| 08:00 | 13:30 | pipeline-movies (SpliceReel) |
| 09:00 | 14:30 | config-updater (weekly Mon) |
| 10:00 | 15:30 | pipeline-sports (ClutchWire) |
| 10:30 | 16:00 | publisher (run 2) |
| 12:00 | 17:30 | affiliate-scraper |
| 12:30 | 18:00 | insights-collector (run 2) |
| 13:30 | 19:00 | feedback-collector |
| 14:30 | 20:00 | audience-collector |
| 16:30 | 22:00 | daily-verify |
| — | — | spike-detector (every 5min) |
| — | — | viral-detector (every 2h) |
| — | — | hook-trainer (weekly Sun 05:00) |
| — | — | preference-collector (weekly Sun 04:00) |

## Hetzner Volume Setup

```bash
# Create 50GB volume in Hetzner console (or via API)
# Attach to server, then on server:
mkfs.ext4 /dev/disk/by-id/scsi-0HC_Volume_XXXXXXXX
mkdir -p /mnt/genlab-media
mount /dev/disk/by-id/scsi-0HC_Volume_XXXXXXXX /mnt/genlab-media

# Add to fstab for auto-mount on reboot
echo '/dev/disk/by-id/scsi-0HC_Volume_XXXXXXXX /mnt/genlab-media ext4 discard,nofail,defaults 0 0' >> /etc/fstab

# Set ownership
chown -R genlab:genlab /mnt/genlab-media
mkdir -p /mnt/genlab-media/{clips,rendered,assets,.scores}
```

Copy channel logo assets from repo to volume:
```bash
cp -r /opt/genlab/BlackboxBrief/assets/ /mnt/genlab-media/assets/blackboxbrief/
cp -r /opt/genlab/CriticalRush/niches/gaming/assets/ /mnt/genlab-media/assets/gaming/
cp -r /opt/genlab/ClutchWire/assets/ /mnt/genlab-media/assets/clutchwire/
cp -r /opt/genlab/SpliceReel/assets/ /mnt/genlab-media/assets/splicereel/
cp -r /opt/genlab/FrameDrift/assets/ /mnt/genlab-media/assets/framedrift/
```

## Caddy Update

```
ops.aspirehub.ai {
    reverse_proxy localhost:5151
    encode gzip

    handle_path /webhooks/meta/* {
        reverse_proxy localhost:8765
    }

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

After updating, register the new webhook URL in Meta Developer Console:
`https://ops.aspirehub.ai/webhooks/meta`

## Code Changes Required

### 1. Render queue integration (~20 lines)

**Modify:** `genlab-core/src/genlab_core/pipeline/stages/render_visuals.py`

In the render stage, after preparing the render config but before calling FFmpeg:
- If `RENDER_MODE=queue` (env var), serialize job to JSON and `LPUSH render_jobs`
- Set blueprint status to `RENDER_QUEUED` and return
- If `RENDER_MODE=inline` (default, backward compatible), render as before

### 2. Render worker (~100 lines)

**Create:** `genlab-core/src/genlab_core/media/render_worker.py`

- `BRPOP render_jobs 0` loop
- Deserialize job JSON
- Call existing `VideoCompositor.render()` with the config
- Update blueprint status (VISUAL_READY or RENDER_FAILED)
- Log timing and result
- Retry once on failure, then mark failed

### 3. Media path configuration

**Modify:** `genlab-core/config/disk_quota.yaml` — add cloud media path
**Modify:** Pipeline `daily_intel.sh` scripts — set `GENLAB_MEDIA_ROOT=/mnt/genlab-media` or read from `.env`

### 4. Cleanup script update

**Modify:** `scripts/cleanup_all.sh` — target `/mnt/genlab-media/` instead of `.tmp/`

## Assume-Unchanged Config Files

Per Phase 1 lesson: config files with real credentials/IDs are `assume-unchanged` in git. After `git pull` on the server, these need to be re-deployed via scp:

```bash
# Check which files are assume-unchanged
git ls-files -v | grep ^h

# scp each one to server after git pull
```

## Estimated RAM Budget (Full Load)

| Component | Steady State | Peak |
|---|---|---|
| OS + system | ~350 MB | ~400 MB |
| Docker (PG + Redis) | ~300 MB | ~400 MB |
| Caddy | ~30 MB | ~30 MB |
| Dashboard (gunicorn) | ~200 MB | ~250 MB |
| Engagement poller | ~150 MB | ~200 MB |
| Engagement webhook | ~80 MB | ~120 MB |
| Engagement worker (1p/2t) | ~200 MB | ~400 MB |
| Render worker (idle) | ~50 MB | — |
| Render worker (encoding) | — | ~600 MB |
| Pipeline (1 niche) | — | ~400 MB |
| Quota monitor | ~30 MB | ~30 MB |
| Cron jobs (transient) | — | ~200 MB |
| **Total** | **~1.4 GB** | **~3.0 GB** |

Leaves ~700MB headroom at peak. Peak only occurs when a pipeline is running AND the render worker is encoding simultaneously — which is the expected overlap since the pipeline pushes to the queue and the worker picks up immediately.

If RAM gets tight, the engagement worker can be reduced to `--processes 1 --threads 1` (saves ~150MB).

## Success Criteria

- [ ] All 5 niche pipelines fire on schedule and produce blueprints
- [ ] Render worker processes queued videos and marks VISUAL_READY
- [ ] Publisher publishes approved content at 06:35 + 10:30 UTC
- [ ] Engagement webhook receives Meta callbacks via `ops.aspirehub.ai/webhooks/meta`
- [ ] Engagement worker processes reply queue (dramatiq)
- [ ] All 12 cron timers fire on schedule
- [ ] Hetzner Volume mounted at `/mnt/genlab-media/`, used for all video I/O
- [ ] Cleanup cron keeps volume under 30GB
- [ ] `launchctl list | grep genlab` returns nothing on local Mac
- [ ] Server RAM stays under 3.5GB, disk under 35GB
- [ ] Server survives reboot with all services auto-starting
- [ ] FFmpeg encodes complete within 2x local time (acceptable for 5 reels/day)

## Out of Scope

- Server monitoring/alerting (Grafana, Prometheus)
- Automated deploys (GitHub Actions CI/CD)
- Database replication or failover
- Multi-server architecture
- CDN for rendered video delivery
