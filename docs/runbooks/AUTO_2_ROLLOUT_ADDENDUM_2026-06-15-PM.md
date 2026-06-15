# AUTO #2 Rollout Addendum — 2026-06-15 PM

**Read this first.** It supplements the 1,663-line
`AUTO_2_ROLLOUT_2026-06-15.md`. The original is still the procedural
authority for the Day-1 walkthrough; this addendum tells you what
shipped after the runbook was written, what to merge in what order,
and which steps in the original are now satisfied by code.

## What changed since the runbook was written

13 PRs landed in flight overnight (see PR list below). Three classes
of change:

1. **All 3 Round-3 audit showstoppers closed in code.** The original
   runbook flagged these but expected operator manual intervention;
   they now ship as merge-ready PRs.

2. **Two real prod bugs surfaced + fixed.** Neither was in the
   runbook because they were discovered during this session via
   empirical probing.

3. **Day-1 work parallelized into one PR per step.** Original
   runbook described monolithic merge events; reality is 6 small
   PRs that can land independently.

## PR list (13 PRs, oldest first)

| PR  | Branch                                    | Purpose                                                              | Base                        |
| --- | ----------------------------------------- | -------------------------------------------------------------------- | --------------------------- |
| 220 | fix/scheduler-per-day-cap-and-warning     | Scheduler per-day cap + DailyCapEnforcer SUCCESS+INSIGHTS fix + script | main                        |
| 221 | auto2-day1-preflight                      | **P1+P2**: gate's `extra` wrapper + `decided_at` kwarg               | main                        |
| 222 | auto2-day1-1-purge-synthetic              | **D1.1**: purge 25 synthetic calibration rows                        | main                        |
| 223 | auto2-day1-3-lower-virality-threshold     | **D1.3**: lower `min_virality_score` 0.05 → 0.02                     | main                        |
| 224 | auto2-day1-2-backfill-calibration         | **D1.2** (HIGH risk): backfill calibration from `dashboard_events`   | main                        |
| 225 | auto2-day1-5-validate-calibration         | **D1.5**: read-only calibration health checker                       | main                        |
| 226 | auto2-day1-4-daily-slo-badge              | **D1.4**: Mission Control DailySloBadge                              | main                        |
| 227 | auto2-s7-blackboxbrief-publishing-yaml    | **Showstopper #2**: create BlackboxBrief publishing.yaml             | main                        |
| 228 | auto2-s1-calibration-source-filter        | **S1**: calibration_logger gate-vs-gate filter                       | #221                        |
| 229 | auto2-d3.9-auto-approver-systemd          | **Showstopper #3 / D3.9**: systemd unit for auto_approver            | main                        |
| 230 | fix/insights-cascade-status-filter        | PerformanceLearner SUCCESS+INSIGHTS fix (same pattern as #220)       | main                        |
| 231 | auto2-s2-bulk-review-calibration          | **S2**: wire calibration_logger into 4 bulk-approval bypass paths    | #228                        |

Plus the runbook lives at
`docs/runbooks/AUTO_2_ROLLOUT_2026-06-15.md` (unchanged, still
authoritative for step procedure).

## Merge ordering for tomorrow morning

```
Parallel tier — merge in any order, all base=main, all CI green:
  #220, #222, #223, #225, #226, #227, #229, #230

Chain 1 (in order):
  #221 → #228 → #231

Chain 2 (after #221):
  #221 → #224
```

Recommended sequence to minimize CI churn:

1. **#221 (P1+P2)** — unblocks the chains
2. **#222 (D1.1 purge)** — clears calibration table before #224 fills it
3. **#224 (D1.2 backfill)** — depends on #221 (`decided_at` kwarg)
4. **#225 (D1.5 validate)** — run between each step to verify state
5. **#223 (D1.3 threshold)** — 1-line constant change
6. **#226 (D1.4 SLO badge)** — pure frontend
7. **#227 (S7 publishing.yaml)** — independent
8. **#220 (scheduler + cap fix)** — high-impact prod bug fix
9. **#229 (D3.9 systemd unit)** — deploy the worker (dry-run mode)
10. **#230 (insights cascade fix)** — independent prod bug fix
11. **#228 (S1 calibration filter)** — based on #221
12. **#231 (S2 bulk-review wiring)** — based on #228

## Tomorrow's operator path

| Time (IST) | Step | What to do |
|------------|------|------------|
| 10:00 | **Pre-flight § 1 of original runbook** | Backups (`pg_dump`), prod state snapshot, STOP-condition checks |
| 10:30 | Merge PR #221, #222 | P1+P2 pre-flight + D1.1 purge |
| 10:45 | SSH prod, run `scripts/purge_synthetic_calibration_2026_06_15.sh` (D1.1) | `--apply` after preview looks right |
| 11:00 | Merge PR #224 (D1.2) | Backfill calibration from dashboard_events |
| 11:15 | SSH prod, run `scripts/backfill_calibration_2026_06_15.py` | dry-run first, then `--apply` |
| 11:30 | Merge PR #225 (D1.5) | Read-only validator |
| 11:35 | Run `scripts/validate_calibration_data.py` | Confirm 0 orphans, samples landed |
| 12:00 | Merge PR #223 (D1.3) + PR #226 (D1.4) + PR #227 (S7) | Threshold + SLO badge + BlackboxBrief yaml |
| 13:15 | **Decision point** (original runbook § 2.4) | Operator confirms backfill data quality |
| 14:00 | Merge PR #220 + PR #230 | Prod bug fixes (scheduler over-scheduling + insights cascade) |
| 14:30 | SSH prod, run `scripts/cleanup_overscheduled_2026_06_15.sh` | Clean the FrameDrift 4-on-Jun-15 stranded posts |
| 15:00 | Merge PR #229 (D3.9 systemd unit) | Deploy to prod: `systemctl daemon-reload && systemctl enable --now genlab-auto-approver.timer` — but worker ships with `--dry-run`, so nothing actually approves yet |
| 15:30 | Merge PR #228 (S1) + PR #231 (S2) | Calibration filtering + wire bulk-review paths |
| End | Day 1 done. AUTO #2 in observation mode. | Watch `journalctl -u genlab-auto-approver -f` |

## Two prod bugs surfaced during this session

Neither was in the original runbook. Both are real prod issues, both
fixed by PRs above. Operator should know about them in case they
surface in tomorrow's monitoring.

### Bug A: scheduler over-scheduling + cap silently bypassed (PR #220)

**Symptom**: operator screenshot showed 4 of 5 FrameDrift Visual
Ready posts scheduled for Jun 15 — violates CLAUDE.md's "1 reel per
channel per day" hard rule.

**Two stacked bugs**:

1. **Scheduler over-scheduling**: `_next_available_slot` collision
   key was per-(date, time, niche), not per-(date, niche). When
   `optimal_time_learner.optimal_slots_hhmm(top_n=3)` returned 3
   learned slots unioned with yaml `["12:00"]`, the scheduler had 4
   candidate slots/day and packed 4 approvals into one day.

2. **DailyCapEnforcer silent fail-open** (worse): the publisher's
   "safety net" was actually broken. Cap counter filtered
   `status='SUCCESS'` but the metric collector flips status to
   `INSIGHTS_6H` ~5h after publish. Second publisher invocation later
   in the day saw count=0 and bypassed the cap. Verified on Jun 14:
   gaming + sports actually DID publish 2 reels each on IG + YouTube
   — R-09's "1 reel per channel per day" guarantee silently violated
   for unknown duration.

Both fixed in #220. The 4 stranded FrameDrift posts on Jun 15 are
cleaned by `scripts/cleanup_overscheduled_2026_06_15.sh --apply`.

### Bug B: PerformanceLearner dark for posts past 6h (PR #230)

Same status-lifecycle bug pattern as Bug A. `_trigger_performance_learner`
in `run_fetch_insights.py` filtered `status='SUCCESS'` — missing the
bulk of the 5-48h engagement-data window the learner is supposed to
mine. The learning loop has been effectively dark for posts past their
6h mark for an unknown duration. Fixed by adding the OR formula
across all 5 lifecycle states.

## What's NOT shipped (intentionally)

### D2.7a (Strategy B+E gate-logic change)

The original runbook flagged this as HIGH-risk + make-or-break.
Deliberately NOT shipped tonight because the runbook says the
operator should see backfilled calibration data first before deciding
on the formula change. Revisit after Day 1 of operator review.

### Day-8 flip PRs (the live activation)

Two PRs needed to actually activate enforcement for ai_creators:

1. **PR editing `BlackboxBrief/config/publishing.yaml`**:
   `auto_publish.enabled: true` (was false). One-line change.
2. **PR removing `--dry-run`** from `deploy/systemd-phase2/genlab-auto-approver.service`'s ExecStart. One-line change.

Both are 1-line PRs the operator triggers once:
- ≥30 calibration samples × ≥90% agreement on ai_creators (D1.5
  validator confirms)
- Operator has watched ~1 week of dry-run worker logs without
  surprises
- 4+ free hours + rested operator (per original runbook § 9)

## STOP conditions (from the original runbook, still apply)

Before doing ANY work tomorrow:

1. `alembic_version` is current
2. Prod `git status` is clean (no uncommitted local edits — see the
   2026-06-14 deploy-pipeline-gap memory)
3. WARP is active (`systemctl status warp-svc`)
4. Dashboard renders without 5xx
5. Operator has 4 free hours and is rested
6. No unresolved CRITICAL alerts in last 24h

If any of these fails, postpone Day 1 by 24h. The PRs aren't going anywhere.

## 3am rollback (from the original runbook § 10)

Same procedure as the original. The PRs are all small enough to
selectively revert without breaking the other 12. The
`auto_approval_calibration` table backups from D1.1 and D1.2 are in
`.tmp/backups/`.

## What changed in the merge sequence vs the original runbook

The original described a monolithic "merge Day-1 work" event. Reality
is the work is split into PRs #221-#226 (Day 1) + #227, #229 (Day 2)
+ #228, #231 (Day 3). The walkthrough above respects the dependency
chain (P1+P2 first, calibration table clean before backfill, etc.)
while letting the operator stop at any boundary if something looks
off.

## Final state after Day 1

- All 12 PRs merged
- Calibration table: clean (synthetic purged + 18 backfilled rows
  preserving historical decided_at)
- Mission Control: shows DailySloBadge (`X/5 niches published today`)
  + AutoApprovalCalibrationCard (now with real samples, no synthetic
  contamination)
- Scheduler: enforces per-day cap; over-scheduled rows show amber
  warning badge
- DailyCapEnforcer: counts SUCCESS + INSIGHTS_* (no more silent
  bypass)
- PerformanceLearner: sees full 0-48h engagement window
- auto_approver: deployed via systemd, running every 30 min 06-22 UTC
  in dry-run mode (logs would-approve decisions, doesn't touch
  backlog)
- AUTO #2 enforcement: still OFF per `auto_publish.enabled: false`
  in every niche's publishing.yaml

Calibration data should hit the 30-sample × 90%-agreement threshold
for ai_creators within 1-2 days (vs. 1-2 weeks pre-S2) because the
bulk-review and approve-and-schedule paths now log calibration data.

Day-8 flip is operator-triggered, not on a calendar.
