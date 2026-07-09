# 2026-07-09 PR #716 canary — NEEDS-INVESTIGATION (fix committed, awaiting deploy)

**4-line status as of follow-up routine (17:05 IST / 11:35 UTC)**

- **Line 1:** Primary routine completed at 11:05 UTC (ended_reason: run_once_fired); no git artifacts committed by routine — SSH unavailable in env, no audit-round-5 file created; operator manual triage confirmed same canary result.
- **Line 2:** NEEDS-INVESTIGATION — all 5 niches at 0/N with_arms (ai_creators 0/4, anime 0/2, gaming 0/3, movies 0/3, sports 0/3); 15/15 post-deploy pending_feedback rows had arm_ids_by_dimension = {} at canary time.
- **Line 3:** Key evidence: commit `f42af29` ("bump HighlightMoment window 12s→16s so arm attribution flows") at 17:00:30 IST reveals root cause — HighlightMoment window_seconds=12 produced ~12s output < 15s SPEC.min_duration guard; post_render_transform returns (path, {}) when output is too short, silently dropping arm attribution even though orchestrator picked arms for every dimension. Fix bumps window to 16s across 4 active niches (FrameDrift/anime unchanged — intelligent_transform disabled). Fix committed by operator from "evening dashboard triage"; not yet confirmed deployed.
- **Line 4:** Await 18:00 IST consolidation routine (trig_01Dau74894rFPWrwdm4QDwL8) for final confirmation after deploy. If deployed before 18:00, expect next canary run (tomorrow 16:30 IST) to show with_arms > 0 across all enabled niches.

## Raw git evidence

```
f42af29 fix(transformation): bump HighlightMoment window 12s → 16s so arm attribution flows (#630) (#754)
```

Commit timestamp: 2026-07-09 17:00:30 +0530 (11:30:30 UTC)

### Commit body excerpt

> **Symptom:** 15/15 pending_feedback rows from today's publishes had arm_ids_by_dimension = {}.
> All 5 niches: ai_creators 0/4, anime 0/2, gaming 0/3, movies 0/3, sports 0/3.
> Verify runner correctly flagged this as "flag or config regression suspected".
>
> **Root cause:** genlab_core.media.post_render_transform:258 — the 15s min-duration guard.
> Task #617 bumped highlight_moment.window_seconds 8→12 assuming intro+outro (~6s) would bring
> output to ~18s. Live-fire showed motion_compositor silently skips intro/outro in most renders —
> 6 renders hit: [gaming] transformed output 10.03s < SPEC.min_duration 15.0s.
> Output = window_seconds EXACTLY. post_render_transform returns (base_composite_path, {}) —
> dropping arm attribution even though orchestrator picked arms for every dimension.
>
> **Fix:** Bump window_seconds 12→16 across 4 activated niches.

## Diagnostic notes

- PR #716 wire is architecturally correct — the 5-hop route (post-render → push_to_backlog → feedback_registration) does populate arm_ids_by_dimension at the orchestrator level.
- Failure is a secondary regression from task #617 (window 8→12, merged same day) interacting with motion_compositor's silent intro/outro skips.
- Wire fix (#716) + window fix (#630/f42af29) together should make attribution flow. Fix needs a prod deploy to confirm.
- Third routine fires 18:00 IST (trig_01Dau74894rFPWrwdm4QDwL8) for final chain consolidation.
