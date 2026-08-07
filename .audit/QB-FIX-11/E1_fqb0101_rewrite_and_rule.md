# QB-FIX-11 E1 — F-QB-0101 Gate Rewrite + Aggregate-Gate Rule Codified

**Date:** 2026-08-07 17:50 IST
**Result:** F-QB-0101 gate decomposed into 3 segments (source / content / encode). Aggregate-gate class closed prospectively via a standing audit rule appended to methodology_errors.md, referencing the four documented instances.

## Rewrite applied

### `phase_1_video_quality.md` F-QB-0101 finding

Original single-number gate ("ffprobe `bit_rate ≥ 4_000_000` after adding `-minrate 4M -bufsize 8M`") — retired.

New decomposed gate:

**Segment 1 — SOURCE (fetcher/downloader):**
- ffprobe on downloaded clip BEFORE compositor
- Gate: `min(width, height) >= 1080` (short-side test)
- Failure attribution: fetcher yielded low-res source; F2's yt-dlp mweb+poToken is the fix

**Segment 2 — CONTENT (context, not pass/fail):**
- OpenCV frame-differencing over 3fps sample
- Record: `motion_energy` (mean pixel delta) + `low_motion_fraction` (fraction below 10th percentile of niche baseline)
- Not a gate — a covariate for segment 3

**Segment 3 — ENCODE (render/compositor):**
- ffprobe on rendered output
- Piecewise gate based on segments 1 + 2:
  - source shortside < 1080 → fail (upstream, but also flags segment 3 shouldn't have rendered it)
  - `low_motion_fraction ≥ 0.75` → expect 0.5-2.5 Mbps
  - `low_motion_fraction 0.25-0.75` → expect 2-5 Mbps
  - `low_motion_fraction < 0.25` → expect ≥ 4 Mbps (real encoder-defect signal)
- Only failure at high-motion tier justifies adding `-minrate 4M -bufsize 8M`

**Cost avoided:** the original blanket `-minrate 4M` remediation would waste bits on legitimately low-motion content (talking-head ai_creators, static-overlay anime) without addressing the real encoder segment issue when there is one. The gate as originally written would satisfy while fixing nothing.

### `phase_9_synthesis.md` gap-matrix

4 F-QB-0101 rows relabeled from "Video quality" (aggregate 8× target delta) to "Encode segment" — see F-QB-0101 rewrite. Aggregate delta figures retired.

### `methodology_errors.md` — standing rule appended

Above ME-16 (which is the specific instance memo), added a **STANDING AUDIT RULE** section:

> Any verification gate on an end-to-end or aggregate quantity MUST decompose the metric into per-segment measurements before a cause is assigned. Aggregate-only gates are structurally incomplete — they can go green while the responsible segment gets worse (if a compensating segment covers), and they can go red on a segment that is not the actual constraint.

Application requirements (4 rules):
1. Identify each segment explicitly
2. Provide per-segment measurement OR note deferred segments + reason
3. Assign a target per segment independently
4. State how per-segment measurements combine into the aggregate

**Anti-pattern to reject:** "gate on total X" where X is composed and the finding does not say which segment causally produces failure.

Referenced four documented instances (R-Encode-1, F3a, F-QB-0602, F-QB-0101) as the evidence base for the rule. Any future aggregate-only gate is a rule violation to be logged as a fifth-instance ME entry.

## Gate

```
F-QB-0101 finding text: REWRITTEN with 3-segment decomposition
phase_9_synthesis rows: 4 rows relabeled to "Encode segment" (aggregate delta retired)
methodology_errors.md: STANDING AUDIT RULE section appended above ME-16
Aggregate-gate class: CLOSED PROSPECTIVELY (rule targets future finding design)
```

## Commit

`docs(audit): decompose F-QB-0101 bitrate gate and codify the aggregate-gate rule`
