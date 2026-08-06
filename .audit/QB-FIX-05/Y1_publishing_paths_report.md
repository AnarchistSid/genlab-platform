# QB-FIX-05 Y1 — Publishing Paths + nightly_scheduler Slot Resolution

**Date:** 2026-08-06 23:15 IST
**Result:** **Option (c) — the slot is never reached.** Gaming's 15:30 IST scheduled_for was picked by an optimal-time bandit at hour 10 UTC. No publisher path fires between the 06:35 UTC daily run and the next day's 06:35 UTC daily run except a `--retry-only` retry pass that R-83 explicitly prevents from publishing fresh blueprints. Approved blueprints scheduled outside the 06:35 UTC ± epsilon window silently expire.

Secondary finding: X0-a's `created_at < 2026-08-06` cutoff was too coarse — some Aug 6 morning renders (including the currently-queued LoL) were created before the F1-F3d fixes landed later that same UTC day.

## Publishing paths enumerated

Every unit or timer on the VPS that reads `scheduled_for` and publishes:

| Unit | Fire time | Mode | Publishes fresh? |
|------|-----------|------|------------------|
| `genlab-publisher.service` | 06:35 UTC daily (12:05 IST) via `.timer` | main | YES — normal publish |
| `genlab-publisher-retry.service` | 10:30 UTC daily (16:00 IST) via `.timer` | `--retry-only` | **NO** — R-83 comment on unit file: "the 2nd daily publish run is RETRY-ONLY — it recovers stuck blueprints and retries genuinely-failed platforms, but never publishes a fresh blueprint (that would double the day's content past the 1-reel/channel/day cap). The main fresh-publish run is genlab-publisher.service at 06:35." |
| `genlab-nightly-schedule.service` | 16:30 UTC daily (22:00 IST) via `.timer` | scheduler | **DOES NOT PUBLISH** — Path-B safety net that sets `scheduled_for` on top-scoring VISUAL_READY when auto-approver skipped a niche. |

No other publish-adjacent unit exists. `systemctl list-unit-files "*publish*" "*publisher*"` returns exactly 4 (2 services + 2 timers). No cron jobs relevant to publishing (`/etc/cron.d/` contains only `certbot`, `e2scrub_all`, `sysstat`). No Prefect deployments observed.

## nightly_scheduler slot logic

From `/opt/genlab/scripts/nightly_schedule_top_per_niche.py:compute_target_slot()`:

```python
picked_hour = 6  # UTC fallback
if niche_id:
    try:
        from genlab_core.scheduling.optimal_time_learner import pick_optimal_hour_for_niche
        picked_hour, source = pick_optimal_hour_for_niche(niche_id, fallback_hour=6)
    except Exception: pass

slot = datetime.combine(tomorrow_utc, time(picked_hour, 0, 0), tzinfo=UTC)
```

`pick_optimal_hour_for_niche` reads per-niche hour arms from the bandit posterior (`GENLAB_OPTIMAL_TIME_BANDIT_ENABLED` flag + ≥30 total obs). Gaming's bandit picked hour 10 UTC → `scheduled_for = 2026-08-07 10:00:00 UTC` = 15:30 IST.

**The bandit is unaware of the daily publisher's fire time.** It picks the hour with the highest historical engagement, not the hour publisher will actually pick up.

Memory comment inside the function: `"6am hurts YT 4× (all niches)" — the hardcoded 06:00 slot has been documented as anti-signal on YouTube specifically. Bandit picks something better once each niche accumulates enough hour-arm observations.`

The bandit picks hours to maximize engagement — but those hours are only useful if publisher fires there. Neither publisher timer does.

## The three options — outcome for LoL

Given:
- Publisher (06:35 UTC): won't reach 10 UTC scheduled_for (3.5h in the future at fire time)
- Publisher-retry (10:30 UTC): `--retry-only`, won't publish fresh even though the slot is now past
- Nightly-scheduler (16:30 UTC): doesn't publish

**Outcome: (c) — the slot is never reached.** The blueprint sits approved-and-scheduled indefinitely until either superseded by a newer scheduled blueprint (typical) or manually resolved.

X0-a corroborates: 8 gaming blueprints found with PAST scheduled_for dates (Jul 29 → Aug 6), all queued by nightly_scheduler, all sat stale. The scheduler quietly generated one per day and each one aged out unpublished.

## Post-X0-a stuck count

```sql
SELECT niche_id, COUNT(*) FROM blueprints
WHERE action_taken='approved' AND status='VISUAL_READY'
  AND scheduled_for < NOW() - INTERVAL '1 day'
GROUP BY niche_id;
-- 0 rows
```

After X0-a archived the 8 stuck gaming rows plus other pre-fix rows, there are currently NO rows in the stuck-past-date state across any niche. The pattern is not currently accumulating (because X0-a cleaned it), but the mechanism that CREATES the pattern is still live — every night at 22:00 IST the scheduler picks a slot the publisher won't reach for niches whose bandit prefers non-06:35 hours.

## LoL blueprint — does it have a render?

**Yes.** Current active LoL blueprint:

```
id=3ad79cf7-7b82-4f26-93f1-ad8efeff4fa0
niche_id=gaming, status=VISUAL_READY, action_taken=approved
action_taken_source=nightly_scheduler
scheduled_for=2026-08-07 15:30 IST (= 10:00 UTC)
created_at=2026-08-06 04:09 UTC
visual_paths=["/opt/genlab/CriticalRush/.tmp/rendered/gaming_20260806_040005/league_of_legends_vertical.mp4"]
```

File exists on disk: 3.2 MB, mtime 2026-08-06 09:39 IST (matches created_at).

**F-QB-0002 does NOT apply to this row.** Gaming has produced rendered MP4s recently — the earlier "zero MP4s ever" finding may be stale, or was measuring a specific class of MP4 (downloaded source clips vs rendered final reels).

**But the render is pre-fix.** Created 2026-08-06 09:39 IST — before F1/F2/F3a/F3a-2/F3b/F3d-1 shipped (all shipped later that day, between 12:00 IST and 22:00 IST). If the (c) defect were ever fixed and this row published, it would ship without the current fix stack.

## Secondary finding — X0-a cutoff too coarse

X0-a used `created_at < 2026-08-06`, which cuts on calendar-day boundary. Some pre-fix renders were created on 2026-08-06 morning UTC, before the fix commits landed. The LoL row above is one; there may be others (movies/anime auto-rendered mornings), depending on per-niche pipeline schedules.

Not archiving in Y1 per §2 constraint ("scope the fix as its own item"). Filed as follow-up: refine cutoff to specific commit landing timestamps, per-niche if pipelines fire at different UTC hours.

## Not fixing (c) in this pass

Per Y1 gate spec: "If (c), report it with the count of any other blueprints currently in the same state. Do not fix it in this pass; scope it as its own item."

Recommended fix scope for a future pass:
- (i) constrain `pick_optimal_hour_for_niche()` to hours the publisher actually fires — or teach the publisher to fire at bandit-chosen hours
- (ii) or: null `scheduled_for` on prior day's un-fired VISUAL_READY-approved rows before nightly_scheduler runs, so it re-picks slots
- (iii) or: run a per-hour publisher timer (`OnCalendar=*-*-* *:05:00 UTC`) that checks whether any blueprint's scheduled_for has landed

Option (i) is the cheapest. Option (iii) is the most correct architecturally but adds systemd fanout.

## Gate

```
Publishing paths found:
  genlab-publisher.service          — 06:35 UTC daily, publishes fresh
  genlab-publisher-retry.service    — 10:30 UTC daily, --retry-only (NOT fresh)
  genlab-nightly-schedule.service   — 16:30 UTC daily, does not publish
nightly_scheduler slot logic:
  scheduled_for = tomorrow @ pick_optimal_hour_for_niche(niche_id, fallback=6) UTC
  Bandit-picked; not synchronized to publisher fire time
Outcome for the LoL blueprint: (c) — 10:00 UTC slot unreachable by any publisher path
LoL has render?                    YES (3.2 MB MP4 on disk)
LoL render is pre-fix?             YES (created 2026-08-06 09:39 IST, before F1-F3d)
```

## Commit

`test(publishing): enumerate publishing paths and resolve nightly_scheduler slot logic`
