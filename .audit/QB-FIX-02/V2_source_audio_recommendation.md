# QB-FIX-02 V2 — Per-Niche `source_audio_duck_db` Recommendation

**Date:** 2026-08-06 21:45 IST
**Deliverable:** written recommendation only. **No value changed in this pass** (per V2 measure-only scope + §1's pre-authorization).

> **SUPERSEDED 2026-08-06 22:30 IST by QB-FIX-03 W0.** The V2 recommendation to attenuate source_audio_duck_db from -6 to -9 dB on sports/movies/anime is withdrawn. Rationale: audio fingerprinting (Content ID, AudibleMagic, Meta Rights Manager) matches on signal CONTENT, not signal LEVEL — a 3 dB attenuation does not reduce match probability, only makes the reel quieter. Section 1.3 explicitly notes 2026 fingerprinting detects low-volume beds (same mechanism catches music mixed under speech). Combined with V1's finding that source audio is now the ONLY aural content a reel has (no TTS narration to compete with, no reason to duck), the correct decision is **keep -6 uniformly across all five niches on audio-quality grounds**. Copyright exposure has to be addressed where it actually lives: clip length as a fraction of source, how much of source is used, whether anything original sits on top. None of those are volume knobs. W3 (QB-FIX-03) addresses the real levers for SpliceReel specifically. Original recommendation preserved below for audit traceability; **do not adopt**. See methodology_errors.md ME-13 for the class-of-bug analysis.

## V2 Step 1 — override surface confirmed

`source_audio_duck_db` and `music_bed_db` are set in each niche's `<Niche>/config/visuals.yaml` under `intelligent_transform.dimensions.music_mood`. All 5 niches currently hold identical values as of F3a-2:

```yaml
intelligent_transform:
  dimensions:
    music_mood:
      source_audio_duck_db: -6
      music_bed_db: -20
```

Per-niche differentiation available without any code change — the YAML surface is already there.

## V2 Step 2 — per-niche exposure inventory

Grounded in V1's finding that TTS is absent (source track IS the entire aural signal), fingerprint exposure = source-track exposure.

| Niche | Primary source type | Sample source material | Fingerprint risk | Notes |
|-------|--------------------|-----------------------|------------------|-------|
| **ai_creators** | AI-tool creator YouTube clips | LangChain demo, GPT-4 tutorial, Claude walkthrough | **LOW** | Creator's own commentary + screen-share; talking-head content usually fair-use commentary; low Content ID coverage |
| **gaming** | YouTube trending gaming clips | GTA V, League of Legends, Fortnite gameplay | **MEDIUM** | Some game OSTs fingerprinted (Nintendo especially); streamer commentary often layered; usually transformative-use tolerated |
| **sports** | Sports highlight plays | NFL, NBA, MLB, FIFA moment clips | **HIGH** | Leagues run aggressive Content ID + issue DMCA; commentary audio itself often triggers |
| **movies** | Movie trailers + TMDB-fetched film clips | Warner/A24/Universal trailers, VIZ Media clips | **VERY HIGH** | Studios use AudibleMagic + Content ID on trailers directly; character dialog and score are both fingerprinted |
| **anime** | AniList seasonal promos + Jikan/YouTube trending anime | Bleach TYBW (VIZ), Solo Leveling (Aniplex), Ghibli material | **VERY HIGH** | Toei Animation, Aniplex, Kadokawa, MAPPA — all aggressive Content ID; music + dubbed dialog trigger |

**Current uniform setting `-6` was applied to all five on the same day it shipped into F4's publish batch, before any claim-detection wire existed.** That sequencing is the V2 finding, independent of what value is ultimately correct.

## V2 Step 3 — recommendation

I would set:

| Niche | Current | Recommended | Delta | Reasoning |
|-------|---------|-------------|-------|-----------|
| **ai_creators** | -6 | **-6** (keep) | 0 dB | Low risk; creator's own audio; source-first is correct default |
| **gaming** | -6 | **-6** (keep) | 0 dB | Medium risk; game SFX + streamer commentary often fair-use transformative; keep source dominant, monitor via §1 watch equivalent when gaming pipeline is unblocked |
| **sports** | -6 | **-9** | -3 dB | High risk; source audible but not dominant hedges league Content ID; commentary-heavy source content is the most fingerprinted category |
| **movies** | -6 | **-9** | -3 dB | Very high risk; trailer dialog + score matter for viewer but are directly fingerprinted; 3dB back-off preserves aural presence without maximum exposure |
| **anime** | -6 | **-9** | -3 dB | Very high risk; anime OST + dubbed dialog matter for viewer but are extensively Content ID matched; matches movies rationale |

## Why this shape, not others

* **Uniform -6 (status quo):** maximizes viewer experience, unbounded fingerprint exposure. The audit's V2 concern is that this was applied uniformly without deliberation — this recommendation is the deliberation.
* **Uniform -12 (pre-F3a-2 revert):** minimizes fingerprint risk but re-creates the "source barely audible" problem F3a-2 fixed. Rejected — the fix was correct as an audio fix, only the uniform application is under review.
* **Per-niche differentiation as above:** source stays dominant on all niches; ai_creators + gaming get the full F3a-2 improvement; sports + movies + anime get a 3 dB hedge that still keeps source audible but reduces fingerprint amplitude. Rejected uniform values because the exposure genuinely differs across niches.

**-9 dB was chosen (not -8 or -10) because it lands on a standard audio-level step and re-uses one of the recentered `audio_ducking.levels_db: [-3, -6, -9]` arms the F3a-2 commit shipped**, meaning the bandit already has an arm at -9 to explore on high-risk niches. That's a coherence bonus, not a design driver.

## What resolves it

No measurement resolves this. It is a risk decision made deliberately vs inherited from a default.

The value that WOULD resolve it — actual claim-detection observations — requires:

1. **Fix ME-10** (F0 zero-claims monitor has no consumer): wire platform audio-claim responses into `compliance_events` with a distinct `event_type='audio_content_id_match'` (or per-platform variant), so YouTube Content ID mutes/region-restricts, Meta Rights Manager matches, and TikTok audio-claim webhook responses become observable telemetry rather than silent.
2. Accumulate ≥30 publishes per niche with the wire live.
3. Bandit-arbitrate `source_audio_duck_db` values based on actual claim rate per niche.

Until then, the pre-authorization in §1 lets me back off any niche whose F4 batch 1 post gets claimed. This V2 recommendation is a starting distribution for that future measurement, not a substitute for it.

## Sequencing note (V2 finding)

The uniform `-6` was applied to all five niches in the same commit as F3a-2 shipped (`67901e85` on 2026-08-06), then baked into F4 batch 1 approvals ~4 hours later, before any claim-detection wire existed. That sequencing — new-audio-config → in-batch — is the V2 finding. Independently of whether -6 is the right value, the audit surface for "does this ship OK?" was empty when it shipped.

The pre-authorization in §1 (back off source_audio_duck_db if any post is claimed) is the correct compensating control until ME-10 is fixed.

## What NOT to do in this pass

- **Do not change any `source_audio_duck_db` value** except as authorized by §1 (post claimed → back off for that niche only).
- **Do not flip `rollout_pct: 0.1` for movies or anime** until batch 1's 48-hour survival is confirmed.

## Recommendation summary

Filed for operator review. Adopt or reject via a subsequent explicit "change these YAML values" instruction.
