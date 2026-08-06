# QB-FIX-01 F4 — Staged Publish Batch 1

**Date:** 2026-08-06 20:15 IST (movies queued 19:30; anime unblocked + queued 20:15)
**Status:** BOTH NICHES QUEUED. 4 blueprints across 2 days.

## What shipped

### Movies — batch 1 (2 blueprints approved)

| Blueprint | Title | Scheduled | Publisher fire | Verified fixes |
|-----------|-------|-----------|----------------|-----------------|
| `e625488a…` | The Teaser For "INHERIT" (Thai horror) | 2026-08-07 06:00 UTC | 2026-08-07 12:05 IST (06:35 UTC) | F1 no-CTA, F3d-1 intro-skip, F3a loudnorm |
| `c25972a9…` | Primetime \| Official Trailer \| A24 | 2026-08-08 06:00 UTC | 2026-08-08 12:05 IST | F1 no-CTA, F3d-1 intro-skip, F3a loudnorm |

Approval mechanism: direct SQL UPDATE with `action_taken_source='qb_fix_01_f4_manual'` (distinct tag so calibration_logger's confusion matrix skips these, same treatment as auto-approver `AUTO_APPROVAL_SOURCE_TAG`).

`scheduled_for` set 35 min BEFORE publisher timer fires to avoid race with `NOW()` comparison.

### Movies — housekeeping

- **Archived:** `0d436dba…` Spider-Man Brand New Day — pre-fix render, was `action_taken=approved` with `scheduled_for=2026-08-05 10:00 UTC` but never actually published (2 days stale). Would have taken priority over fresh blueprints. Marked `auto_archived_qb_fix_01_f4_pre_fix`.
- **Left alone:** 6 pre-fix VISUAL_READY unapproved + 5 DRAFTED. Won't publish (unapproved), safe until rollout_pct flip. Archive them BEFORE flipping `rollout_pct: 0.1` in step F4.4.
- **Reserved:** `b2292ede…` Yankee — third fresh reel, unapproved. `[gate] LLM judge fired for niche=unknown rule_decision=False` flag on this row means data-shape defect (empty `niche_id` reached the gate). Investigate before approving.

## Fresh-reel verification (recap from Reddit-fix session)

Confirmed live on `a4fd2c5f6e94fc79_reel.mp4` (INHERIT):
- **F3a loudness:** LUFS -13.26 (target -14 ±1 → PASS)
- **F3d-1 intro override:** template similarity -0.026 vs `logo_tagline_reveal` (expect <0.4 → PASS)
- **F2 encode:** 1080x1920 from 1080p AV1 source
- **F3a-2 mix:** `[audio_replacer] mixing: (duck=-9 music_bed=-20)` — 11 dB source-over-bed margin
- **F3d-1 override fired on all 3 bandit picks:** `logo_tagline_reveal`, `logo_zoom`, `pattern_break_intro` — none escaped to compositor.

Confirmed via SQL:
- `affiliate_cta` NULL, `affiliate_url` NULL on all 3 fresh rows → **F1 gate PASS**
- Caption prefix starts with story hook, no `→ [CTA]` tail

**F3b is NOT verified on these reels.** Correcting a session-summary drift (QB-FIX-02 V0-b): my in-conversation recap listed F3b among "verified live" fixes for the F4 batch. That was wrong. Per `F3d_reports.md` §"F3b final status": F3b's caption safe-zone clamp is **NOT MEASURED end-to-end**. Movies has `whisper_sync.enabled=false` — the movies reels here cannot exercise the whisper-caption clamp. F3b's real gate remains the ai_creators whisper canary, still pending the ai_creators pipeline's video-sourcing unblock. F3b constants are verified in code (SAFE_TOP=268, SAFE_BOTTOM_Y=1344, `y_expr=h*0.62`) and the F3d-2 attribution analysis validates the load-bearing hypothesis, but the movies + anime reels in F4 batch 1 do NOT constitute a caption-clamp measurement. `F3d_reports.md` is authoritative.

## Anime — UNBLOCKED (fix shipped this session)

Anime pipeline was blocked on **0-blueprint-from-N-stories** across 3 consecutive runs today. Root cause identified: `FetchAnimePromos` (phase1_fetchers_extra) hardcoded `summary=""` in the story dict → writer's ≥40 char `_has_writable_context()` floor → LLM skipped → template rescue → push rejects "ALL platform bodies empty".

**Fixes shipped (2 commits):**
- `c17ff1bd` — `TrendingVideo._writable_summary()`: fallback when YouTube snippet empty (covers backbone fetcher; universal, not niche-specific)
- `39bc5c0e` — `FetchAnimePromos._build_promo_summary()`: pulls AniList description/genres/studios into story summary + synthesizes fallback from title for Jikan (which has no synopsis API)

**Live-verified on run `anime_20260806_142754`:**
- 20 AniList promos fetched (Jikan hit external 504 timeout — unrelated)
- 3 blueprints created (was 0 before fix)
- Real captions: Saga of Tanya S2 mentions NUT Studio; Master Swordsman II uses Japanese original title "Katainaka no Ossan"; Dating Sim references character Mob
- F1 no-CTA held (all 3 rows have empty `affiliate_cta`)
- F3d-1 intro-skip fired via journal evidence

### Anime blueprints approved into F4 batch

| Blueprint | Title | Scheduled | Publisher fire |
|-----------|-------|-----------|----------------|
| `586686be…` | Saga of Tanya the Evil Season 2 | 2026-08-07 06:00 UTC | 2026-08-07 12:05 IST |
| `b8de02a6…` | From Old Country Bumpkin to Master Swordsman II | 2026-08-08 06:00 UTC | 2026-08-08 12:05 IST |

**Reserved:** `e1ada462…` Dating Sim (DRAFTED, render didn't complete — separate render failure).

### Remaining follow-ups (NOT blocking F4)

1. **Rule #26 sweep** for `push_to_backlog` "0 blueprints" branch — exit=0 with WARN. Pipeline still exits `2/INVALIDARGUMENT` on the "one story got DRAFTED not VISUAL_READY" path even with mostly-successful runs.
2. **fetch_reddit_clips.py permalink-as-summary** at `:254` + `:512` — passes 40-char shape gate but is semantic garbage. Same class as this bug at a different layer.
3. **Jikan `/anime/{mal_id}/full` synopsis lookup** — deferred; ~7s extra latency per run, only useful if Jikan promos become dominant candidate source.

## What to check tomorrow

**2026-08-07 ~12:15 IST** (10 min after publisher fires):

```bash
ssh genlab-prod 'sudo -n docker exec genlab-postgres psql -U genlab_app -d genlab -c "
  SELECT id, LEFT(title, 40) AS title, status, jsonb_pretty(platform_publish_status) AS pub_status
  FROM blueprints
  WHERE id = '"'"'e625488a-400c-4d29-9656-cf308729990a'"'"';
"'
```

**Success criteria:**
- `status = PUBLISHED`
- `platform_publish_status` shows YT/FB/IG/Threads success entries
- Live post URLs render (F2 1080p source, F3d-1 no intro, F3a normalized audio, F1 no CTA in caption)

**On success:** approve day-3 batch (Yankee if `niche=unknown` gate resolved, else next fresh movies run). After 3 clean manual publishes → **archive pre-fix VISUAL_READY leftovers → flip `auto_publish.enabled: true`, `rollout_pct: 0.1` in `SpliceReel/config/publishing.yaml` → then anime once fetcher is fixed**.

**On failure:** debug from `platform_publish_status` per-platform error string. Do NOT flip rollout_pct until manual batch is clean.

## Sign-off note

F4 as originally spec'd was movies+anime. Both queued this session — movies via approve-fresh-post-fix path, anime via fix-fetcher-then-approve-fresh path. **100% shipped for batch 1** pending tomorrow's publisher-fire verification.

The anime unblock was a genuine additional discovery beyond QB-FIX-01 scope: the fetcher summary-emptying bug had been silently causing 0-blueprint anime runs for an unknown duration (all recent anime runs hit the same wall). Fix is universal (any YouTube-source niche benefits from `_writable_summary()`), not just anime.
