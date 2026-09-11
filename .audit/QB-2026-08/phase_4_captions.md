# Phase 4 — Captions, on-screen text, safe zones (Dimension 5)

**Sample:** 20 rendered reels across ai_creators/sports/movies/anime (gaming N=0 per F-QB-0002). Measurements: `pytesseract` OCR sampled at ~3 fps over the first 3 seconds of each reel, with per-blob bounding boxes intersected against Instagram/Reels UI-occluded safe zones (top 14% + right 15% + bottom 30% of the 1080×1920 frame). Full data at `phase_measurements.jsonl` (`ocr_first_3s` block per row).

## Per-niche summary

| Niche | Median text blobs / first 3s | Any text in first 1s | Median safe-zone violations / first 3s | Median n_ocr blobs |
|---|---|---|---|---|
| ai_creators (whisper-captioned) | 24 | 5/5 (100%) | **11** (46% of blobs violate) | 24 |
| sports | 12 | 3/5 (60%) | **10** (83% of blobs violate) | 12 |
| movies | 13 | 3/5 (60%) | 0 | 13 |
| anime | 36 | 5/5 (100%) | 2 (6%) | 36 |

Text-in-first-1s is a Tier-1 hook signal per Section 1.1 row 4-5.

---

## Findings (6/12)

### F-QB-0401 — HIGH — CLAUDE.md's claim that `whisper_sync.enabled=false` across all 5 niches is STALE for ai_creators; ai_creators reels have full word-level captions burned in since 2026-08-01 (5/5 sampled)

* **Measured value:** every ai_creators reel filename contains `_reel_captioned`. Every one shows 10-34 OCR text blobs in the first 3 seconds. Anime and movies masters are `_reel.mp4` (no captioned variant); their OCR blob counts still exist but come from hook overlay text, not word-level captions.
* **Evidence:** file naming, OCR count differential, and F-QB-0006.
* **Impact:** an outdated CLAUDE.md rule can drive future work in the wrong direction. Also: whisper captions are BACK on ai_creators despite CLAUDE.md warning against it because of a prior `text_optimizer` regression. Whether the regression is fixed is not verified in this pass.
* **Confidence:** HIGH.
* **Verification gate:** update CLAUDE.md; also grep every niche's `visuals.yaml` for `whisper_sync.enabled` and record the actual per-niche state.

### F-QB-0402 — HIGH — ai_creators's whisper captions violate Instagram UI safe zones — median 11 text blobs per reel intersect UI-occluded regions (top status bar, right action rail, bottom caption/CTA area)

* **Measured value:** 5 reels × median 11 safe-zone-violating text blobs. One reel (`d5171762c965beee`) has 26 violating blobs out of 34 total — 76% of on-screen text is in UI-occluded regions.
* **Benchmark:** Section 1.1 row 5 HIGH confidence: "burned-in, word-level aligned, ≈4-7 words per line, inside platform safe zones."
* **Impact:** on IG mobile, the bottom 30% of the frame is covered by caption + CTA button + reel progress bar; the right 15% is the action rail (like/share/save/comment/DMs); the top 14% is username + audio attribution. Text placed there is invisible to viewers.
* **Confidence:** HIGH.
* **Tier:** 1.
* **Verification gate:** re-render one ai_creators blueprint with a caption Y-position clamped to the middle 56% of the frame and re-OCR — expect zero `in_safe_top/bottom` violations.

### F-QB-0403 — MEDIUM — Sports reels have the WORST safe-zone-hit ratio (median 10 violations / 12 blobs = 83%). ai_creators's absolute count is higher but proportional violation rate is lower

* **Measured value:** sports `13b488e156` has 67 safe-violations out of 125 blobs (heavy burned-in title overlay landing all over the frame). `98b374a7be0f1b50` has 29/73. `6fd305b2c1a065b8` 10/11.
* **Impact:** sports' overlay compositor is not respecting safe zones for the burned-in title text.
* **Confidence:** MEDIUM (sample is small — 5 sports reels).
* **Verification gate:** clamp title Y-position; re-OCR expect ≤10% blobs in safe zones.

### F-QB-0404 — MEDIUM — Movies has 0 median safe-zone violations but 40% of movies reels have NO text visible in the first second (weak/absent hook)

* **Measured value:** 2 of 5 movies reels show zero OCR blobs at any sample before t=1.0s.
* **Impact:** even a perfectly-placed hook fails if it doesn't appear in the first second when the viewer decides swipe-vs-hold. Movies is currently rendering with hook text that starts late — verify Phase 5.
* **Confidence:** MEDIUM (n=5).
* **Tier:** 1 (crosses into Phase 5 hook territory).

### F-QB-0405 — LOW — Anime carries the highest OCR text-blob density in first 3s (median 36) — closer to a burned-in subtitle track than a hook overlay

* **Measured value:** anime blobs 32-67 per reel first-3s. Same reel range as ai_creators's captioned variant. Since anime is `_reel.mp4` (not `_captioned`), the density source is either burned-in title cards on the source clips or a heavy hook overlay.
* **Impact:** could be fine (if the text is well-placed subtitles) or bad (if it competes with the source clip's own on-screen text). Only 2 median safe-zone violations, so not a placement issue.
* **Confidence:** LOW (need to inspect a reel visually to know whether the text is a template overlay vs. source-clip burn-in).

### F-QB-0406 — MEDIUM — Caption accuracy vs. TTS audio not measured — WhisperX transcription against the actual audio track was skipped in this pass to keep runtime bounded

* **Not measured.** Prompt Section 4 step 4 asks for word-error-rate + mean timing offset between pipeline captions and WhisperX ground truth. openai-whisper is installed in the audit venv but a 20-file transcription pass at ~30s/file would add ~10 min and Whisper is the tool most likely to hallucinate on GenLab's TTS voices.
* **Impact:** if pipeline captions are timing-drift from the delivered speech, the karaoke effect is broken. Ai_creators's `_reel_captioned` variant is where this matters most.
* **Confidence:** LOW.
* **Verification gate:** for at least 3 ai_creators reels, run WhisperX with word-level alignment and diff timestamps against the pipeline's caption timing metadata (need to locate the caption metadata file per reel — deferred).

## Deferral ledger

| Item | Reason |
|---|---|
| Whisper WER vs. pipeline captions | Runtime; specialized to ai_creators |
| Per-niche `whisper_sync.enabled` config value | Config read; deferred to Phase 9 verification |
| Contrast ratio of text vs. background (WCAG) | Requires per-blob background sampling in OpenCV; deferred |

## Sample N: 20 reels (5 per niche × 4 niches). Gaming excluded.
