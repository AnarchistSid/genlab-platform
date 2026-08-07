# Phase 6 — Thumbnails, covers, topic freshness/novelty (Dimensions 1, 8)

**Scope:** cover-frame reality, topic latency, source-channel diversity, hook novelty. Section 1.1 row 1 (thumbnails) is MEDIUM for FB/IG grid, LOW for Shorts — grade severity accordingly.

## Findings (7/12)

### F-QB-0601 — MEDIUM — No deliberate cover-frame generation exists in the codebase; all platforms receive the reel's default first frame as the thumbnail

* **Measured value:** verified via grep in Phase 0 (`F-QB-0009`). Publishing payload builders for IG/FB (`publishing/payload_builder.py`) do not set a `cover_frame` / `thumbnail` / `image_url` field.
* **Benchmark:** Section 1.1 row 1 MEDIUM for FB/IG grid discoverability, LOW for Shorts.
* **Impact:** on Instagram, the reel appears in the profile grid using its first frame — which is the branded intro shot if there is one (F-QB-0605 below indicates ai_creators has none). On Facebook the same first-frame is used as the video card cover. On YouTube Shorts the cover is auto-generated. Grid appearance is therefore whatever accidentally appears in frame 1 of each reel.
* **Confidence:** HIGH.
* **Tier:** 2 for FB/IG grid, 3 for Shorts.
* **Verification gate:** IG API supports `cover_url` at reel upload; assert it is set on IG publishes.

### F-QB-0602 — HIGH — Topic freshness (event → publish latency) is 4-12 DAYS median across niches. Sports (viral half-life measured in HOURS) is 7.6 days; anime is 11.7 days. "Trending" content is stale by the time it publishes.

* **Measured value:** median lag hours from source event (via `stories.published_at`) to GenLab publish: ai_creators 111h (4.6d), gaming 120h (5d), sports 182h (7.6d), anime 281h (11.7d).
* **Benchmark:** Section 1.1 row 8 LOW confidence but directionally clear: "trend freshness (event → publish latency)."
* **Impact:** the "trending" premise underlying all 5 niches is not realised. A trending topic on day-0 has already peaked by day-4-11. The ai_creators channel is the only one with lag < 5 days, and even that is 4.6× the useful window for news/viral content.
* **Confidence:** HIGH.
* **Tier:** 1.
* **Verification gate:** median lag ≤ 24h for sports/gaming, ≤ 48h for anime/movies, ≤ 72h for ai_creators (evergreen fraction is higher for AI creators).

* **REATTRIBUTION 2026-08-07 (QB-FIX-10 D1):** the total lag is queue residency under `daily_cap=1`, **not fetcher cadence**. QB-FIX-09 C1 decomposed event-to-publish into three segments on 14d of PUBLISHED rows:

  | niche | fetch (h) | approver (h) | slot (h) | total (h) |
  |-------|-----------|--------------|----------|-----------|
  | ai_creators | 11.0 | **87.7** | 0.1 | 87.8 |
  | anime | 24.2 | **151.9** | 0.7 | 152.5 |
  | gaming | 0.1 | **194.4** | 0.2 | 194.6 |
  | movies | 6.2 | 21.8 (F4) | 1.8 | 23.6 |
  | sports | 11.7 | **169.1** | 0.2 | 169.3 |

  The **approver segment** (`auto_approver._pick_next_available_slot()` first-fit-forward walk on IST days 0-7 against daily_cap=1) dominates 92%-100% of total lag on every niche. The fetchers were never the constraint. Original framing (fetcher cadence problem) was wrong.

* **REWRITTEN VERIFICATION GATE (D1):** target the approver segment, not total lag. Measure `PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (scheduled_for - created_at))/3600)` per niche on rows published in the last 14 days. Target: `≤ 24h` for all niches. Fetcher-latency gate (`created_at - stories.published_at`) is orthogonal and can be measured separately; the original per-niche thresholds (24/48/72h) never applied to the correct segment.

* **Class-of-bug link (ME-16):** third instance of gate-design failure where an aggregate is measured but the defect lives in a segment. Same shape as R-Encode-1 (measured output bitrate; defect was source resolution) and F3a (measured aggregate loudness; defect was internal mix ratio). The pattern is documented in methodology_errors.md ME-16.

### F-QB-0603 — HIGH — Every niche is >95% mono-source: ai_creators=95% `youtube_trending`, gaming=95% `twitch_trending`, anime=97% `youtube_trending`. The topic taxonomy has one tag per niche

* **Measured value:** `SELECT topic, COUNT(*) FROM blueprints GROUP BY 1;` per niche shows ≥95% of posts share a single topic label.
* **Benchmark:** Section 1.1 row 8 — evergreen vs newsjacking mix; also Section 1.3 YT inauthentic-content bucket #1 "generic/repetitive/template-based content."
* **Impact:** the `topic` field is effectively `source_pipeline` — there is no per-blueprint topic classification. This means (a) the bandit's `topic` context feature (`linucb.py` context vector) collapses to a constant per niche and offers no signal, (b) topic-diversity metrics cannot be computed retrospectively, (c) the YT inauthentic-content risk from F-QB-0708 is amplified because "repetitive theme" is now measurable at the topic-tag level.
* **Confidence:** HIGH.
* **Tier:** 1.
* **Verification gate:** enrich the `topic` field with per-story topic-classification (game title, movie franchise, anime title, sport/team, AI-product/company); expect ≥20 distinct topics per niche within 30 days.

### F-QB-0604 — MEDIUM — Source-channel concentration on ai_creators: 25 of 42 blueprints (60%) draw from just 5 YouTube creator channels

* **Measured value:** top-5 `source_channel_id` values for ai_creators account for 25 blueprints (6+5+5+5+4). The `intelligence-state-audit-2026-07-16` note mentioned "Top-creator upload watcher" as a mechanism, so this is likely the intended behaviour. But at 60% concentration on 5 creators, ai_creators is functionally "The 5 Creators You Follow Anyway" not "AI News."
* **Impact:** limits topic variety; makes the channel highly dependent on those 5 creators' schedules; if 2 of them pause posting, ai_creators's own posting rate would drop. Also amplifies the F-QB-0603 mono-topic effect.
* **Confidence:** HIGH.
* **Tier:** 2.

### F-QB-0605 — MEDIUM — Hook length rule (`≤60 chars`, CLAUDE.md) holds at 100% across all 5 niches (0 blueprints over 60 chars in 30 days). Hooks are the ONE quality invariant this audit found intact end-to-end

* **Measured value:** `SUM(CASE WHEN LENGTH(hook_text) > 60 THEN 1 ELSE 0 END)` per niche = 0 for all 5 niches. Averages: ai_creators 42, gaming 35, movies 48, anime 54, sports 54. Minimums: 13-32 chars.
* **Benchmark:** CLAUDE.md CONTENT QUALITY RULES: "≤60 characters, story-specific."
* **Impact:** POSITIVE — a rule that has held. Also a data point that when a rule is codified with a specific numeric threshold (60 chars), the compliance is visible and enforceable. Contrast with the caption disclosure position rule (F-QB-0701) which has no such enforcement.
* **Confidence:** HIGH.
* **Verification gate:** re-run trailing-30 -day hook-length check monthly; alert on any regression.

### F-QB-0606 — MEDIUM — Hook novelty: only 2 identical-hook duplicates in 30 days across 173 hooks (rule "same hook cannot appear twice in same niche" — mostly holding). Both hits are on gaming: `"League of Legends"` (5×) and `"They actually added THIS to Fortnite"` (2×)

* **Measured value:** `SELECT niche_id, hook_text, COUNT(*) FROM blueprints GROUP BY 1,2 HAVING COUNT(*) > 1;` → 2 rows.
* **Impact:** the `"League of Legends"` × 5 case is exactly the `hook_bare_title` failure the pre-render gate `pre_render_quality.check_pre_render_quality` was built to catch (per CLAUDE.md). 5 blueprints reached DRAFTED/VISUAL_READY with a bare title as the hook. Either the gate rejected them (which would leave them as DRAFTED forever) or the gate has a bug.
* **Confidence:** HIGH.
* **Tier:** 2 — cross-references F-QB-0002 (gaming render dead).
* **Verification gate:** the 5 `"League of Legends"` gaming blueprints should have `status='DRAFTED'` with `extra->>'render_error'='pre_render_quality:hook_bare_title'`. Verify.

### F-QB-0607 — LOW — Embedding-distance based hook/topic novelty across last 30 posts not measured — deferred to Phase 9 verification queries (needs an embedding API call)

* **Not measured.** Prompt Section 6 step 5 asks for embedding distance. Adding this requires an embedding call (OpenAI / local model). Deferred.
* **Impact:** F-QB-0708's YT inauthentic-content template-signature exposure is strengthened by low novelty. Confirming numerically would sharpen the case.

## Deferral ledger

| Item | Reason |
|---|---|
| Embedding-distance novelty | External embedding call; deferred |
| CLIP aesthetic score on cover frame | Cover doesn't exist per F-QB-0601 |
| Published post metadata check (does IG actually accept cover) | Requires operator-side API test |

## Sample N: 173 blueprints (30 days). Gaming, anime, movies missing from Phases 1-5 sample but present in Phase 6 (this is DB-only, not artifact-dependent).
