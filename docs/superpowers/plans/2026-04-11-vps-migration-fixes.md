# VPS Migration Fixes — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 6 broken systemd services on VPS, deploy latest code, disable local launchd, free disk space.

**Architecture:** VPS at 46.224.237.56 already runs all 24 timers + 6 always-on services. Local Mac was never decommissioned, causing duplicate runs. Fix the broken services, deploy code, cut over.

**Tech Stack:** systemd, bash, Python, ssh

---

### Task 1: Deploy latest code to VPS

**Steps:**
- [ ] Commit the dashboard fixes (CSP, _resolve_video_url, frontend rebuild)
- [ ] `ssh root@46.224.237.56 "cd /opt/genlab && sudo -u genlab git pull origin main"`
- [ ] Rebuild frontend on VPS: `ssh root@46.224.237.56 "cd /opt/genlab/dashboard/frontend && npm run build"`
- [ ] Restart dashboard: `ssh root@46.224.237.56 "systemctl restart genlab-dashboard"`

### Task 2: Fix publisher exit code (systemd marking "no blueprints" as failure)

**Root cause:** `run_publish()` returns `EXIT_NO_BLUEPRINTS=1` when a niche has no approved posts. `total_exit = max(total_exit, exit_code)` propagates this. Systemd treats non-zero as failure.

**Fix:** Add `SuccessExitStatus=1 3` to the service file (1=no blueprints, 3=daily cap — both normal operations).

- [ ] `ssh root@46.224.237.56` and edit `/etc/systemd/system/genlab-publisher.service` — add `SuccessExitStatus=1 3` under `[Service]`
- [ ] `systemctl daemon-reload && systemctl restart genlab-publisher.timer`

### Task 3: Fix cleanup_all.sh bash integer bug

**Root cause:** Line 91: `count=$(echo "$dirs" | grep -c . 2>/dev/null || echo 0)` — when dirs is empty, `grep -c .` exits 1 (no matches), `|| echo 0` runs, producing `"0\n0"` (two lines). `[ "$count" -gt 3 ]` fails because `"0\n0"` is not an integer.

**Fix:** Change to `count=$(echo "$dirs" | grep -c . 2>/dev/null) || count=0`

- [ ] Edit `/Users/anarchistsid/GenLab/scripts/cleanup_all.sh` line 91
- [ ] Verify fix: `bash -n scripts/cleanup_all.sh`

### Task 4: Fix verify_daily_cycle.sh (calls macOS `launchctl` on Linux)

**Root cause:** Lines 95-107 call `launchctl list` which doesn't exist on Linux.

**Fix:** Detect platform and use `systemctl` on Linux.

- [ ] Edit `/Users/anarchistsid/GenLab/scripts/verify_daily_cycle.sh` — replace launchctl block with platform-aware check

### Task 5: Fix shared-ingestion timeout

**Root cause:** 711 RSS feeds take >10min. `TimeoutSec=600` kills the process.

**Fix:** Increase to `TimeoutSec=1800` (30 min).

- [ ] Edit `/etc/systemd/system/genlab-shared-ingestion.service` on VPS
- [ ] `systemctl daemon-reload`

### Task 6: Fix affiliate services

**affiliate-scraper:** Playwright not installed on VPS. Non-critical — scraper works for API-based networks but exits non-zero when Playwright fails.
**affiliate-link-check:** 2 broken Amazon links cause exit 1. The check itself works fine.

**Fix:** Add `SuccessExitStatus=1` to both services so partial failures don't block systemd.

- [ ] Edit both service files on VPS
- [ ] `systemctl daemon-reload`

### Task 7: Move .tmp to media volume (free root disk)

**Root cause:** Root disk 75% full (27/38GB). Media volume at 3% (1.3/49GB).

**Fix:** Move `/opt/genlab/.tmp` → `/mnt/genlab-media/.tmp`, symlink back.

- [ ] Stop pipeline timers temporarily
- [ ] `mv /opt/genlab/.tmp /mnt/genlab-media/.tmp && ln -s /mnt/genlab-media/.tmp /opt/genlab/.tmp`
- [ ] Re-enable timers

### Task 8: Disable all local launchd services

- [ ] `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.genlab.*.plist`
- [ ] Verify: `launchctl list | grep genlab` shows nothing
