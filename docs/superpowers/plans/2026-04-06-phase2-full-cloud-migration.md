# Phase 2 — Full Cloud Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate all 24 remaining local launchd services to the Hetzner cloud server so local Mac runs zero GenLab processes.

**Architecture:** Same hybrid approach as Phase 1 — Postgres/Redis in Docker, everything else native systemd. Pipelines run inline (staggered 90min apart, no concurrent FFmpeg). Media storage on a 50GB Hetzner Volume. Render queue deferred to Phase 3 (inline rendering is fine for 5 reels/day with staggered schedules — the render stage is deeply integrated into each niche's pipeline and would require modifying 5 niche-specific render stages to decouple).

**Tech Stack:** systemd, Hetzner Volume (ext4), Caddy, uv, FFmpeg, Redis, Dramatiq

**Spec:** `docs/superpowers/specs/2026-04-06-phase2-full-cloud-migration-design.md`

---

## File Structure

```
deploy/
├── systemd/                              (Phase 1 — already exists)
│   ├── genlab-dashboard.service
│   ├── genlab-engagement-poller.service
│   ├── genlab-metric-collector.{service,timer}
│   ├── genlab-token-refresh.{service,timer}
│   └── genlab-db-maintenance.{service,timer}
│
├── systemd-phase2/                       (NEW — all Phase 2 units)
│   ├── genlab-pipeline-ai.{service,timer}
│   ├── genlab-pipeline-gaming.{service,timer}
│   ├── genlab-pipeline-sports.{service,timer}
│   ├── genlab-pipeline-movies.{service,timer}
│   ├── genlab-pipeline-anime.{service,timer}
│   ├── genlab-publisher.{service,timer}
│   ├── genlab-shared-ingestion.{service,timer}
│   ├── genlab-webhook.service
│   ├── genlab-engagement-worker.service
│   ├── genlab-quota-monitor.service
│   ├── genlab-insights-collector.{service,timer}
│   ├── genlab-spike-detector.{service,timer}
│   ├── genlab-viral-detector.{service,timer}
│   ├── genlab-feedback-collector.{service,timer}
│   ├── genlab-audience-collector.{service,timer}
│   ├── genlab-hook-trainer.{service,timer}
│   ├── genlab-config-updater.{service,timer}
│   ├── genlab-preference-collector.{service,timer}
│   ├── genlab-cleanup.{service,timer}
│   ├── genlab-pg-backup.{service,timer}
│   ├── genlab-daily-verify.{service,timer}
│   ├── genlab-morning-briefing.{service,timer}
│   ├── genlab-affiliate-scraper.{service,timer}
│   └── genlab-affiliate-link-check.{service,timer}
│
├── caddy/
│   └── Caddyfile                         (MODIFY — add webhook route)
│
└── scripts/
    └── setup-volume.sh                   (NEW — Hetzner Volume mount)
```

No application code changes in this phase. All pipelines run as-is (inline render).

---

## Task 1: Create Hetzner Volume and mount script

**Files:**
- Create: `deploy/scripts/setup-volume.sh`

- [ ] **Step 1: Create `deploy/scripts/setup-volume.sh`**

```bash
#!/usr/bin/env bash
# setup-volume.sh — Mount Hetzner Volume for GenLab media storage.
#
# Prerequisites: Create a 50GB volume in Hetzner Console and attach to server.
# The volume device will appear as /dev/disk/by-id/scsi-0HC_Volume_<ID>
#
# Usage: sudo bash deploy/scripts/setup-volume.sh <volume-device>
# Example: sudo bash deploy/scripts/setup-volume.sh /dev/disk/by-id/scsi-0HC_Volume_12345678

set -euo pipefail

DEVICE="${1:?Usage: $0 <volume-device-path>}"
MOUNT="/mnt/genlab-media"

if ! [ -b "$DEVICE" ]; then
    echo "ERROR: $DEVICE is not a block device"
    exit 1
fi

# Format if not already formatted
if ! blkid "$DEVICE" | grep -q ext4; then
    echo "Formatting $DEVICE as ext4..."
    mkfs.ext4 "$DEVICE"
fi

# Mount
mkdir -p "$MOUNT"
mount "$DEVICE" "$MOUNT"

# Add to fstab if not already there
if ! grep -q "$MOUNT" /etc/fstab; then
    echo "$DEVICE $MOUNT ext4 discard,nofail,defaults 0 0" >> /etc/fstab
    echo "Added to /etc/fstab"
fi

# Create directory structure
mkdir -p "$MOUNT"/{clips,rendered,assets,.scores}

# Set ownership
chown -R genlab:genlab "$MOUNT"

echo "Volume mounted at $MOUNT"
df -h "$MOUNT"
```

- [ ] **Step 2: Make executable**

```bash
chmod +x deploy/scripts/setup-volume.sh
```

- [ ] **Step 3: Commit**

```bash
git add deploy/scripts/setup-volume.sh
git commit -m "feat(deploy): add Hetzner Volume mount script"
```

---

## Task 2: Create always-on service units (webhook, worker, quota monitor)

**Files:**
- Create: `deploy/systemd-phase2/genlab-webhook.service`
- Create: `deploy/systemd-phase2/genlab-engagement-worker.service`
- Create: `deploy/systemd-phase2/genlab-quota-monitor.service`

- [ ] **Step 1: Create `deploy/systemd-phase2/genlab-webhook.service`**

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

- [ ] **Step 2: Create `deploy/systemd-phase2/genlab-engagement-worker.service`**

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

- [ ] **Step 3: Create `deploy/systemd-phase2/genlab-quota-monitor.service`**

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
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.storage.quota_daemon \
    --config /opt/genlab/genlab-core/config/disk_quota.yaml \
    --interval 60
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 4: Commit**

```bash
git add deploy/systemd-phase2/
git commit -m "feat(deploy): add Phase 2 always-on service units"
```

---

## Task 3: Create pipeline timer units (5 niches)

**Files:**
- Create: 10 files — `genlab-pipeline-{ai,gaming,sports,movies,anime}.{service,timer}`

- [ ] **Step 1: Create all 5 pipeline service units**

Each pipeline service runs the channel's `daily_intel.sh` (or `cron_wrapper.sh` for BB).
All are `Type=oneshot` triggered by timers.

`deploy/systemd-phase2/genlab-pipeline-ai.service`:
```ini
[Unit]
Description=GenLab AI Creators Pipeline (BlackboxBrief)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/BlackboxBrief
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/BlackboxBrief/runbooks/cron_wrapper.sh
TimeoutSec=14400
```

`deploy/systemd-phase2/genlab-pipeline-gaming.service`:
```ini
[Unit]
Description=GenLab Gaming Pipeline (CriticalRush)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/CriticalRush
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/CriticalRush/runbooks/daily_intel.sh
TimeoutSec=7200
```

`deploy/systemd-phase2/genlab-pipeline-sports.service`:
```ini
[Unit]
Description=GenLab Sports Pipeline (ClutchWire)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/ClutchWire
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/ClutchWire/runbooks/daily_intel.sh
TimeoutSec=3600
```

`deploy/systemd-phase2/genlab-pipeline-movies.service`:
```ini
[Unit]
Description=GenLab Movies Pipeline (SpliceReel)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/SpliceReel
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/SpliceReel/runbooks/daily_intel.sh
TimeoutSec=3600
```

`deploy/systemd-phase2/genlab-pipeline-anime.service`:
```ini
[Unit]
Description=GenLab Anime Pipeline (FrameDrift)
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/FrameDrift
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/FrameDrift/runbooks/daily_intel.sh
TimeoutSec=3600
```

- [ ] **Step 2: Create all 5 pipeline timer units**

`deploy/systemd-phase2/genlab-pipeline-ai.timer`:
```ini
[Unit]
Description=GenLab AI Creators Pipeline Timer

[Timer]
OnCalendar=*-*-* 02:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-pipeline-gaming.timer`:
```ini
[Unit]
Description=GenLab Gaming Pipeline Timer

[Timer]
OnCalendar=*-*-* 04:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-pipeline-sports.timer`:
```ini
[Unit]
Description=GenLab Sports Pipeline Timer

[Timer]
OnCalendar=*-*-* 10:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-pipeline-movies.timer`:
```ini
[Unit]
Description=GenLab Movies Pipeline Timer

[Timer]
OnCalendar=*-*-* 08:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-pipeline-anime.timer`:
```ini
[Unit]
Description=GenLab Anime Pipeline Timer

[Timer]
OnCalendar=*-*-* 06:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Commit**

```bash
git add deploy/systemd-phase2/genlab-pipeline-*
git commit -m "feat(deploy): add 5 niche pipeline timer units (staggered 90min)"
```

---

## Task 4: Create publisher + ingestion + analytics timer units

**Files:**
- Create: 18 files — service+timer pairs for publisher, shared-ingestion, insights-collector, spike-detector, viral-detector, feedback-collector, audience-collector, hook-trainer, config-updater, preference-collector

*Note: Only key units shown in full. All follow the same pattern: oneshot service + calendar/interval timer.*

- [ ] **Step 1: Create publisher service + timer**

`deploy/systemd-phase2/genlab-publisher.service`:
```ini
[Unit]
Description=GenLab Publisher
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
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.publishing.publish_all_platforms --niche all
TimeoutSec=1800
```

`deploy/systemd-phase2/genlab-publisher.timer`:
```ini
[Unit]
Description=GenLab Publisher Timer

[Timer]
OnCalendar=*-*-* 06:35:00 UTC
OnCalendar=*-*-* 10:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Create shared-ingestion service + timer**

`deploy/systemd-phase2/genlab-shared-ingestion.service`:
```ini
[Unit]
Description=GenLab Shared Ingestion
After=network-online.target docker.service

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=BACKLOG_CONFIG_PATH=/opt/genlab/genlab-core/config/lists_config.yaml
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.pipeline.shared_ingestion
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-shared-ingestion.timer`:
```ini
[Unit]
Description=GenLab Shared Ingestion Timer

[Timer]
OnCalendar=*-*-* 05:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Create insights-collector service + timer**

`deploy/systemd-phase2/genlab-insights-collector.service`:
```ini
[Unit]
Description=GenLab Insights Collector
After=network-online.target docker.service

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/scripts/collect_all_insights.sh
TimeoutSec=1800
```

`deploy/systemd-phase2/genlab-insights-collector.timer`:
```ini
[Unit]
Description=GenLab Insights Collector Timer

[Timer]
OnCalendar=*-*-* 06:45:00 UTC
OnCalendar=*-*-* 12:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Create spike-detector + viral-detector (interval timers)**

`deploy/systemd-phase2/genlab-spike-detector.service`:
```ini
[Unit]
Description=GenLab Spike Detector
After=network-online.target docker.service

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/CriticalRush
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python -m niches.gaming.flows.spike_detector_flow --once
TimeoutSec=120
```

`deploy/systemd-phase2/genlab-spike-detector.timer`:
```ini
[Unit]
Description=GenLab Spike Detector Timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
AccuracySec=30s

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-viral-detector.service`:
```ini
[Unit]
Description=GenLab Viral Detector
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python /opt/genlab/scripts/viral_detector.py
TimeoutSec=300
```

`deploy/systemd-phase2/genlab-viral-detector.timer`:
```ini
[Unit]
Description=GenLab Viral Detector Timer

[Timer]
OnBootSec=10min
OnUnitActiveSec=2h
AccuracySec=5min

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Create remaining analytics timers**

`deploy/systemd-phase2/genlab-feedback-collector.service`:
```ini
[Unit]
Description=GenLab Feedback Collector
After=network-online.target docker.service

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab/CriticalRush
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python scripts/collect_feedback.py
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-feedback-collector.timer`:
```ini
[Unit]
Description=GenLab Feedback Collector Timer

[Timer]
OnCalendar=*-*-* 13:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-audience-collector.service`:
```ini
[Unit]
Description=GenLab Audience Collector
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.scripts.collect_audience_metrics
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-audience-collector.timer`:
```ini
[Unit]
Description=GenLab Audience Collector Timer

[Timer]
OnCalendar=*-*-* 14:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-hook-trainer.service`:
```ini
[Unit]
Description=GenLab Hook Trainer
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.scripts.train_hook_classifier
TimeoutSec=300
```

`deploy/systemd-phase2/genlab-hook-trainer.timer`:
```ini
[Unit]
Description=GenLab Hook Trainer Timer (Weekly Sunday)

[Timer]
OnCalendar=Sun *-*-* 05:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-config-updater.service`:
```ini
[Unit]
Description=GenLab Config Updater
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python -m genlab_core.scripts.run_config_update
TimeoutSec=300
```

`deploy/systemd-phase2/genlab-config-updater.timer`:
```ini
[Unit]
Description=GenLab Config Updater Timer (Weekly Monday)

[Timer]
OnCalendar=Mon *-*-* 09:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-preference-collector.service`:
```ini
[Unit]
Description=GenLab Preference Collector
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python -c "from genlab_core.learning.preference_collector import collect_weekly_pairs; print(f'Pairs: {collect_weekly_pairs(window_days=7)}')"
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-preference-collector.timer`:
```ini
[Unit]
Description=GenLab Preference Collector Timer (Weekly Sunday)

[Timer]
OnCalendar=Sun *-*-* 04:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Commit**

```bash
git add deploy/systemd-phase2/
git commit -m "feat(deploy): add publisher, ingestion, and analytics timer units"
```

---

## Task 5: Create ops + affiliate timer units

**Files:**
- Create: 10 files — service+timer pairs for cleanup, pg-backup, daily-verify, morning-briefing, affiliate-scraper, affiliate-link-check

- [ ] **Step 1: Create ops service + timer units**

`deploy/systemd-phase2/genlab-cleanup.service`:
```ini
[Unit]
Description=GenLab Media Cleanup
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/scripts/cleanup_all.sh
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-cleanup.timer`:
```ini
[Unit]
Description=GenLab Cleanup Timer

[Timer]
OnCalendar=*-*-* 01:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-pg-backup.service`:
```ini
[Unit]
Description=GenLab PostgreSQL Backup
After=network-online.target docker.service

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/scripts/pg_backup.sh
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-pg-backup.timer`:
```ini
[Unit]
Description=GenLab PG Backup Timer

[Timer]
OnCalendar=*-*-* 01:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-daily-verify.service`:
```ini
[Unit]
Description=GenLab Daily Verification
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/bin/bash /opt/genlab/scripts/verify_daily_cycle.sh
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-daily-verify.timer`:
```ini
[Unit]
Description=GenLab Daily Verify Timer

[Timer]
OnCalendar=*-*-* 16:30:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-morning-briefing.service`:
```ini
[Unit]
Description=GenLab Morning Briefing
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python /opt/genlab/scripts/morning_briefing.py
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-morning-briefing.timer`:
```ini
[Unit]
Description=GenLab Morning Briefing Timer

[Timer]
OnCalendar=*-*-* 02:45:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 2: Create affiliate service + timer units**

`deploy/systemd-phase2/genlab-affiliate-scraper.service`:
```ini
[Unit]
Description=GenLab Affiliate Revenue Scraper
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python /opt/genlab/scripts/scrape_affiliate_revenue.py
TimeoutSec=600
```

`deploy/systemd-phase2/genlab-affiliate-scraper.timer`:
```ini
[Unit]
Description=GenLab Affiliate Scraper Timer

[Timer]
OnCalendar=*-*-* 12:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

`deploy/systemd-phase2/genlab-affiliate-link-check.service`:
```ini
[Unit]
Description=GenLab Affiliate Link Checker
After=network-online.target

[Service]
Type=oneshot
User=genlab
Group=genlab
WorkingDirectory=/opt/genlab
EnvironmentFile=/opt/genlab/.env
Environment=GENLAB_PROJECT_ROOT=/opt/genlab
Environment=HOME=/opt/genlab
ExecStart=/opt/genlab/.venv/bin/python /opt/genlab/genlab-core/scripts/check_affiliate_links.py
TimeoutSec=300
```

`deploy/systemd-phase2/genlab-affiliate-link-check.timer`:
```ini
[Unit]
Description=GenLab Affiliate Link Check Timer

[Timer]
OnCalendar=*-*-* 03:45:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Commit**

```bash
git add deploy/systemd-phase2/
git commit -m "feat(deploy): add ops and affiliate timer units"
```

---

## Task 6: Update Caddy config for webhook route

**Files:**
- Modify: `deploy/caddy/Caddyfile`

- [ ] **Step 1: Update Caddyfile to add webhook route**

Replace the entire file with:

```
ops.aspirehub.ai {
    reverse_proxy localhost:5151

    handle_path /webhooks/meta/* {
        reverse_proxy localhost:8765
    }

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

- [ ] **Step 2: Commit**

```bash
git add deploy/caddy/Caddyfile
git commit -m "feat(deploy): add Meta webhook route to Caddyfile"
```

---

## Task 7: Create Hetzner Volume and mount on server

**Prerequisites:** All files from Tasks 1-6 committed and pushed.

- [ ] **Step 1: Push all commits to GitHub**

```bash
git push origin main
```

- [ ] **Step 2: Create 50GB volume in Hetzner Console**

In Hetzner Cloud Console:
1. Go to Volumes → Create Volume
2. Size: 50 GB
3. Location: Nuremberg (nbg1) — same as server
4. Server: ubuntu-4gb-nbg1-1
5. Format: Automount disabled (we'll format manually)
6. Click Create

Note the volume device path (e.g., `/dev/disk/by-id/scsi-0HC_Volume_12345678`).

- [ ] **Step 3: Mount the volume on the server**

```bash
ssh root@46.224.237.56 'ls /dev/disk/by-id/scsi-0HC_Volume_*'
```

Note the device path, then:

```bash
ssh root@46.224.237.56 'bash /opt/genlab/deploy/scripts/setup-volume.sh /dev/disk/by-id/scsi-0HC_Volume_XXXXX'
```

Expected: "Volume mounted at /mnt/genlab-media" with size info.

- [ ] **Step 4: Verify mount**

```bash
ssh root@46.224.237.56 'df -h /mnt/genlab-media && ls -la /mnt/genlab-media/'
```

Expected: ~50GB volume, dirs clips/ rendered/ assets/ .scores/

- [ ] **Step 5: Copy channel logo assets to volume**

```bash
ssh root@46.224.237.56 '
for ch in BlackboxBrief CriticalRush ClutchWire SpliceReel FrameDrift; do
    src="/opt/genlab/$ch/assets"
    [ -d "$src" ] && cp -r "$src" "/mnt/genlab-media/assets/$(echo $ch | tr A-Z a-z)/" && echo "Copied $ch assets"
done
# Gaming assets are nested differently
[ -d /opt/genlab/CriticalRush/niches/gaming/assets ] && \
    cp -r /opt/genlab/CriticalRush/niches/gaming/assets/* /mnt/genlab-media/assets/criticalrush/ 2>/dev/null
chown -R genlab:genlab /mnt/genlab-media/assets/
'
```

---

## Task 8: Server setup — pull code, fix paths, deploy configs

- [ ] **Step 1: Pull latest code on server**

```bash
ssh root@46.224.237.56 'cd /opt/genlab && sudo -u genlab git pull'
```

- [ ] **Step 2: Create uv symlink for genlab user**

The `daily_intel.sh` script uses `${HOME}/.local/bin/uv`. On the server, genlab's HOME is `/opt/genlab`, so it looks for `/opt/genlab/.local/bin/uv`. Create the symlink:

```bash
ssh root@46.224.237.56 'mkdir -p /opt/genlab/.local/bin && ln -sf /usr/local/bin/uv /opt/genlab/.local/bin/uv && chown -R genlab:genlab /opt/genlab/.local'
```

- [ ] **Step 3: Create .tmp directory structure for pipeline logs**

```bash
ssh root@46.224.237.56 'mkdir -p /opt/genlab/.tmp/{logs,runs,cache,media} && chown -R genlab:genlab /opt/genlab/.tmp'
```

- [ ] **Step 4: Symlink media to volume (so existing code paths work)**

Many scripts reference `.tmp/media/` for video downloads. Symlink to volume:

```bash
ssh root@46.224.237.56 '
rm -rf /opt/genlab/.tmp/media/videos 2>/dev/null
ln -sf /mnt/genlab-media/clips /opt/genlab/.tmp/media/videos
ln -sf /mnt/genlab-media/rendered /opt/genlab/.tmp/media/rendered
chown -h genlab:genlab /opt/genlab/.tmp/media/videos /opt/genlab/.tmp/media/rendered
'
```

- [ ] **Step 5: Deploy assume-unchanged config files**

```bash
scp /Users/anarchistsid/GenLab/genlab-core/config/engagement_pollers.yaml root@46.224.237.56:/opt/genlab/genlab-core/config/engagement_pollers.yaml
```

Check for other assume-unchanged files:
```bash
git ls-files -v | grep ^h
```

scp each one to the server.

- [ ] **Step 6: Fix ownership**

```bash
ssh root@46.224.237.56 'chown -R genlab:genlab /opt/genlab'
```

---

## Task 9: Install and start all Phase 2 systemd units

- [ ] **Step 1: Copy all Phase 2 units to systemd**

```bash
ssh root@46.224.237.56 'cp /opt/genlab/deploy/systemd-phase2/*.service /opt/genlab/deploy/systemd-phase2/*.timer /etc/systemd/system/ && systemctl daemon-reload'
```

- [ ] **Step 2: Start always-on services**

```bash
ssh root@46.224.237.56 '
systemctl enable --now genlab-webhook.service
systemctl enable --now genlab-engagement-worker.service
systemctl enable --now genlab-quota-monitor.service
'
```

Verify:
```bash
ssh root@46.224.237.56 'systemctl is-active genlab-webhook genlab-engagement-worker genlab-quota-monitor'
```

Expected: `active` / `active` / `active`

- [ ] **Step 3: Enable pipeline timers**

```bash
ssh root@46.224.237.56 '
systemctl enable --now genlab-pipeline-ai.timer
systemctl enable --now genlab-pipeline-gaming.timer
systemctl enable --now genlab-pipeline-sports.timer
systemctl enable --now genlab-pipeline-movies.timer
systemctl enable --now genlab-pipeline-anime.timer
'
```

- [ ] **Step 4: Enable publisher + ingestion timers**

```bash
ssh root@46.224.237.56 '
systemctl enable --now genlab-publisher.timer
systemctl enable --now genlab-shared-ingestion.timer
'
```

- [ ] **Step 5: Enable analytics + learning timers**

```bash
ssh root@46.224.237.56 '
systemctl enable --now genlab-insights-collector.timer
systemctl enable --now genlab-spike-detector.timer
systemctl enable --now genlab-viral-detector.timer
systemctl enable --now genlab-feedback-collector.timer
systemctl enable --now genlab-audience-collector.timer
systemctl enable --now genlab-hook-trainer.timer
systemctl enable --now genlab-config-updater.timer
systemctl enable --now genlab-preference-collector.timer
'
```

- [ ] **Step 6: Enable ops + affiliate timers**

```bash
ssh root@46.224.237.56 '
systemctl enable --now genlab-cleanup.timer
systemctl enable --now genlab-pg-backup.timer
systemctl enable --now genlab-daily-verify.timer
systemctl enable --now genlab-morning-briefing.timer
systemctl enable --now genlab-affiliate-scraper.timer
systemctl enable --now genlab-affiliate-link-check.timer
'
```

- [ ] **Step 7: Verify all timers are scheduled**

```bash
ssh root@46.224.237.56 'systemctl list-timers genlab-* --no-pager'
```

Expected: All timers listed with next trigger times.

---

## Task 10: Update Caddy and configure Meta webhook

- [ ] **Step 1: Update Caddyfile on server**

```bash
ssh root@46.224.237.56 'cp /opt/genlab/deploy/caddy/Caddyfile /etc/caddy/Caddyfile && caddy validate --config /etc/caddy/Caddyfile && systemctl reload caddy'
```

- [ ] **Step 2: Verify webhook endpoint responds**

```bash
ssh root@46.224.237.56 'curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8765/'
```

Expected: `200` or `404` (depends on route — any non-connection-error means uvicorn is serving).

```bash
curl -s -o /dev/null -w "%{http_code}" https://ops.aspirehub.ai/webhooks/meta/
```

Expected: Response from the webhook app (not a Caddy 502).

- [ ] **Step 3: Update Meta webhook URL (manual)**

In Meta Developer Console (https://developers.facebook.com):
1. Go to your app → Webhooks
2. Update the callback URL to: `https://ops.aspirehub.ai/webhooks/meta`
3. Verify token should already match what's in `.env`

---

## Task 11: Test a pipeline manually

- [ ] **Step 1: Run gaming pipeline manually**

```bash
ssh root@46.224.237.56 'systemctl start genlab-pipeline-gaming.service'
```

- [ ] **Step 2: Monitor its progress**

```bash
ssh root@46.224.237.56 'journalctl -u genlab-pipeline-gaming.service --no-pager -f'
```

Watch for: fetch trending → score → compose → write → render (FFmpeg) → push to backlog.

Press Ctrl+C when done or it completes.

- [ ] **Step 3: Verify output**

```bash
ssh root@46.224.237.56 'ls /mnt/genlab-media/clips/ /mnt/genlab-media/rendered/ 2>/dev/null | head -10'
```

Expected: Video files in clips/ and/or rendered/ (depending on whether the pipeline found eligible content).

---

## Task 12: Unload ALL local launchd plists

- [ ] **Step 1: Unload every genlab plist**

```bash
launchctl list | grep genlab | awk '{print $3}' | while read label; do
    launchctl unload ~/Library/LaunchAgents/${label}.plist 2>/dev/null && echo "Unloaded: $label" || echo "Skip: $label"
done
```

- [ ] **Step 2: Verify nothing remains**

```bash
launchctl list | grep genlab
```

Expected: No output (zero genlab services running locally).

---

## Task 13: Full verification and reboot test

- [ ] **Step 1: Run comprehensive checks**

```bash
echo "=== Dashboard ==="
curl -s -o /dev/null -w "%{http_code}" https://ops.aspirehub.ai/

echo "=== Always-on services ==="
ssh root@46.224.237.56 'systemctl is-active genlab-dashboard genlab-engagement-poller genlab-webhook genlab-engagement-worker genlab-quota-monitor'

echo "=== Docker ==="
ssh root@46.224.237.56 'docker ps --format "{{.Names}}: {{.Status}}"'

echo "=== Timers ==="
ssh root@46.224.237.56 'systemctl list-timers genlab-* --no-pager | wc -l'

echo "=== Volume ==="
ssh root@46.224.237.56 'df -h /mnt/genlab-media'

echo "=== RAM ==="
ssh root@46.224.237.56 'free -h | head -2'

echo "=== Local services ==="
launchctl list | grep -c genlab || echo "0"
```

Expected: Dashboard 200, 5 active services, 2 healthy containers, ~20+ timers, volume mounted, RAM under 3GB, 0 local services.

- [ ] **Step 2: Reboot server**

```bash
ssh root@46.224.237.56 'reboot'
```

Wait 90 seconds:

```bash
sleep 90 && ssh root@46.224.237.56 '
echo "Uptime: $(uptime)"
docker ps --format "{{.Names}}: {{.Status}}"
systemctl is-active genlab-dashboard genlab-webhook genlab-engagement-worker genlab-engagement-poller genlab-quota-monitor
systemctl list-timers genlab-* --no-pager | wc -l
df -h /mnt/genlab-media
'
```

Expected: All containers healthy, all 5 always-on services active, all timers scheduled, volume mounted.

- [ ] **Step 3: Verify HTTPS after reboot**

```bash
curl -s -o /dev/null -w "%{http_code}" https://ops.aspirehub.ai/
```

Expected: `200` or `302`.

---

## Post-Deployment Notes

### Updating code
```bash
ssh root@46.224.237.56 'cd /opt/genlab && sudo -u genlab git pull && uv sync --frozen'
# Restart affected services
ssh root@46.224.237.56 'systemctl restart genlab-dashboard genlab-engagement-poller genlab-webhook genlab-engagement-worker'
# Timers auto-pick up new code on next trigger
```

### If home IP changes (PostgreSQL access)
```bash
ssh root@46.224.237.56 'ufw status numbered'  # find old rule
ssh root@46.224.237.56 'ufw delete RULE_NUMBER'
ssh root@46.224.237.56 'ufw allow from NEW_IP to any port 5432'
```

### Monitoring service health
```bash
# All services at a glance
ssh root@46.224.237.56 'systemctl list-units genlab-* --no-pager'
# Failed services
ssh root@46.224.237.56 'systemctl --failed | grep genlab'
# Recent logs for any service
ssh root@46.224.237.56 'journalctl -u genlab-pipeline-gaming --no-pager -n 30'
```

### Future: Render queue (Phase 3)
The render currently runs inline within each niche's pipeline stage. A render queue would:
1. Create `genlab_core.media.render_worker` (BRPOP from Redis `render_jobs`)
2. Modify each niche's render stage to push to queue when `RENDER_MODE=queue`
3. Decouple pipeline speed from FFmpeg encoding time

This is worth doing when: adding more niches, video encoding time exceeds 10min, or you upgrade to a box with 4+ vCPU where parallel renders are beneficial.
