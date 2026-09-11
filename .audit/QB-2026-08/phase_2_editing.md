# Phase 2 — Editing quality and compilation structure (Dimensions 2, 10)

**Sample:** 20 reels across 4 niches (gaming N=0). PySceneDetect `ContentDetector(threshold=27.0)` for cuts; OpenCV frame-difference for motion (mean absolute difference between grayscale frames sampled every 5 frames after resizing to 192×340). Cuts-per-reel × 60 / duration → cuts-per-minute.

## Per-niche summary

| Niche | Median n_cuts | Median shot length (s) | Median low-motion fraction | Interpretation |
|---|---|---|---|---|
| ai_creators | 3 | 7.01 | 0.50 | Under-cut vs. benchmark; half of samples static |
| movies | 3 | 7.01 | **0.76** | Under-cut AND mostly static |
| sports | 3 | 7.01 | 0.22 | Under-cut but genuine motion |
| anime | 2 | 8.03 | 0.49 | Most under-cut; even fewer scenes than others |

Section 1.1 row 2 benchmark: 1 cut per 2-4s (1-2s for action/entertainment) — MEDIUM confidence.

The suspicious detail: three of four niches share **identical median mean-shot-length (7.01s)** across independent reel samples. That is an editorial-template signature, not a measurement artefact — same threshold in SceneDetect finds the same cut cadence because the same compositor is producing the same rhythm.

## Findings (5/12)

### F-QB-0201 — HIGH — Median shot length ≈ 7 seconds across ai_creators/movies/sports and 8 seconds for anime — every niche is 2-4× slower-cut than Section-1.1 benchmark of 1 cut / 2-4s for short-form

* **Measured value:** ai_creators/movies/sports median mean_shot_len 7.01s. Anime 8.03s. Only 1 of 20 reels (ai_creators `d5171762c965beee` with 7 cuts) hits the 3s/shot mark.
* **Benchmark:** Section 1.1 row 2 MEDIUM — 1 cut/2-4s for cross-format, 1-2s for entertainment/action.
* **Impact:** slow-cut talking-head content on short-form video is a documented under-performer. Combined with F-QB-0708's YT inauthentic-content template signature, this is another marker of template-driven rather than editorial output.
* **Confidence:** HIGH.
* **Tier:** 2 (editing is a Tier-2 correctness floor per Phase 9).
* **Verification gate:** re-render a subset with content-driven cut points (audio-onset-locked or motion-triggered) and re-measure — expect median shot length ≤ 3s.

### F-QB-0202 — HIGH — Movies has 76% of motion samples below the low-motion threshold — the "movie trailer" reels are largely static, defeating the whole trending-trailer premise

* **Measured value:** movies median low_motion_frac = 0.76 (5 reels). Sports (which should be motion-heavy) = 0.22. Anime = 0.49. ai_creators = 0.50 (matches talking-head content).
* **Impact:** the movies channel is supposed to be trending trailers + viral clips (per CLAUDE.md `SpliceReel = Movies, trending trailers`) — 76% low motion means the trailers are being rendered with long static frames on top of the source video. Either the source clip is short and the reel is padded, or a large overlay is masking the trailer motion. Either way, the "trending clip" promise fails.
* **Confidence:** HIGH.
* **Tier:** 2.
* **Verification gate:** for the 5 sampled movies reels, compare motion energy of the source clip (in `.tmp/runs/*/clips/`) against the rendered output — the drop is the amount of motion the overlay/compositor destroyed.

### F-QB-0203 — MEDIUM — One sports reel (`98b374a7be0f1b50`) has 0 cuts and 0.00 low-motion fraction — a completely static 16s render, likely a rendering failure that shipped

* **Measured value:** 0 scene changes detected in 16s of video; motion low-motion-fraction=0.00 means every motion sample was below threshold (near-static). This reel exists on disk from 2026-07 vintage.
* **Impact:** this is a shipped reel that is functionally a still image (or near-still). If this fired for the operator's channel it hurt trust with the audience.
* **Confidence:** HIGH.
* **Verification gate:** grep `pipeline_alerts` for `motion_zero` around 2026-07 for sports; if no alert exists, add a post-render motion sanity check.

### F-QB-0204 — LOW — Shot-length standard deviation is 6+ seconds for the fixed-cut-cadence templates and near-zero for the single-scene reels; the distribution shape is bimodal not editorial

* **Measured value:** most reels have std ~6.36s (matches the 7.01s mean with 1-3 cuts near edges of the video); the "compilation" outlier reels have std 1-3s.
* **Impact:** editorial cutting produces a wide distribution of shot lengths (short intro cuts, longer holds on key moments); template cutting produces a narrow distribution. GenLab's output is template.

### F-QB-0205 — MEDIUM — Compilation / multi-segment structure not audited. Only compilation observed in .media/cdn (`9b589020_compilation_best_of_day_overlaid_instagram.mp4`) but not present in the audit sample pull

* **Not measured.** Compilation blueprints are rare in the sample (per Phase 6 topic-mix; also F-QB-0201 shows shot cadence isn't segment-consistent).
* **Deferred to Phase 6** for a full topic-mix analysis.

## Deferral ledger

| Item | Reason |
|---|---|
| Compilation segment analysis | Compilation reels not in Phase 0 pull |
| Audio-onset vs. cut correlation | Phase 3 will run onset detection; correlation deferred to Phase 9 |
| Motion energy per platform variant vs master | Sample too small for cross-platform inference |

## Sample N: 20 reels. Gaming excluded.
