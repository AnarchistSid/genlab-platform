# QB-FIX-01 F3a-2 + F3d-1-gate — reports (2026-08-06)

## F3a-2 Step 1 — filter graph diagnosis (STOP-and-report per spec §2 Step 1 item 2)

**Structure found (audio_replacer.py:156-160):**
```
[0:a]volume={source_duck_db}dB[a1];   # input 0 = source video's audio track
[1:a]volume={music_bed_db}dB[a2];     # input 1 = music bed mp3
[a1][a2]amix=inputs=2:duration=first[aout]
```

**Answer to §2 Step 1 Q1: NEITHER shared-path NOR separate-TTS/source-inputs.** 

`[0:a]` is the source video's own audio track alone. TTS is NOT in the reel at all — the `_audio.mp3` produced by GenerateAudio is consumed only by WhisperX for word-level caption timestamps (`render_whisper_captions.py:130`). No ffmpeg call in genlab-core/src/, BlackboxBrief/bb_strategies/, or any niche */strategies/ mixes the TTS mp3 back into the reel. Empirical: reel audio mean_volume ≈ source clip audio mean_volume across sampled niches.

**Operator design intent confirmed:** TTS is captions-only (visual sound-off recovery). Source video's own audio IS the primary aural signal. F3a-2 fix = swap the mix ratio so source sits above bed.

## F3a-2 Step 2 — value swap (shipped)

Commit: `67901e85` fix(audio): correct inverted VO-to-bed mix ratio (voice was 6dB under music)

Changes:
* `MusicMoodConfig.source_audio_duck_db`: -12 → -6 (Pydantic default)
* `MusicMoodConfig.music_bed_db`: -6 → -20 (Pydantic default; 14dB margin)
* 5× visuals.yaml: same value overrides applied
* 5× audio_ducking.levels_db: `[-9,-12,-15]` → `[-3,-6,-9]` (recentered on new -6 source centre)
* FrameDrift previously had -9/-9 "conservative" with note "source audio matters more" — converged to standard -6/-20 (the new default is already conservative-enough by construction)

Verified across all 5 niches via Pydantic + YAML load: source=-6, bed=-20, margin=14dB, arms=[-3,-6,-9] uniformly.

**Gate: measured on a fresh reel — UNMEASURED. Movies re-render (the intended gate carrier) failed today. F3a-2 will auto-verify on the next successful movies pipeline run.**

---

## F3d-1-gate — BLOCKED (spec §3 escalation)

Third movies pipeline attempt today (18:37 IST post-F3a-2 deploy): 0 blueprints produced. Systemd exit=2/INVALIDARGUMENT.

**Concurrent failure modes:**

| Failure | Detail | Blueprints lost |
|---|---|---|
| Reddit 403 | 6 of 10 movies subreddits return 403 (auth expired/missing). Only DC_Cinematic + r/horror + one more respond via RSS. | Fraction of Reddit candidates |
| URL dedup at PushToBacklog | 4 of 5 YouTube trending candidates match existing DRAFTED/VISUAL_READY blueprints in the active-blueprint set. | 4 of 5 candidates |
| yt-dlp extractor error | Remaining candidate (IEFmpe2QYA8) fails: "unable to extract yt initial data". Earlier same URL hit "unavailable in your country". Extractor instability. | 1 of 1 remaining |
| **VideoGate** | 0 clips validated → 1 story dropped → 0 blueprints → SLO VIOLATION | Terminal |

Queue depths per niche (dedup-blocking active blueprints):
```
ai_creators   13 VISUAL_READY
gaming        11 VISUAL_READY + 3 DRAFTED = 14  (despite F-QB-0002 zero renders)
movies         6 VISUAL_READY + 5 DRAFTED = 11
anime          6 DRAFTED
sports         4 DRAFTED
```

Configured dedup TTL: `url_dedup_ttl_days: 3` (in `BlackboxBrief/config/niche.yaml` + `SpliceReel/config/niche.yaml`). BUT the actual dedup query at `pipeline/dedup_keys.py` considers ALL blueprints in DRAFTED/VISUAL_READY status regardless of age — the TTL is not enforced. Blueprints older than 3 days still block new fetches, contradicting config intent.

**Immediate unblock options (recorded per §3, NOT acted on):**
1. Archive DRAFTED/VISUAL_READY blueprints older than 3 days:
   ```
   UPDATE blueprints SET status='ARCHIVED'
     WHERE status IN ('DRAFTED','VISUAL_READY')
     AND created_at < NOW() - INTERVAL '3 days';
   ```
   Affects: 11+ movies, 14+ gaming, subset of others.
2. Fix `pipeline/dedup_keys.py` to honour `url_dedup_ttl_days` from niche config.
3. Provision Reddit auth cookies for the fetcher — restores 6 of 10 subreddits.
4. yt-dlp extractor errors are upstream — occasional per-URL, no local fix.

## F4 executability

**F4 is NOT executable today.** Movies is not an outlier — ai_creators, gaming, sports, anime all hit variants of the same sourcing failures. The dedup + Reddit + yt-dlp problems are systemic across niches.

Further, F4's design assumes the queued blueprints can be re-rendered. The current pipeline architecture:
- Fetches NEW content every run (not a re-render-existing operation)
- Runs URL dedup against active blueprints (blocking re-fetch of the same URL)
- Has no "reprocess queued blueprint with current code" primitive

Even with F1-F3d shipped, the queued blueprints in DRAFTED/VISUAL_READY are locked in their pre-fix state until either:
- The dedup TTL is honoured + they age out + fresh runs re-fetch the same URLs
- Some kind of forced re-render primitive is added
- Blueprints are archived and identical content becomes fetch-able again

The proper F4 unblock is:
1. Fix Reddit auth + URL dedup TTL (2 code changes, ~1 day)
2. Archive stale queue OR add re-render primitive
3. Wait for at least one successful pipeline run per niche to produce F1-F3d-fixed reels
4. Then F4's "re-render, manually publish, then rollout_pct 0.1" flow works
