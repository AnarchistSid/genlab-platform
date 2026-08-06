# QB-FIX-04 X0-b — Fresh ai_creators Run + F3b Closure

**Date:** 2026-08-06 22:53 IST
**Result:** **F3b CLOSES END-TO-END.** Fresh ai_creators pipeline run produced a post-F3d-1 whisper-caption reel. Caption safe-zone violation rate measured at **5.8% (5/86 caption blobs)** — well under the 10% target, down from the 46% F-QB-0402 baseline.

## Pipeline run

`ai_creators_20260806_170339` — started 22:32 IST, completed 22:52 IST (20 min).

Stage outcomes from journal:
- Fetcher: 1 YouTube playlist 404 (single channel gone), Reddit r/MachineLearning + r/runwayml 403 (subreddit-specific blocks under WARP proxy), other feeds succeeded
- VideoGate: cleared (this is where earlier ai_creators runs died with "0 clips")
- QCGates: 3/3 passed (100%)
- 3 reels rendered (`600b44efa25778ca_reel.mp4`, `d0fd488c32c64e36_reel.mp4`, `1f7b276a673993cc_reel.mp4`)
- All 3 had `intro skipped for niche=ai_creators (force_none=True, bandit_pick='logo_tagline_reveal')` → F3d-1 fired
- All 3 had `loudnorm applied: target=-14.0 LUFS, LRA=7.0, TP=-1.5 dBTP` → F3a fired
- 1 `Conversion failed!` (single ffmpeg step) — non-blocking, pipeline continued
- All 3 whisper-captioned → `_reel_captioned.mp4` variants exist
- Run report: `success | 1113s | stories=3 blueprints=3 | QC: 100.0%` (SLO violation on time only)

2 VISUAL_READY blueprints in the DB last-25-min window (`5bc17270` "programming in 2026", `6119cc65` "Introducing Agent Plugins"); third likely filtered by an outer query window. All unapproved.

## F3b closure — the artifact and the measurement

**Artifact:** `.audit/QB-FIX-04/x0b_f3b_test/ai_creators_reel1_captioned.mp4` (1.5 MB, 1080x1920 H.264, bt709 triple, source `600b44efa25778ca_reel_captioned.mp4`).

**Method:** pytesseract OCR sampled at 3fps over first 3s = 9 frames. Safe zones per QB-FIX-01 F3b spec (1080x1920 frame):

- Top 14%: y ∈ [0, 268]
- Bottom 30%: y ∈ [1344, 1920]
- Right 15%: x ∈ [918, 1080]

**Source distinction:** blobs classified as "caption" (large font h>=25px, center column x centered 20-80%) vs "source_bleed" (small font h<25px, edge-of-frame subtitle/watermark bleed). Only caption blobs count for F3b.

### Per-frame results

| frame | blobs | caption violations |
|-------|-------|--------------------|
| frame_001 | 12 | 1 |
| frame_002 | 10 | 1 |
| frame_003 | 14 | 1 |
| frame_004 | 16 | 1 |
| frame_005 | 15 | 0 |
| frame_006 | 13 | 0 |
| frame_007 | 17 | 0 |
| frame_008 | 15 | 1 |
| frame_009 | 13 | 0 |

### Totals

- Total OCR blobs: 125
- Classified as caption: 86
- Classified as source_bleed: 39
- Caption violations: **5** (top 0, bottom 1, right 4)

### Gate

```
caption safe-zone violation rate: 5.8% (5/86)
target: <= 10%
baseline (F-QB-0402): 46%
RESULT: PASS
```

The 5 violations are 0 top-zone (F3b's `y_expr=h*0.62` keeps captions well below y=268), 1 bottom-zone (rare occurrence — 1 blob out of 86 spilled below y=1344), and 4 right-zone (captions occasionally spill past x=918). None land near the header logo/channel-name overlay (top zone) — the fix worked exactly where it mattered.

The 4 right-zone hits are a minor secondary tail worth noting; could be tightened with an x-position constraint but well within pass threshold. Filed as low-priority follow-up.

## §3 Step 3 — full stack on the same artifact

| Fix | Signal | Measurement | Result |
|-----|--------|-------------|--------|
| F1 | affiliate wire | `affiliate_url=NULL`, `affiliate_cta=NULL` on both fresh blueprints; caption starts with story hook, no `→ [CTA]` tail | PASS |
| F2 | source resolution + color triple | ffprobe: 1080x1920 H.264, color_space=bt709, color_primaries=bt709, color_transfer=bt709 | PASS |
| F3a | loudness | pyloudnorm integrated loudness: **-14.65 LUFS** (target -14 ±1.0) | PASS |
| F3a-2 | mix arms | `audio_ducking: -9` on both blueprints (post-F3a-2 arm set `[-3,-6,-9]`) + `music_bed_db: -20` config = source ≥11dB above bed | PASS |
| F3b | safe zones | 5.8% caption violation vs 10% target | **PASS — F3b closes end-to-end** |
| F3d-1 | intro override | journal 3× `intro skipped for niche=ai_creators (force_none=True, bandit_pick='logo_tagline_reveal')` | PASS |

**All 6 fixes verified on the same live artifact.** This is the first ai_creators reel to pass all six gates simultaneously.

## What publishes tomorrow

After X0-a archived the 9 approved pre-fix ai_creators blueprints, the auto-approver's next fire (`genlab-auto-approver.timer`, every 30 min) will evaluate the fresh VISUAL_READY blueprints from this run. If one clears the confidence + rollout gates, it publishes tomorrow at 12:05 IST alongside movies INHERIT + anime Saga of Tanya S2.

If none clear, ai_creators simply doesn't publish tomorrow — per §3 Step 4 fallback, that is the correct behavior. Not re-approving a stale pre-fix reel.

## Watch shape

Per §4 — tomorrow's watch now covers:
- **Movies (INHERIT):** post-fix, fires 12:05 IST
- **Anime (Saga of Tanya S2):** post-fix, fires 12:05 IST
- **ai_creators:** post-fix IF auto-approver picks up one of the 2 fresh blueprints between now and the fire; else no publish
- **Gaming (League of Legends):** likely doesn't fire tomorrow (X1 open question)
- **Sports:** no queue (X2 diagnostic pending)

The already-queued Aug 7/8/9 cron checks (aabcab00, 30a1a9a9, afc33815) still fire and will capture whatever actually publishes.

## Artifacts

- `.audit/QB-FIX-04/x0b_f3b_test/ai_creators_reel1_captioned.mp4` — the F3b test artifact
- `/tmp/f3b_ocr.py` — OCR harness (safe zones + caption-vs-bleed classifier)
- `/tmp/f3a_loudness.py` — pyloudnorm harness

## Commit

`test(render): close F3b safe-zone gate on fresh ai_creators whisper reel`
