# QB-FIX-11 E2 — Gaming Read-Only Diagnostic

**Date:** 2026-08-07 18:00 IST
**Result:** Gaming's failure shape is **ONE independent failure at the publish-selection step**, contained to the interval between "blueprint approved" and "publisher decides to publish it". Not sourcing, not rendering, not approval-writing. Went dark 2026-07-06 (32 days ago) after 101 successful historical publishes. F-QB-0002's "zero MP4s ever produced" framing is empirically wrong.

## Step 1 — Where renders die

**They don't.** Gaming renders MP4s daily.

Journal evidence (last 2 gaming runs):
- **2026-08-06 09:39:33** `[RENDER] 3 rendered, 0 failed, 0 skipped, compilation=False` — pipeline exit "Deactivated successfully"
- **2026-08-07 09:40:09** `[RENDER] 2 rendered, 0 failed, 0 skipped, compilation=False` — pipeline exit "Deactivated successfully"

DB state confirms:
- `MARVEL TŌKON: Fighting Souls` — currently VISUAL_READY (created 2026-08-07 09:40, matches render log)
- `League of Legends`, `Escape from Tarkov` (2026-08-06) — reached VISUAL_READY, then archived by X0-a/Z0

**101 gaming blueprints have been PUBLISHED historically.** Not zero. `SELECT COUNT(*) FROM blueprints WHERE niche_id='gaming' AND status='PUBLISHED'` = 101.

**F-QB-0002's "gaming has produced zero MP4s ever" is empirically wrong.** Correction filed. Possible causes for the original claim:
- Measured against a specific window that missed gaming's active publish period
- Measured "downloaded MP4s" (fetcher output) rather than "rendered MP4s" (compositor output)
- Was true at time of audit but false now (would mean gaming re-started rendering post-audit)

Regardless of original interpretation, the current state is: gaming produces 2-3 MP4s/day and 1 blueprint via nightly_scheduler picking.

### 1c — render_error persistence

Per ME-15 (from QB-FIX-06 Z1): all queried gaming blueprints have `extra->>'render_error'` = NULL. The pre-render quality gate does not persist rejection reason to the blueprint row. Absence of a value proves nothing about whether the gate ran or rejected. Same observability gap flagged in ME-15; the actual render success is confirmed via journal.

## Step 2 — Source path characterisation

Gaming source URL distribution over 14d:

| url_type | count |
|----------|-------|
| twitch | 37 |
| other | 1 |

**37/38 = 97% Twitch.** No YouTube, no Reddit, no streamable.

**Twitch sourcing works.** Evidence:
- 3 rendered blueprints on 2026-08-06 → downloader retrieved Twitch clips + compositor produced MP4s
- 2 rendered on 2026-08-07 → same
- 101 historical publishes → Twitch has been the working source path all along
- The current MARVEL TŌKON reel is on disk right now (VISUAL_READY)

**F2's yt-dlp mweb+poToken changes are YouTube-specific** — they don't touch Twitch. But Twitch was NOT broken; it did not need touching. The rendering evidence confirms.

## Step 3 — Scheduling failure specificity

Y1 (QB-FIX-05) reported gaming's LoL blueprint scheduled for 15:30 IST — a slot the 12:05 publisher never reaches. **That was an exceptional single-row observation, not the general pattern.**

Approved-blueprint scheduled hour distribution (14d, all niches):

| niche | scheduled hour IST | count |
|-------|-------------------|-------|
| ai_creators | 12 | 46 |
| anime | 11 | 2 |
| anime | 12 | 5 |
| **gaming** | **12** | **13** |
| movies | 11 | 2 |
| movies | 12 | 1 |
| sports | 12 | 19 |

**13 of 13 gaming approved rows in 14d were scheduled at 12:00 IST** — the same hour every other niche uses. The 15:30 IST case Y1 caught was an outlier (bandit occasionally picks a non-canonical hour for gaming due to its posterior).

**Scheduling is NOT gaming-specific broken.** Rows are scheduled at the right hour.

## Step 4 — What actually went dark 2026-07-06

Last successful gaming publish:

| platform | post_id | published_at |
|----------|---------|--------------|
| facebook | `facebook:1068787548912944` | 2026-07-06 06:38:04 UTC |
| facebook | `facebook:1692428025134958` | 2026-06-29 06:49:12 UTC |
| threads | `threads:DZ9Yhuekyqi` | 2026-06-24 06:48:02 UTC |
| instagram | `instagram:DZ9YPOhkZ_t` | 2026-06-24 06:45:42 UTC |
| threads | `threads:DZ6zfoWCheZ` | 2026-06-23 06:46:57 UTC |

**Nothing since 2026-07-06.** 32 days dark, not "never rendered." Gaming's config state:

```yaml
# CriticalRush/niches/gaming/config/publishing.yaml
auto_publish:
  enabled: false
  rollout_pct: 0.0
```

`auto_approver_v1` does not fire on gaming — the approver's gate short-circuits when `auto_publish.enabled=false`. All approvals since ~2026-06-28 (when auto_publish scaffolding shipped) have come from `nightly_scheduler` alone.

**Gaming DB state now:**
- 25 ARCHIVED unapproved
- 135 ARCHIVED approved (mostly by nightly_scheduler)
- 101 PUBLISHED (all from before 2026-07-06)
- 1 VISUAL_READY unapproved (MARVEL TŌKON)

## Failure shape: ONE

**Single failure at the publish-selection step.**

Not sourcing (Twitch works). Not rendering (2-3 MP4s/day). Not approval-writing (135 approved rows). Not scheduling to a valid hour (13/13 at 12:00 IST).

**Something between "nightly_scheduler sets action_taken=approved + scheduled_for=next-day-12:00-IST" and "publisher publishes it" broke on 2026-07-06.**

Candidate causes (not investigated per E2 read-only scope):
- **(a) auto_publish gate side-effect:** the `auto_publish.enabled=false` config may block MORE than just auto_approver — it may also cause the publisher's `PublishGatekeeper` to skip gaming's approved blueprints. Would need to trace `PublishGatekeeper` for niche-specific gates.
- **(b) DailyCapEnforcer:** gaming's daily cap may be misconfigured or its counting may be picking up phantom rows.
- **(c) Publisher blueprint_selector:** it may filter gaming out for a reason not in the general selection logic (niche-specific gate, etc.).
- **(d) A change made on 2026-07-06:** operator-side (config), commit-side (code), or environmental. Git log around that date + config timestamps would narrow.

## Deliverable

**Shape: ONE independent failure.** The post-Aug-10 session's work is one investigation, not a sequence. Starting point:

1. What changed on/around 2026-07-06 (git log + config timestamps + syslog for the specific date)
2. Whether `auto_publish.enabled=false` interacts with the publisher's gate beyond just disabling auto_approver
3. Whether nightly_scheduler's approvals produce blueprints that trip a niche-specific `PublishGatekeeper` check

Do NOT re-derive the render diagnosis — gaming DOES render. F-QB-0002 needs correction.

## Corrections filed

**F-QB-0002 (Phase 0): "gaming has produced zero MP4s ever"** — empirically false as of 2026-08-07. Gaming has produced 101 PUBLISHED blueprints historically; 5 rendered MP4s in the last 2 days. The finding as literally worded is wrong. Post-Aug-10 session should reframe as "gaming has not published since 2026-07-06" and target the publish-selection segment specifically.

## Gate

```
Renders die where: THEY DON'T — 2-3 MP4s/day render successfully
Twitch sourcing supported: YES (37/38 stories, 101 historical publishes)
Scheduling gaming-specific broken: NO (13/13 rows at 12:00 IST slot in 14d)
Failure shape: ONE (publish-selection step; something between approval and publish; 32 days dark since 2026-07-06)
F-QB-0002 correction: filed (was "zero MP4s ever"; reality is "no publish since 2026-07-06")
```

## Commit

`test(gaming): read-only diagnostic of zero-render and unreachable-slot failures`
