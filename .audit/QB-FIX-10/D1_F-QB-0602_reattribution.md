# QB-FIX-10 D1 — F-QB-0602 Reattribution + ME-16

**Date:** 2026-08-07 16:05 IST
**Result:** F-QB-0602 reattributed from fetcher cadence to publish throughput. ME-16 filed as third instance of the gate-on-aggregate class-of-bug. Audit for other Phase 9 items surfaced one more (F-QB-0101 bitrate gate).

## Actions taken

### 1. `phase_6_thumbnails_topics.md` — F-QB-0602 amended

Preserved original finding text; appended **REATTRIBUTION 2026-08-07 (QB-FIX-10 D1)** block:

- The total lag is queue residency under `daily_cap=1`, not fetcher cadence
- QB-FIX-09 C1 decomposition table (fetch / approver / slot) added
- Approver segment dominates 92-100% of total on every niche
- **REWRITTEN VERIFICATION GATE:** target the approver segment, not total lag. Measure `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (scheduled_for - created_at))/3600)` per niche on rows published in last 14d. Target: `≤ 24h` for all niches.
- Class-of-bug link to ME-16 recorded

### 2. `phase_9_synthesis.md` — gap-matrix rows rewritten

Four rows (anime / sports / gaming / ai_creators under "Topic freshness") relabeled to **"Publish throughput"** with the approver-segment measurement:

| dim | niche | now | target | delta | conf | tier | H | finding | est |
|-----|-------|-----|--------|-------|------|------|---|---------|-----|
| 8 Publish throughput | anime | approver seg 151.9h (C1) | ≤24h | WORSE 6× | HIGH | 1 | H | F-QB-0602 (reattr QB-FIX-10 D1) | 2 |
| 8 Publish throughput | sports | approver seg 169.1h (C1) | ≤24h | WORSE 7× | HIGH | 1 | H | F-QB-0602 (reattr QB-FIX-10 D1) | 5 |
| 8 Publish throughput | gaming | approver seg 194.4h (C1) | ≤24h | WORSE 8× | HIGH | 1 | H | F-QB-0602 (reattr QB-FIX-10 D1) | 2 |
| 8 Publish throughput | ai_creators | approver seg 87.7h (C1) | ≤24h | WORSE 3.7× | HIGH | 1 | H | F-QB-0602 (reattr QB-FIX-10 D1) | 12 |

The "WORSE Nx" delta is now against a 24h target and reflects only the approver segment's contribution. Fetcher-latency (0.1-24h) and slot-contention (0.1-1.8h) are orthogonal and would need their own gates if flagged.

### 3. `methodology_errors.md` — ME-16 filed

New entry: **"A gate on an aggregate cannot localise a defect to a segment (third instance)."**

Explicitly names three instances documented in this audit alone:
- R-Encode-1: measured output bitrate; defect was source resolution
- F3a: measured aggregate loudness; defect was internal mix ratio
- F-QB-0602: measured total lag; defect was approver segment

Canonical statement: "any verification gate that measures an end-to-end quantity is structurally unable to localise the defect to a specific segment. A gate can go green while the responsible segment gets worse, if a different segment compensates."

**Detection heuristic:** for any proposed gate on a metric with recognisable input → transformation → output segments, the gate MUST decompose the metric into per-segment measurements and target the segment where causal responsibility actually lives. "Total X" gates are structurally incomplete without segment attribution.

## Step 4 — audit for other Phase 9 items on undecomposed end-to-end measurement

Scanned Phase 9 gap-matrix + verification gates for the pattern. Findings:

### Confirmed additional candidate

**F-QB-0101 — median video bitrate gate**

Current verification gate: `"ffprobe median vbr per niche ≥ 4 Mbps"`.

An under-4 Mbps output could be caused by:
1. Source clip is genuinely low-motion (bitrate correctly low; not a defect)
2. Source clip is low-resolution and upscaled (R-Encode-1 defect — source segment)
3. Encoder settings clipped (needs `-minrate`/`-bufsize` — encoder segment)

The aggregate cannot distinguish. R-Encode-1 addresses one of the three (source resolution) but the bitrate gate itself doesn't decompose. Someone re-verifying the gate could see it fail and misattribute to encoder settings when the actual cause is a low-motion clip (not a defect at all).

**Reported per D1 spec, not fixed.** Recommended future rewrite: decompose into (source-resolution segment, encoder-setting segment, motion-adjusted target).

### Reviewed and not matching

- **F-QB-0201** (median shot length ≤ 4s): each shot IS the atomic segment. No further decomposition possible. Aggregate is the correct measurement here.
- **F-QB-0402** (safe-zone violations): ALREADY decomposed by F3d-2 into caption-path (85%) vs intro-path (19%). No further action.
- **F-QB-0708** (caption template): aggregate over 173 blueprints, but the segments (7-block template shape) are structural, not causal. Different pattern.
- **Reward-path items (F-QB-0801/0802/0809):** structural gaps (missing wire, missing persistence), not aggregate measurements.
- **F-QB-0803** (auto-publish enabled but zero VR): explicit segment analysis (upstream sourcing vs config). Not an aggregate gate.
- **R-Fresh-1 recommendation**: literally the same misdesigned gate as F-QB-0602. Also updated (implicitly via F-QB-0602 reattribution).

## Gate

```
Reattribution applied to: phase_6_thumbnails_topics.md F-QB-0602 finding + 4 gap-matrix rows
ME-16 filed: methodology_errors.md
Other Phase 9 items resting on undecomposed end-to-end measurement:
  F-QB-0101 (bitrate gate) — same shape, reported not fixed
  All other reviewed gates: correctly targeted OR already decomposed OR structural gaps (not aggregate measurements)
```

## Commit

`docs(audit): reattribute F-QB-0602 from fetcher cadence to publish throughput`
