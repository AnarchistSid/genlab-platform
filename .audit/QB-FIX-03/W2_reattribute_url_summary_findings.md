# QB-FIX-03 W2 — Reattribution: 836 URL-Dominant Summaries × 5 Phase-9 Findings

**Date:** 2026-08-06 23:00 IST
**Result:** the URL-summary defect contributes partially to F-QB-0606 (27% overlap) and F-QB-0605 (indirectly), does NOT explain F-QB-0603 (measurement was on source not topic), and is orthogonal to F-QB-0708 and F-QB-0403. Two Phase 9 items become lower-value once fetcher fix is live; three remain independently necessary.

## Overlap measurements

### F-QB-0606 — bare-title hooks

**All bare-title hooks in DB** (hook_text alphanumeric-equal to title):

| niche | count |
|-------|-------|
| sports | 90 |
| ai_creators | 48 |
| movies | 26 |
| gaming | 25 |
| anime | 11 |
| **total** | **200** |

**Bare-title hooks WHERE linked story summary is URL-dominant:**

| niche | count | % of all bare-title |
|-------|-------|---------------------|
| sports | 21 | 23% |
| gaming | 13 | 52% |
| movies | 10 | 38% |
| ai_creators | 8 | 17% |
| anime | 2 | 18% |
| **total** | **54** | **27%** |

**Attribution:** URL-summary defect explains ~27% of bare-title hooks. **The remaining 73% have a different cause** — likely writer template-rescue paths, LLM refusal-preamble recovery, or the pre-render `hook_bare_title` gate failing to trigger. The URL-summary fix will only halve gaming's bare-title problem; sports (largest absolute count) barely moves.

**Downgrade:** F-QB-0606 severity stays MEDIUM. URL-summary fix reduces incidence by ~27%; full fix requires investigating the other 73% (writer template rescue + pre-render gate coverage).

### F-QB-0605 — 100% hooks ≤60 chars

Not a direct query — the finding is trivially satisfied by bare titles. Game names, movie titles, and anime titles are usually 15-40 chars. When the writer produces a bare-title hook, it clears the 60-char gate by construction.

Averages per niche (from finding): ai_creators 42, gaming 35, movies 48, anime 54, sports 54.

**Gaming's 35-char average is suspicious.** Bare game names like "Fortnite" (8), "Overwatch" (9), "Rust" (4), "League of Legends" (17) skew the average down. If we removed the 25 bare-title gaming hooks, the remaining ~50 gaming hooks would show a much higher average (closer to the LLM-generated 45-50 char norm).

**Reframe:** F-QB-0605's "100% compliance" is not a quality win — it is met by the defect it should have caught. **Mis-graded.** Should be reclassified as neutral or downgraded from "quality win" to "gate coverage insufficient." The real hook-quality gate is `hook_bare_title` and `hook_title_truncation` in `pre_render_quality.check_pre_render_quality` (per CLAUDE.md §Pre-render quality gate) — that gate's rejection rate is the actual quality signal, not the ≤60 char ceiling.

### F-QB-0603 — mono-topic ≥95% per niche

Result of the topic query per niche — the "topic" column is actually a SOURCE label:

| niche | top "topic" (== source) | total | url-dominant subset |
|-------|-------------------------|-------|---------------------|
| ai_creators | youtube_trending | 190 | 14 (7%) |
| ai_creators | rss | 96 | 54 (56%) |
| anime | youtube_trending | 226 | 10 (4%) |
| anime | anilist | 82 | 43 (52%) |
| gaming | twitch_trending | 116 | 87 (75%) |
| gaming | steam_spike | 76 | 65 (86%) |
| movies | youtube_trending | 215 | 24 (11%) |
| movies | tmdb_trailer | 57 | 30 (53%) |

**Two findings visible in this table:**

1. **F-QB-0603's "topic" column is actually a SOURCE label**, not a semantic topic. "youtube_trending" is not a topic; it's where the story was fetched from. The finding was measuring source concentration, not topic diversity. **The URL-summary defect is orthogonal** — even if all summaries were rich prose, this "topic" column would still show source-based concentration.
2. **URL-summary rates vary drastically by source.** Feed-derived sources (`rss`, `anilist`, `steam_spike`, `tmdb_trailer`) have 50-86% URL-dominant summaries; live-API sources (`youtube_trending`) have 4-11%. The Reddit fix touches only Reddit-sourced stories; other feed-based sources (RSS, AniList — pre-V3 fetcher fix, and TMDB, Steam) may have their own summary defects worth investigating.

**Downgrade for F-QB-0603:** MEDIUM → LOW. The finding is a measurement artifact of the "topic" column being source labels. Reframe as "source diversity per niche is 1-3 dominant sources" — that's an ingestion architecture observation, not a content quality problem. Actual semantic topic diversity would need a real topic classifier over hook + caption text.

### F-QB-0708 — 7-block caption template signature

The template is applied by `base_writing._assemble_platform_body()` on every write, regardless of summary quality. The finding was that all 173 blueprints share the same 7-block template shape.

The URL-summary defect does not cause the template — the template is imposed by the writer's platform-body assembly code. What the URL-summary defect DOES do is make each block's CONTENT weaker (repetitive language when the LLM has nothing new to say), potentially aggravating the "generic / repetitive / template-based" signal.

**Not directly attributable to URL-summary.** F-QB-0708's severity is set by the template shape, not the content variance. Fixing summaries will improve the CONTENT of each block but the SHAPE (7 fixed blocks: hook, sentence, hashtags, CTA, attribution) is a writer-architecture decision.

**Reframe:** F-QB-0708 remains independently necessary. The writer's platform-body assembly should be varied per-post (e.g., some posts skip the CTA, others fold the attribution into the hook, some have 2 sentences, some have 4). That's a writer-refactor item, not a fetcher-fix side effect.

### F-QB-0403 — sports 83% safe-zone violation

The finding attributes this to sports' overlay compositor not respecting safe zones with burned-in title text. That is a compositor rendering bug (`base_visual_render` + `frame_compositor` interaction with sports-specific title-overlay code path).

Weak plausible link to URL-summary: if the compositor uses `title` as the overlay text when `hook_text` is a bare title (they'd be equal), the overlay might render longer text than expected. But that assumes the compositor branches on title-vs-hook, which needs verification.

**Not directly attributable to URL-summary.** F-QB-0403's severity is set by the compositor's overlay positioning, not the source of the text. Fixing summaries will not change safe-zone violation rates.

## Attribution summary

| Finding | Original severity | URL-summary attribution | Post-fix expected change | Recommended new status |
|---------|-------------------|-------------------------|--------------------------|----------------------|
| F-QB-0606 bare-title hooks | MEDIUM | 27% partial | -27% overlap eliminated; other 73% needs writer/gate fix | **MEDIUM — partially closed by V4; independent writer investigation required** |
| F-QB-0605 100% ≤60 char hooks | MEDIUM (as quality win) | trivially met by bare titles | metric compliance would drop but the real gate is `hook_bare_title` | **DOWNGRADE — mis-graded as win; actual signal is pre-render gate rejection rate** |
| F-QB-0603 mono-topic ≥95% | HIGH | orthogonal (topic column is source label) | no change | **DOWNGRADE to LOW — measurement artifact; needs real topic classifier** |
| F-QB-0708 caption template | HIGH | orthogonal (template is writer-imposed) | content variance may improve slightly | **UNCHANGED — writer template refactor is a separate item** |
| F-QB-0403 sports safe-zone | MEDIUM | orthogonal (compositor bug) | no change | **UNCHANGED — compositor fix required independently** |

**Two Phase 9 items downgraded (F-QB-0605 to lower, F-QB-0603 to lower). One partially closed (F-QB-0606). Two unchanged (F-QB-0708, F-QB-0403).**

## Prospective fix verification

Query on stories created in the last 4h (post-V4 deploy at commit `5c6f9965` ~22:15 IST):

```sql
SELECT niche_id,
  SUM(CASE WHEN summary ~ '^https?://'
         OR LENGTH(REGEXP_REPLACE(summary, 'https?://\S+', '', 'g')) < 40
      THEN 1 ELSE 0 END) AS url_dominant,
  COUNT(*) AS total_new
FROM stories WHERE created_at > NOW() - INTERVAL '4 hours' GROUP BY niche_id;
```

Result:
```
 niche_id | url_dominant | total_new
----------+--------------+-----------
 movies   |            3 |         3
```

**All 3 fresh movies stories still have URL-dominant summaries** — because they were fetched by the movies pipeline that ran 19:17 IST, BEFORE the V4 fix deploy at ~22:20 IST. The fix is on VPS but no fresh pipeline has fired since deploy.

**Anime pipeline ran 20:08 IST (before V4 deploy).** Anime stories from that run: not URL-dominant because the V3 anime-fetcher fix (`39bc5c0e`) had already synthesized real summaries from AniList descriptions. So V3 covers anime; V4's writer-gate change is the fresh preventative layer.

**Hook quality spot-check on the 6 fresh (movies + anime) blueprints:**

| niche | title (first 30) | hook |
|-------|------------------|------|
| anime | Saga of Tanya the Evil Season | "Why is NUT adapting Tanya's most brutal arc?" |
| anime | From Old Country Bumpkin to Ma | "Katainaka no Ossan went full swordmaster in Season 2" |
| anime | Trapped in a Dating Sim: The W | "Trapped in a Dating Sim: The World of Otome Games is..." |
| movies | A Connecticut Yankee In King A | "Bing Crosby predicts an eclipse to save medieval England" |
| movies | Primetime | Official Trailer | | "Primetime is a Thai body horror film from A24. Without..." |
| movies | The Teaser For "INHERIT": A Th | "Thai body horror that makes Tusk look restrained" |

**Zero bare-title hooks on the 6 fresh blueprints.** The Dating Sim hook LOOKS bare-title in the display truncation but the character count is 51 (not equal to title). The writer produced substantive hooks on all 6 despite 3 of them (movies) having URL-dominant story summaries — the LLM was resourceful with title + other fields.

**Deferred gate:** run the "0 URL-dominant summaries in stories created in last 2h" query after the next natural pipeline fire (nightly cron 03:30 UTC or manual re-trigger). Post-V4 code is present on VPS.

## What Phase 9 remediation becomes lower-value

- **F-QB-0605-driven work** — dropping the ≤60-char metric from the "quality win" section of Phase 9 and reframing as "gate coverage insufficient" — the actual invariant needing tests is pre-render `hook_bare_title` rejection rate.
- **F-QB-0603-driven work** — no need to invest in taxonomy-broadening if the finding is a measurement artifact. Actual work needed: implement a real semantic topic classifier over hook + caption, then re-measure diversity.

## What Phase 9 remediation remains independently necessary

- **F-QB-0708 writer template refactor** — 7-block shape is writer-architecture, not fetcher-content. Vary the shape per-post.
- **F-QB-0403 compositor safe-zone fix** — sports overlay compositor bug. Independent of hook text source.
- **F-QB-0606 residual 73%** — the writer's template-rescue path and the pre-render gate's coverage need direct investigation. URL-summary fix takes ~27% of bare-title hooks off the table; the other 73% still need work.

## Read-across (out of scope)

The overlap query showed non-Reddit feed sources (RSS, AniList before fix, TMDB, Steam) also have high URL-dominant rates (50-86%). Only the anime AniList path was fixed by V3 + V4. The Reddit path is fixed by V4. **TMDB, Steam, RSS feed synthesizers may have the same class of bug at additional layers**, unmeasured. Filed as follow-up: audit `TMDBFetcher`, Steam trending fetcher, and per-niche RSS-to-story conversion for summary-quality parity.

## Not backfilling

Historical blueprints already shipped. 200 bare-title hooks are in the past. The fetcher + writer-gate fixes cover the class prospectively.

## Commit

`docs(audit): reattribute hook and topic findings to upstream summary defect`
