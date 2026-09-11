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
