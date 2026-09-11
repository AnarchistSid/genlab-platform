# INV-01b — Deferral ledger
Out-of-scope items. Tasks, not commits. None acted on.

| # | task | why it matters | class |
|---|---|---|---|
| U-01 | **#218 is blocked on approval, not schedule.** `1b3e0a5c` must go through the dashboard approval path (which stamps `reviewed_at`/`action_taken`), not a direct `scheduled_for` write. My 09-09 assign was insufficient | blocks #218, OUTRO-01, AFF-01 2-4, MULTICLIP-01 | M |
| U-02 | **gaming rendered 0 MP4s** from 6 blueprints with 6 TTS syntheses. Silent — no render error surfaced in the retention copy | a whole niche produced no publishable asset | M |
| U-03 | **movies true peak −0.19 dBTP** on the intro-appended artifact. Confirm whether the ValidateVideos final-asset gate fires on the published file or only on `_reel_captioned.mp4` | audible clipping on the hook | M |
| U-04 | **Sports/YouTube reward 0.3828 (n=8)** is the highest measured cell by 30%, against YouTube ≈0 on every other niche. Worth a dedicated read before any consolidation decision | contradicts "YouTube is a lottery" | M |
| U-05 | **`niche_id='all'` in compliance_events** (30 rows) — tenant attribution gap in the compliance writer | multi-tenant correctness | M |
| U-06 | **No caption sidecars anywhere**; only ai_creators produces a captioned render | subtitles are effectively a one-niche canary | M |
| U-07 | Re-run sections 2/3 probes not completed this pass (VO presence, ducking, transitions, slate, render path) against the next fire's retention copy | closes INV-01b properly | M |
| U-08 | Validate the caption-detection heuristic against a known-captioned file before trusting any future automated caption audit | prevents repeating E-03 | M |

---

## Scope 1c (OPS-03b) — T-14 superseded

**T-14 — "gaming silent render failure" — SUPERSEDED / RETRACTED.** Gaming
rendered four MP4s to `CriticalRush/.tmp/rendered/gaming_20260911_040019/`. The
"zero MP4s" reading came from my retention scan covering only
`/opt/genlab/.tmp/runs/`. Replaced by three tasks:

### T-14a — `caption_animator` filtergraph defect
`[AVFilterGraph] No such filter: '0.000'` (gaming), `'23.700'` and `'21.000'`
(ai_creators) — a numeric **time value** lands where a filter name belongs.
ffmpeg exit 8. Format-string or separator defect in the graph builder.

Scope 1b finding attached: **not style-specific and not gaming-specific.**
movies ran the identical style (karaoke) and segment count (4) and succeeded,
while ai_creators failed twice on that same style. The discriminator is segment
**timing**, not style or count — all three failing literals are seconds.

Fix scope: locate the builder; reproduce with the retained 4-segment input from
`.audit-retention/2026-09-11/`; gate on a transformed MP4 from the retained
blueprint carrying burned-in captions confirmed by frame sample.

### T-14b — exception swallowed to WARNING while the stage reports success
ffmpeg exit 8 logged at `WARNING`; the gaming run contains **zero ERROR or
CRITICAL lines**; `RenderGamingVideo` (815.4s) and `ValidateVideos` (88.7s) both
reported completion. A downstream guard
(`transformed output 8.52s < SPEC.min_duration 15.0s — returning base
composite`) then shipped the untransformed clip.

**This is a class, not an instance.** Any swallowed-to-WARNING path paired with a
return-base-composite guard ships degraded output while reporting green. Scope:
enumerate every `except …: log.warning` fall-through in the render/transform
chain and every "return base composite" guard; classify each as legitimate
degradation vs silent failure. No fixes. T-06-adjacent. Rule #19 shape.

### T-14c — gaming render output path diverges
Gaming writes to `CriticalRush/.tmp/rendered/<run_id>/`; the other four write to
`/opt/genlab/.tmp/runs/`. Single-path audits miss it — INV-01b did, and reported
a false zero. Scope: document the divergence; decide converge vs register both
paths in the audit tooling. No change now.
