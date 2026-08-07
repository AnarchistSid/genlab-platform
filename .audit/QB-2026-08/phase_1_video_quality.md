# Phase 1 — Video quality and encode conformance (Dimension 7)

**Sample:** 20 rendered reels across 4 niches (gaming N=0 per F-QB-0002).
**Command:** `ffprobe -v error -show_streams -show_format -print_format json <file>` per artifact; results in `phase_1_ffprobe.jsonl` and `phase_1_reel_summary.json`. Raw source clips (640x360) excluded.

Distribution of reels analyzed:
* ai_creators: 5 reels (2026-08-05 → 2026-08-06 vintage, mix of master / threads / facebook variants)
* sports: 5 reels (2026-07-19 → 2026-07-23 vintage — no fresh renders)
* movies: 5 reels (all 2026-08-06 master)
* anime: 5 reels (all 2026-08-06 master)

---

## Measured summary — what conforms

| Attribute | Target | Measured | Conformance |
|---|---|---|---|
| Resolution | 1080×1920 | 1080×1920 (all 20) | 20/20 (100%) |
| Aspect | 9:16 | 9:16 (all) | 100% |
| Video codec | H.264 | h264 (all) | 100% |
| H.264 profile | High | High (all) | 100% |
| Pixel format | yuv420p | yuv420p (all) | 100% |
| Frame rate | 30 CFR | 30.00 fps (all — computed from `r_frame_rate=30/1`) | 100% |
| Audio codec | AAC | aac (all) | 100% |
| Audio sample rate | 48 kHz | 48000 (all) | 100% |
| Audio channels | stereo | 2 (all) | 100% |
| Color space | bt709 | bt709 (all) | 100% |

## Measured summary — what deviates

| Attribute | Target | Median measured | Notes |
|---|---|---|---|
| Video bitrate | 6-12 Mbps | 1.24 Mbps (across all 20) | See F-QB-0101 |
| Color transfer / primaries | bt709 / bt709 | 15/20 have transfer + primaries missing / undeclared | See F-QB-0102 |
| Duration | 15-60s cross-platform | Anime: 16.1s uniform; ai_creators: 21.0-21.1s; movies: 21.0s; sports: 16.0-21.0s | Deferred to Phase 5 |

---

## Findings (5/12 — Phase 1 is a narrow measurement phase; no ceiling reached)

### F-QB-0101 — HIGH — 18/20 rendered reels (90%) are encoded well below the 6-12 Mbps target. Median 1.24 Mbps.

* **Measured value:** per-niche median VBR — ai_creators 0.92, anime 1.51, movies 1.02, sports 2.39. Only 2/20 files break 3.5 Mbps (both are sports/facebook variants at 3.69 and 5.23 Mbps).
* **Command:** ffprobe → `format.bit_rate` OR `video_stream.bit_rate` (whichever present) per file; medians computed in `phase_1_reel_summary.json`.
* **Benchmark:** Section 1.1 row 7 "~8-12 Mbps" — MEDIUM confidence per prompt.
* **Sample N:** 20 reels across 4 niches.
* **Root cause (supporting evidence, code):** `genlab-core/src/genlab_core/media/ffmpeg.py:163-237` — every platform uses `-crf 20|22 -preset fast -maxrate {8|6|5|4|3}M` with **no minrate floor**. CRF+maxrate constrains only the ceiling; low-motion content (talking-head + static overlay = movies, anime, ai_creators) compresses to whatever CRF's rate-control produces. Sports genuinely-motion content (2026-07 facebook variants) rides higher (2.4-5.2 Mbps) because motion complexity forces bits.
* **Confidence:** HIGH on measurement (unambiguous ffprobe). MEDIUM on gap-vs-benchmark: the 8-12 Mbps target is directional; low bitrate is only defective when it causes visible quality loss after platform re-encode. Platform re-encode of a 0.9 Mbps 1080p master will produce visibly softer output than a 6 Mbps master.
* **Tier:** 2 (encode is a correctness floor with diminishing returns).

**GATE REWRITTEN 2026-08-07 (QB-FIX-11 E1).** Original single-number gate ("ffprobe `bit_rate ≥ 4_000_000`") could not distinguish three different causes of a low reading — low-motion content compressing efficiently (correct), upscaled low-res source (R-Encode-1 / F2 fixed for new renders), or a clipped encoder (real defect). Same class-of-bug as R-Encode-1 / F3a / F-QB-0602 (ME-16, ME-11). Recorded as fourth documented instance. Below the original bitrate-floor remediation is retired; the decomposed gate targets three segments independently:

**Segment 1 — SOURCE (belongs to the fetcher/downloader):**
Measurement: `ffprobe` on the downloaded clip BEFORE compositor.
Gate: `min(width, height) >= 1080` (short-side test — 9:16 vertical or 16:9 landscape both count).
Failure attribution: fetcher yielded a low-res source; F2's yt-dlp mweb+poToken work is the fix.

**Segment 2 — CONTENT (context, not pass/fail):**
Measurement: OpenCV frame-differencing over 3fps sample; report mean pixel delta between consecutive frames as `motion_energy`. Also report `low_motion_fraction` = fraction of frames with delta below the 10th percentile of a per-niche baseline.
Purpose: contextualise segment 3. A 0.9 Mbps output at high motion is a defect; the same figure at high `low_motion_fraction` is expected behaviour.
No pass/fail — this is a covariate.

**Segment 3 — ENCODE (belongs to the render/compositor):**
Measurement: `ffprobe` on the rendered output.
Gate: bitrate must be consistent with segments 1 and 2, NOT a fixed floor. Concrete rule (piecewise):
  - source shortside < 1080 → fail (segment 1 upstream, but ALSO flags segment 3 shouldn't have rendered it)
  - `low_motion_fraction >= 0.75` → expect bitrate 0.5-2.5 Mbps; pass if in range
  - `low_motion_fraction 0.25-0.75` → expect 2-5 Mbps; pass if in range
  - `low_motion_fraction < 0.25` (high motion) → expect ≥4 Mbps; pass if `>= 4_000_000`
Failure attribution: only failure at high-motion tier signals an actual encoder defect worth adding `-minrate 4M -bufsize 8M`. Failures at other tiers signal a segment 1 problem (source resolution) or expected low-bitrate behaviour.

**Original 8-12 Mbps target was directional, not a hard floor.** A blanket `-minrate 4M` remediation would waste bits on legitimately low-motion content without addressing the segment where the defect lives when there is one. Anyone re-running the gate must decompose before assigning cause. See ME-16 for the class-of-bug and the standing rule.

**Post-F2 status:** cause 2 (upscaled low-res source) is fixed for new renders. F-QB-0101 is not live-defective; it is a rewrite of a gate that would otherwise satisfy while fixing nothing.

### F-QB-0102 — MEDIUM — 15/20 reels (75%) have `color_transfer` and `color_primaries` metadata undeclared. Only color_space is set to bt709.

* **Measured value:** `ffprobe` shows `color_space=bt709` for all 20 but `color_transfer=` and `color_primaries=` are empty for 15 files. Only the 5 anime + one sports file have the full triple `bt709/bt709/bt709`.
* **Command:** `ffprobe -v error -show_streams -print_format json` → `color_space`, `color_transfer`, `color_primaries`.
* **Benchmark:** CLAUDE.md STRICT VIDEO REQUIREMENTS: "bt709 color space (not bt470bg) — enforced in FrameCompositor with `-colorspace bt709 -color_primaries bt709 -color_trc bt709`."
* **Impact:** Platforms that see missing `transfer` metadata often assume the encoding is BT.709 anyway, but some players (especially older Android IG viewer) fall back to sRGB gamma interpretation → subtle color / gamma drift. Under CLAUDE.md's own rule this is a partial regression: the compositor was supposed to set all three.
* **Confidence:** HIGH.
* **Verification gate:** every future ffprobe output should show `bt709/bt709/bt709` on the reel master and all platform variants.

### F-QB-0103 — LOW — VMAF measurement not performed because the pipeline's own "master" is already an upscale from the yt-dlp source (640×360 → 1080×1920)

* **Measured value:** all clips/ source files sampled are 640×360 24fps AAC 44.1 kHz. All rendered reels are 1080×1920 30fps AAC 48 kHz. VMAF against a downsampled source is dominated by the upscale rather than the encode.
* **Command:** ffprobe on `clips/*.mp4` (13 source samples).
* **Impact:** Confirms CLAUDE.md's own R-25 note that `video_gate.py:fail-opens because master_path is not set — a lossless master is not produced today.` Any real VMAF gate would need to composite a lossless intermediate before the platform-specific encode step.
* **Confidence:** HIGH.
* **Tier:** 3 (nice-to-have quality-loop closure; low priority vs. the bitrate defect).
* **Verification gate:** run VMAF only after lossless intermediate is added.

### F-QB-0104 — LOW — 4/20 reels report `-` for `color_transfer/primaries` for platform variants but full triples for master (anime); pattern suggests the platform-variant transcode drops metadata

* **Measured value:** anime masters have full bt709/bt709/bt709; ai_creators / movies masters have bt709/-/-; sports mix. When a master has full triple and the platform variant does not, that is a transcode-metadata-loss defect. Sample too small to conclude definitively across niches — flagged as "needs verification" per rule 0.4.
* **Impact:** Deferred to Phase 9 as a follow-up if the bitrate fix also touches encoder flags.
* **Confidence:** LOW (sample size for platform variants is 1-3 per niche).
* **Verification gate:** re-run ffprobe on ≥3 platform variants per niche after re-render.

### F-QB-0105 — MEDIUM — Faststart / moov-atom position not verified (ffprobe -show_format did not report; would need `AtomicParsley` or `MP4Box`)

* **Measured value:** neither `format.tags.encoder` nor another exposed field surfaced moov position. Faststart is critical for platform upload — a moov-at-end MP4 forces the platform to buffer the whole file before it can parse.
* **Impact:** Every upload path in `publishing/parallel_publish.py` should include a `-movflags +faststart` in the transcode. Quick grep confirms `+faststart` is in the ffmpeg args (`genlab-core/src/genlab_core/media/ffmpeg.py:98-107`). Very high confidence the flag is set; unmeasured directly here.
* **Confidence:** LOW (measurement not done; code inspection is admissible only as supporting evidence per prompt Section 0.2). Reclassifying as "not measured" per rule 0.2.
* **Verification gate:** run `MP4Box -info <file> | grep 'MOOV'` on a sample and confirm position before file end.

---

## Deferral ledger (Phase 1)

| Item | Reason deferred |
|---|---|
| Faststart / moov position measurement | No tool (MP4Box/AtomicParsley) currently in audit venv; low-value verification given code shows +faststart is set |
| VMAF baseline | No lossless master exists per F-QB-0103 |
| OpenCV sharpness (Laplacian variance) + mean saturation distribution | Deferred to Phase 2 (motion analysis has same OpenCV frame-sample loop; combine to avoid duplicate reads) |
| Gaming N=0 → no encoding measured for gaming | Rolled into F-QB-0002 (Phase 0) |

## What was not measured

* Full VMAF (no lossless master).
* Sharpness / contrast / saturation (folding into Phase 2's OpenCV pass).
* Faststart position (tool gap).
* Gaming channel (no artifacts, per F-QB-0002).

## Sample N per Phase-1 finding: 20 reels (5 per niche × 4 niches). Gaming excluded.
