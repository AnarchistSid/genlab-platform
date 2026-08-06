# QB-FIX-03 W3 — SpliceReel Copyright Exposure Decision

**Date:** 2026-08-06 23:15 IST
**Deferred four times prior.** This is a decision that requires operator input. No change ships from this document. The deliverable is the analysis, the options, and a recommendation.

## Convergence of findings

Five independent audit signals point at SpliceReel:

| Finding | Detail | Severity |
|---------|--------|----------|
| F-QB-0701 | 17/17 affiliate posts had disclosure past the fold (closed by F1 but happened) | HIGH → RESOLVED by F1 |
| F-QB-0708 | Caption template matches inauthentic-content bucket signature | HIGH |
| F-QB-0202 | 76% low-motion despite trending-trailer channel | MEDIUM |
| Section 1.3 | Highest copyright exposure of 5 niches — movie trailers | STRUCTURAL |
| **V1 (new)** | **No transformative element remains: no original VO, full-length borrowed audio, hook overlay only** | **NEW after QB-FIX-02** |

## Transformation ratio — measured on F4 batch 1

Measurement per §5 Step 1. Both movies reels in F4 batch 1:

### INHERIT (F4 batch 1 blueprint `e625488a…`)

| Layer | Value |
|-------|-------|
| Reel duration | 18.6s |
| Source clip duration | 52.06s |
| **Source-used ratio** | **35.7%** (18.6/52.06) |
| Source clip resolution | 1920x1080 AV1 |
| Reel resolution | 1080x1920 (9:16 crop of source) |
| Video content in reel | 100% source footage (letterboxed) |
| Audio content in reel | 100% source audio dominant (music bed at -20dB inaudible per V1) |
| **Original visual overlay** | hook text (44px, ~50-60 chars), channel logo (60px), black chrome bars (~58% of frame vertically) |
| **Original audio** | 0 seconds spoken narration (V1 confirmed); music bed effectively silent |

### Primetime (F4 batch 1 blueprint `c25972a9…`)

| Layer | Value |
|-------|-------|
| Reel duration | 18.6s |
| Source clip duration | 136.23s (2:16) |
| **Source-used ratio** | **13.7%** (18.6/136.23) |
| Source clip resolution | 1920x1280 AV1 |
| Reel resolution | 1080x1920 (9:16 crop of source) |
| Video content | 100% source footage |
| Audio content | 100% source audio dominant |
| **Original visual overlay** | hook text + logo + chrome bars |
| **Original audio** | 0 spoken narration; music bed inaudible |

### Aggregate

**Reel content structurally consists of:**
- 100% borrowed video
- ~100% borrowed audio (source dominant at -6dB, music bed at -20dB is 14dB below)
- ~10-15% of frame area = original overlay (hook text + logo + chrome bars)
- 0% original audio content

Source-use fraction: **~14-36% of the source clip**. Reel duration ~19s vs Section 1.3's "very short clips (<~7s)" benchmark for transformative-use safety.

**This is a re-upload with a caption.** None of Section 1.3's three transformative patterns holds.

## Precedent

- **Screen Culture** (~1.4M YouTube subs) — permanently terminated December 2025 for blending copyrighted movie footage with AI-generated material under YouTube's spam and misleading-metadata policies. Terminated following a Disney cease-and-desist to Google.
- **KH Studio** (~600K subs) — permanently terminated same window, same category.

Both channels had significantly more transformative content than SpliceReel does today (they added AI-generated elements; SpliceReel adds a hook overlay). The termination-signal precedent applies directly.

## Options

### Option 1 — Continue as-is

**Cost:** accept termination risk on one channel. Estimated risk timeframe: 6-12 months to first strike based on Screen Culture/KH Studio timing pattern.

**What continues to work:** the pipeline ships as configured, no code change, no operator effort.

**What breaks eventually:** either (a) Content ID muting/region-restricting individual posts (V1 makes this the likely first symptom), (b) DMCA takedowns from studios directly, (c) YouTube spam/misleading-metadata strike, (d) Meta Rights Manager blocking Instagram uploads.

**Reversibility:** low. After 3 strikes / 90 days: channel termination is permanent.

### Option 2 — Restructure toward defensible pattern

Three sub-options:

**2a — Shorten clips to <7s per Section 1.3.**
- Config change: `highlight_moment.window_seconds: 16 → 6` in `SpliceReel/config/visuals.yaml`.
- BUT: fingerprint matching operates on any duration; the <7s figure is for "very short clips illustrating original analysis" — the reel still needs the ORIGINAL ANALYSIS layer to be transformative. Shortening alone reduces watch time signal without adding a defense-worthy pattern.
- Cost: config-only. Effect: partial hedge; likely still terminated eventually.

**2b — Add original commentary replacing source audio.**
- V1 established TTS is absent from every reel; `audio_replacer`'s 2-input `amix` (source + music bed) needs to become 3-input (source + music bed + TTS commentary).
- Requires: (i) writer produces a script distinct from the caption (or repurposes it), (ii) TTS cascade already runs — just needs mux wiring, (iii) mix-ratio decision (source ducked hard to background so commentary dominates → complete inversion of F3a-2's design intent), (iv) voice selection + quality bar.
- Cost: substantial build. 1-2 weeks of focused work per V1's scoping note.
- Effect: strong defense; commentary + hook text + reduced source audio matches transformative-use patterns.

**2c — Add data-viz / analysis overlay.**
- New visual layer: box-office numbers, rating comparisons, cast/crew stats over the trailer footage.
- Requires: (i) data source (TMDB, IMDB), (ii) chart/table renderer, (iii) compositor integration, (iv) design template.
- Cost: substantial build. Different visual identity for the channel.
- Effect: moderate defense; adds an "original analysis" layer that Section 1.3 counts as transformative.

### Option 3 — Pivot channel format

Redesign SpliceReel around a defensible content type:
- **Ranking format:** "Top 5 A24 trailers of 2026" using stills + <3s clips from each
- **Talking-points format:** LLM-generated commentary read via TTS over stills or very short cuts
- **News/analysis format:** box-office data + industry commentary + stills

Cost: full channel redesign. Complete change in brand voice, content type, and viewer expectation.

### Option 4 — Retire SpliceReel; reallocate

Accept the loss of one channel. Redirect the daily-cap slot + compute + operator attention to:
- **ai_creators** (lowest fingerprint exposure — creator's own content, transformative fair-use commentary common)
- **gaming** (medium exposure but game footage tolerance is much higher; streamer commentary + gameplay SFX rarely trigger)
- **sports** (similar exposure to movies but league DMCA is less trigger-happy than studio DMCA — leagues prefer to monetize claims rather than terminate)
- **anime** (similar exposure; V3 fetcher fix unblocked; watch this niche's claim rate under §1 first)

Cost: loss of one revenue-tracking niche. Zero build effort.

## Recommendation

Adopting **Option 2b (add original commentary via TTS mux)** IF the operator wants to keep SpliceReel on the platform. This is the only option that materially reduces termination risk. Every other structural fix is either a partial hedge (2a, 2c) or a full pivot away from the channel's stated purpose (3).

**BUT: Option 2b requires the TTS mux build that V1 confirmed absent.** That is not a config change; it's a 1-2 week engineering item with its own design questions (mix ratio, voice selection, per-niche script variance). If that build effort exceeds the value SpliceReel delivers, **Option 4 (retire) is the pragmatic answer.**

**Option 1 (continue) is inadvisable.** The precedent is specific, recent, and applicable. Recommending against.

## Read-across (note for the record)

The same analysis applies in weaker form to:

- **FrameDrift (anime promos):** source-use ratios similar (13-36%); anime studios (Toei, Aniplex, MAPPA, Kadokawa) are aggressive Content ID matchers. F4 batch 1 anime posts are the first to publish under this configuration; §1 watch outcome directly informs the anime read.
- **ClutchWire (sports highlights):** leagues use Content ID + DMCA but prefer monetization to termination. Lower termination risk than movies; higher claim/revenue-share risk.

Both scoped OUT of W3. If W3's SpliceReel decision is Option 2b or Option 4, apply the same analysis to FrameDrift and ClutchWire as separate decisions with their own scoping.

## What to do while operator decides

- **§1 watch continues.** All 4 F4 posts (2 movies + 2 anime) publish tomorrow + day-after. Audio-plays verification per §1 pre-authorization.
- **No config change to SpliceReel** in this pass. `source_audio_duck_db=-6` preserved (per W0 supersede).
- **No new SpliceReel blueprints beyond F4 batch 1** should be approved for publish until the operator's Option decision. Manual gate at approval time.

## Operator decision line (fill in)

```
Operator decision (2026-__-__): Option ___
Reasoning: ___
Effective date: ___
```

## Commit

`docs(strategy): SpliceReel copyright exposure assessment post-V1`
