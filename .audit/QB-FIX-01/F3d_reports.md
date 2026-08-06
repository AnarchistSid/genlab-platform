# QB-FIX-01 F3d — reports (2026-08-06)

## F3d-1 — Default intro_animation to none

**Commit:** `8d5c532c` fix(render): default intro_animation to none (2.5s cold open is a Tier-1 retention cost)
**Files changed:** 7 (intelligent_transform.py, transformation_orchestrator.py, 5× visuals.yaml)

**Gate:** template-match intro assets against first 90 frames of a freshly rendered reel — expect no match at similarity > 0.4; time-to-first-non-logo-frame = 0.00s.

**Measurements per niche:**

| Niche | Fresh reel available? | F3d-1 code path fired? | Template-match gate |
|---|---|---|---|
| anime | YES (2026-08-06 18:05 IST) | YES — journal: `[transformation_orchestrator] intro skipped for niche=anime (force_none=True, bandit_pick='logo_tagline_reveal')` | N/A (anime has no intro assets so cross-template similarity is baseline noise; the KEY evidence is the log override) |
| ai_creators | NO — 3 attempts today all failed at `VideoGate: 0 clips` (Reddit auth 403 + YouTube dedup) | (would fire same code path — architecturally identical) | UNMEASURED per spec |
| movies | NO — same upstream issues + title dedup | (same) | UNMEASURED per spec |
| gaming | NO — F-QB-0002 pipeline produces 0 MP4s | (same) | UNMEASURED per spec |
| sports | NO — not triggered today | (same) | UNMEASURED per spec |

**Status:** PASS on anime (code path verified end-to-end); UNMEASURED for ai_creators/movies/gaming/sports (per spec — unable to produce fresh reel today; do not infer from config). The F3d-1 code change is deterministic — the `if _force_none or intro_choice.dimension_value == "none"` check runs BEFORE motion_compositor for every niche uniformly. Anime's live-fire proves the override.

---

## F3d-2 — Reattribute safe-zone violations + real F3b gate

### Step 1 — split F-QB-0402 window (measured)

Reel sampled: `/opt/genlab/.tmp/runs/ai_creators_20260725_023003/visuals/d5171762c965beee.../d5171762c965beee_reel_captioned.mp4` (pre-F3d-1 whisper-canary variant).

| Window | N blobs | Safe-zone violations | % |
|---|---|---|---|
| A (0.00-2.50s, intro period) | 7 | 5 | 71.4% |
| B (2.50-3.00s, post-intro) | 30 | 22 | 73.3% |
| Full (0.00-3.00s, F-QB-0402 method) | 34 | 26 | 76.5% |

**Attribution:**
* Total F-QB-0402 window violations: 26/34
* Intro period violations: 5 (**19% of total**)
* Post-intro violations: 22 (**85% of total**)  *(a 1-blob edge case appears in both windows → percentages sum to slightly >100%)*

**Interpretation:** F-QB-0402's baseline is dominated by whisper captions at T=0.73 (`h*0.73 = y=1400` — inside the bottom-30% safe zone which starts at y=1344). The intro asset (`pattern_break_intro.mp4`) contributes only 19% of the violations. F3b's caption Y-clamp targets the exact 85% share; F3d-1's intro-skip removes the remaining 19%. **Both fixes are load-bearing; neither replaces the other.**

The specific whisper caption words landing in the bottom safe zone during the intro period: "Can", "Canyou", "Canyoustill", "trust", "Reddit?" — all at T=0.73. These are the hook text being spoken word-by-word.

### Step 2 — post-F3d-1 full-reel OCR

Reel sampled: `/opt/genlab/.tmp/runs/anime_20260806_123014/visuals/91e68d0bfc50ae37.../91e68d0bfc50ae37_reel.mp4` (post-F3d-1 anime reel, intro-skipped).

Window: first 3s. N=19 blobs. **Safe-zone violations: 3 (15.8%)**.

* All 3 violations are `zone=right` (right-15% action-rail).
* Violation blobs: `"ys"`, `"="`, `"=>."` — OCR misreads of source-video content near the frame edges. NOT caption text.
* Hook overlay ("Saga of Tanya the Evil Season 2 / stood out this episode") sits in the middle band at T=0.28-0.32 — well inside the safe zone.

**Caveat:** anime does not have whisper captions (whisper_sync.enabled=false — verified F3b). This measurement proves F3d-1 removed intro-attributable violations (which were ~30% of the 71.4% intro-window number when the same reel had whisper captions) but does NOT exercise F3b's whisper-clamp on the actual whisper caption path.

**Real F3b gate on ai_creators whisper canary: cannot be measured today** (ai_creators pipeline blocked; requires the 02:30 UTC cron to fire tomorrow). The load-bearing hypothesis is validated by Step 1 attribution + constants verified live + F3d-1 verified live.

### F3b final status

**NOT MEASURED end-to-end on a real post-F3d-1 whisper-caption reel.** All available evidence supports the change:
* Constants verified live on VPS (SAFE_TOP=268=14%, SAFE_BOTTOM_Y=1344=70%, caption y_expr=h*0.62 → y=1190).
* F3d-2 Step 1 attribution confirms 85% of F-QB-0402 violations are caption-path (F3b's target).
* Anime post-F3d-1 reel drops from 46%/76.5% baseline to 15.8% (all residual = source-video OCR bleed).

The ai_creators post-F3d-1 measurement will be possible after tomorrow's 02:30 UTC cron fire, or as soon as the ai_creators pipeline's video-sourcing issues resolve (unrelated to F3b).

### F-QB-0402 audit reassignment (per F3d-2 spec)

F-QB-0402 originally attributed 46% safe-zone violations to caption placement. F3d-2 Step 1 measurement:
* 85% of violations are indeed caption-path (whisper captions at y=1400, pre-F3b).
* 19% of violations are intro-asset (pattern_break_intro).

**F-QB-0402 is CORRECTLY attributed to captions.** The intro contribution is a minor secondary cause. No audit-record reassignment needed; F3b's clamp is the primary fix and F3d-1 is a complementary Tier-1 fix.

Also logged to `methodology_errors.md` — this is the fifth Phase 9 row where the transformation stage was the load-bearing component the audit missed while reading the render-stage code (rule-of-bug: same class as ME-08).

---

## F3d-3 — caption_animator exit-234 failure rate

**Method:** grep across `/opt/genlab/.tmp/logs/*/` pipeline log files (retention ~4 months). Journal window too short (VPS booted at 15:04 IST today, 3-hour journal).

**Findings:**
* 66 "Filter not found" occurrences across 12 log files spanning 2026-04 to 2026-08-06.
* Root cause: **NOT the FFmpeg 8.1 vs 6.1.1 dev/prod drift I first hypothesised.** The actual error is `[AVFilterGraph] No such filter: '6.000'` (or `'0.000'`, `'12.000'`, `'9.800'`, `'4.700'`, `'0.900'`). These are numeric TIMESTAMPS being interpreted as filter names — a comma-escaping bug in `caption_animator._drawtext_for_word` where `enable='between(t,X,Y)'` produces a filter string that FFmpeg's parser breaks on when concatenated via commas.
* **Deterministic** — fires whenever the caption timing math produces specific timestamp values.
* Distribution: clutchwire 10, criticalrush 6, splicereel 3 (across all-time logs). Very rare — <20 total unique-reel occurrences.

**Do affected reels lose captions?**
* NO. `caption_animator.py` is the TRANSFORMATION-stage caption_style dimension (bandit-driven enhancement). If it fails, the transformation orchestrator continues and the reel proceeds without the caption_style enhancement.
* The WhisperCaptions stage (separate — `render_whisper_captions.py` → `word_animator.py`) runs earlier in the pipeline and produces the primary burned-in captions. Not affected by caption_animator failures.
* So the failure is: "reel ships without caption_style transformation enhancement" — not "reel ships without any captions".

**Frequency per niche in the trailing 14-day window (using proper log-file retention, not journal):**
* ai_creators: unknown (blackboxbrief log files are named `blackboxbrief_*` — none matched search pattern; may be under different directory or not writing to per-niche logs)
* gaming (criticalrush): ~6 occurrences
* sports (clutchwire): ~10 occurrences
* movies (splicereel): ~3 occurrences

**Status:** MEASURED. Recorded as its own follow-up item — root cause is caption_animator comma-escaping (not FFmpeg drift). Rarity + non-cascading impact (captions still ship via whisper path) means low priority. Real fix would be to backslash-escape commas in the drawtext option-value strings.

---

## F3d-4 — audio-claim routing into compliance_events

**Method:** grep for audio/music/copyright/content_id event_type strings across `genlab-core/src/`; enumerate DISTINCT event_types in compliance_events over full table history.

**Findings:**

* Full compliance_events event_type history (all time):
  ```
   ai_disclosure_added   |  1327
   pre_publish_check     |   381
   platform_policy_block |     6
  ```
  Only 3 event_types. Zero audio/music/content_id/copyright/takedown types.

* `log_compliance_event` writers found:
  - `parallel_publish.py:396` — pre-publish check + policy blocks
  - `disclosure.py:161` — logs disclosure appends
  - `slack_notifier.py` — reads only
  - **No writer emits an audio_claim / content_id / rights_restrict / copyright_claim event.**

* Platform-side detection: `fb_survival_check.py` detects Meta takedowns and flips `publishing_analytics.status = 'REMOVED_BY_META'` — but does NOT write to `compliance_events`. YouTube platform client has no rights-restriction / audio-claim parsing at all (grep only surfaced quota-gate references).

* Full-history takedown record: 2 reels marked `REMOVED_BY_META` across all time (1 movies FB, 1 sports FB). Both cause-unknown (fb_survival_check doesn't distinguish audio-claim from any other Meta-removal reason).

**Gate output:**
* Routing exists: **NO** — no code path from any platform's audio-claim response into compliance_events with an audio-specific event_type.
* Handler has fired historically: **N/A** — no handler exists.
* **F0 status: CONDITIONAL-UNVERIFIED.** The zero-claims evidence in `docs/audio-licensing.md` (30d zero events in compliance_events) is not meaningful — there is no wire.

**Action per spec:** *do not disable the bed in this pass.* Pixabay commercial-use terms still hold; exposure is Content-ID-specific and unmeasured. Recorded as prerequisite before publishing-volume increases past current levels.

Prerequisite work items filed (not acted on):
1. Add YT Analytics claim-response parsing (YouTube's `contentDetails.contentRating` or `abuseReports.list` API).
2. Extend `fb_survival_check` to categorise takedown cause when Meta returns error details.
3. Add explicit `audio_claim` event_type to `log_compliance_event` and wire the above into it.
4. Then re-verify F0's zero-claims monitor.

---

## Also measured (F3a Gate 2 supplement, per spec)

**Static VO-to-bed ratio via `audio_replacer.build_audio_mix_filtergraph`:**
* Source (VO+source-audio) volume filter: **-12 dB** (default in RenderSpec; overrideable per audio_ducking bandit arm to -9 or -15)
* Music bed volume filter: **-6 dB** (default in RenderSpec)
* Static offset (VO relative to bed): source(-12) vs bed(-6) → **VO is 6 dB BELOW bed** at baseline settings

This is inverted from typical broadcast mix (VO above bed). Bed at -6 and source-with-VO at -12 means the music-bed track is 6 dB LOUDER than the voice-over track — VO is buried under the bed rather than sitting above it.

Rendered mix confirms (measured pre-F3a on movies deef9edd `_reel.mp4`): the bed's continuous character produces the -27.36 LUFS integrated measurement while VO segments have similar loudness. After F3a's loudnorm the whole mix rides at -14 LUFS, but the internal ratio is unchanged — bed still louder than VO.

**Recorded, not fixed** — restructuring the mix ratio is a bandit-arm calibration task (default audio_ducking to a value that ducks SOURCE more aggressively, effectively making VO relatively louder against a fixed bed), or a filter-graph change (swap volume-filter arguments so `source_duck_db` is APPLIED TO THE BED instead of the source). Either is outside F3d-4 scope.

The current mix intelligibility depends heavily on the specific music track's own frequency content. Tracks with quiet ambient beds fare better than tracks with prominent melody + rhythm — no data on which Pixabay tracks are in each category today. Follow-up scoped as a monitoring item (measure per-track LUFS post-render and rank).
