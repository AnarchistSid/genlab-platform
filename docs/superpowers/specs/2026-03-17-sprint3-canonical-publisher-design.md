# Sprint 3: Single Canonical Publisher

**Goal**: One publisher in genlab-core, used by all 5 niches via clean launchd plists. Content Scraper's publisher retired.

**Architecture**: ~300 LOC orchestrator that delegates to existing genlab-core infrastructure: `PublishGatekeeper` (gates), `get_client()` (platform registry), `DailyCapEnforcer` (caps), `niche_credentials` (per-niche tokens), `BacklogClient` (data access via StorageBackend).

## What Already Exists (no need to build)

| Component | Location | Status |
|---|---|---|
| Platform clients (IG, YT, FB, X, Threads) | `platforms/*.py` | Complete — `.publish(payload) -> PublishResult` |
| PublishGatekeeper (7 gates) | `platforms/gatekeeper.py` | Complete — approval, schedule, score, media, cap |
| DailyCapEnforcer | `publishing/daily_cap.py` | Complete — per-niche caps from YAML |
| niche_credentials | `publishing/niche_credentials.py` | Complete — per-niche token resolution |
| PublishPayload / PublishResult | `platforms/models.py` | Complete — typed data models |
| Platform registry | `platforms/registry.py` | Complete — `get_client(platform_id)` |
| BacklogClient | `http/backlog_client.py` | Complete — routes through StorageBackend |

## What to Build (~300 LOC)

### `genlab-core/src/genlab_core/publishing/publish_all_platforms.py`

```
main(--niche NICHE_ID)
  → acquire_pid_lock(/tmp/publisher-{niche}.lock)
  → load credentials via niche_credentials
  → query VISUAL_READY blueprints via BacklogClient
  → PublishGatekeeper.evaluate() each blueprint
  → take top 1 by priority_score
  → set status = PUBLISHING
  → for platform in enabled_platforms:
      client = get_client(platform, niche_id=niche_id)
      payload = build_payload(blueprint, platform)
      result = client.publish(payload)
      record result in platform_publish_status
  → set status = PUBLISHED (if any succeeded)
  → release lock
```

### PID Lock (`/tmp/publisher-{niche}.lock`)

```python
class PidLock:
    def __init__(self, niche_id: str):
        self.path = Path(f"/tmp/publisher-{niche_id}.lock")

    def acquire(self) -> bool:
        if self.path.exists():
            pid = int(self.path.read_text().strip())
            if _pid_alive(pid): return False  # already running
            self.path.unlink()  # stale lock
        self.path.write_text(str(os.getpid()))
        return True

    def release(self):
        self.path.unlink(missing_ok=True)
```

### Payload Builder

Reads blueprint fields and constructs `PublishPayload` + platform-specific configs:
- Instagram: caption + hashtags + video path
- YouTube: shorts_title (question format ≤40 chars) + video path
- Facebook: caption + video path (as Reel)
- X/Twitter: tweet text (≤280) + video path
- Threads: caption + video path

### Exit Codes

| Code | Meaning |
|---|---|
| 0 | Success (≥1 platform published) |
| 1 | No eligible blueprints (queue empty, not an error) |
| 2 | All platforms failed (error) |
| 3 | Daily cap reached (not an error) |
| 4 | Lock held by another process |

## What Gets Retired

`Content Scraper/execution/publish_all_platforms.py` (2,407 LOC) → moved to `DEPRECATED/`

## 5 Canonical Plists

All call Python directly (no shell wrappers):
```
ProgramArguments: [.venv/bin/python3, -m, genlab_core.publishing.publish_all_platforms, --niche, {niche_id}]
WorkingDirectory: /Users/anarchistsid/GenLab
EnvironmentVariables: HOME, BACKLOG_CONFIG_PATH
StartCalendarInterval: 06:30 UTC (12:00 IST)
```

## Files

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/publishing/publish_all_platforms.py` | NEW — canonical publisher |
| `~/Library/LaunchAgents/com.genlab.{niche}-publisher.plist` × 5 | NEW — clean plists |
| `Content Scraper/execution/publish_all_platforms.py` | DEPRECATED |
| Old publisher plists | DEPRECATED |
| `genlab-core/tests/publishing/test_publish_all_platforms.py` | NEW — tests |
