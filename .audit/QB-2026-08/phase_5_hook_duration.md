# Phase 5 — Hook, opening branding shot, duration (Dimensions 4, 9 + hook)

**Sample:** 20 reels across 4 niches (gaming N=0).

## Per-niche duration distribution

| Niche | N | Min | Max | Median | Std | Notes |
|---|---|---|---|---|---|---|
| ai_creators | 5 | 21.0 | 21.1 | 21.0 | 0.05 | tight distribution — hard-coded template? |
| movies | 5 | 21.0 | 21.0 | 21.0 | 0.00 | **exact-same duration all 5 reels — template fingerprint** |
| sports | 5 | 15.9 | 21.0 | 21.0 | 2.51 | mix of 16s and 21s |
| anime | 5 | 16.1 | 22.7 | 16.1 | 2.89 | 4 of 5 exactly 16.1s — **template** |

Section 1.1 row 9 benchmark: cross-platform safe zone 30-60s; entertainment/anime edits often 11-30s (**MEDIUM confidence** — targets are directional).

## Findings (6/12)

### F-QB-0501 — MEDIUM — Every niche's reel duration collapses to 1-2 discrete values across 5 samples. Movies, ai_creators = exact same duration; anime = same for 4 of 5. This is a template signature, not editorial pacing.

* **Measured value:** ai_creators variance across 5 reels = 0.05s. Movies = 0.0s. Anime = 2.89s (but mode is 16.1s). Sports = 2.51s (bimodal 16s or 21s).
* **Impact:** duration should be a lever tuned per-clip to hit completion-curve optimum. GenLab renders every ai_creators/movies reel to the same 21s regardless of source content. This is Phase-9-Tier-1: duration matched to *your own completion curve* is the target (Section 1.1 row 9), but the platform doesn't currently know its own completion curve (F-QB-0801) so tuning duration to it is impossible today.
* **Confidence:** HIGH (measurement); MEDIUM (interpretation — the fixed 21s might be intentional; needs operator confirmation).
* **Tier:** 1.
* **Verification gate:** query `SELECT AVG(views), COUNT(*), duration_bucket FROM (SELECT ROUND(EXTRACT(EPOCH FROM (extra->>'duration')::interval), 0)/5 AS duration_bucket, views FROM publishing_analytics WHERE ...) GROUP BY 1` — expect a completion curve to emerge only after the per-metric persistence gap (F-QB-0801) is closed.

### F-QB-0502 — MEDIUM — 40% of movies and sports reels have NO on-screen text in the first second — the hook is not visually present when the swipe decision fires

* **Measured value (from Phase 4 OCR pass):** movies 2/5, sports 2/5 have `any_text_in_first_1s=False`. ai_creators 5/5 and anime 5/5 have text in the first second.
* **Benchmark:** Section 1.1 row 4 MEDIUM: "no logo cold open; time-to-first-content should be ≈0s." Text-in-first-second is a proxy for the hook being visually present.
* **Impact:** movies and sports lose the visual hook slot. Combined with the (likely fixed-template) audio hook, viewers who muted the reel see nothing for ≥1 second — swipe-away.
* **Confidence:** MEDIUM (sample size).
* **Tier:** 1.
* **Verification gate:** re-OCR expect 90%+ text-in-first-1s across all niches.

### F-QB-0503 — MEDIUM — Time-to-first-content (branded cold open) not directly measured; agent report + F-QB-0009 note that no deliberate cover-frame is generated, but a "logo cold open" could still exist inside the first frames

* **Not measured.** Prompt Section 5 step 1 asked for a per-reel branded-cold-open duration. The batch measurement pass didn't compute this specifically. Approximation from OCR: any reel with text-in-first-1s=True has SOMETHING on-screen, but that could be a logo or a hook.
* **Impact:** deferred — needs frame-1 vs. logo-template diff (CLIP or template match against each channel's logo asset).
* **Verification gate:** for each channel's logo asset, template-match against each reel's first 15 frames; log frame index of first non-logo content.

### F-QB-0504 — LOW — Hook novelty across last N ai_creators publishes not measured — embedding-distance calculation deferred

* **Not measured.** Prompt Section 5 step 5 asks for embedding-distance between last-10 hooks per channel. Requires an embedding API call ($ + latency); deferred to Phase 6 where topic novelty is a paired metric.
* **Impact:** important for the Section-1.3 YouTube inauthentic-content bucket (F-QB-0708), which relies on this novelty measure.
* **Verification gate:** for the last 30 ai_creators hooks, compute pairwise embedding distance; report median.

### F-QB-0505 — LOW — CLAUDE.md's hook rules ("≤60 chars, story-specific, no generic templates") not verified against actual hook text; deferred to Phase 6

* **Not measured.** DB query is trivial (`SELECT niche_id, hook, LENGTH(hook) FROM blueprints WHERE …`) but the qualitative "story-specific" test is a manual inspection.

### F-QB-0506 — LOW — Duration correlation vs. own completion curve is impossible today because per-metric outcome data is not stored (F-QB-0801)

* **Not measurable.** No `avg_view_percentage` or `completion_rate` timeseries in the DB. F-QB-0801 is the blocker.
* **Verification gate:** first close F-QB-0801, then re-run this analysis at N ≥ 50 posts per niche.

## What was not measured (from prompt Section 5)

* Cold-open branded-frame detection (F-QB-0503).
* Hook novelty embedding distance (F-QB-0504).
* Hook-length regex check (F-QB-0505).
* Completion-curve join (F-QB-0506 — blocked by F-QB-0801).

## Sample N: 20 reels. Gaming excluded.
