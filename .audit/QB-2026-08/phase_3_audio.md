# Phase 3 — Audio, music, mix, licensing (Dimension 3)

**Sample:** 20 reels. Loudness via `pyloudnorm.Meter` (ITU-R BS.1770-4). Peak dBFS from max absolute sample value. VO-to-bed ducking approximation via sliding-400ms-window RMS distribution: `p90 − p10` in dB across windows.

## Per-niche summary

| Niche | Median integrated LUFS | Median peak dBFS | Median dynamic range (p90-p10 dB) |
|---|---|---|---|
| ai_creators | **-30.7** | -12.5 | ⚠ 174 dB (measurement broken by silence) |
| movies | **-33.0** | -16.7 | ⚠ 170 dB (same) |
| sports | -26.0 | -11.5 | ⚠ 174 dB (same) |
| anime | -18.8 | -1.6 | 6.6 dB (plausible) |

Section 1.1 row 3 benchmarks: (a) VO clearly above bed, (b) music ducked under speech, (c) integrated loudness in a sane range, (d) cuts correlated with audio onsets, (e) **licensing cleared for commercial/monetised use — HIGH confidence**.

Streaming loudness target: -14 to -16 LUFS integrated (Apple Music), -14 (Spotify), -13 to -14 (YouTube Music), YouTube post-normalisation caps at -14. Section 1.1 says "in a sane range" without a specific number — treating -20 LUFS as the outer safe band.

---

## Findings (5/12)

### F-QB-0301 — HIGH — All 4 non-anime niches are systematically 6-19 LU below the -14 LUFS streaming target. Movies is worst at median -33 LUFS. YouTube's post-processing normalisation will amplify a -33 LUFS master by ~19 dB — every existing quantisation and dither error will amplify with it

* **Measured value:** ai_creators -30.7 LUFS median, movies -33.0, sports -26.0, anime -18.8. Peak values suggest headroom is present (peak -12 to -16 dB on ai_creators/movies) but integrated loudness is low.
* **Benchmark:** streaming target -14 LUFS; Section 1.1 row 3 "sane range" (MEDIUM confidence).
* **Impact:** platform-side normalisation raises quiet audio by up to 20 dB, and every artefact from the TTS voice + the compositor's audio pipeline gets amplified with it. The audible result is TTS voice with faint hiss / crushing artefacts, not the intended clean voice. Anime at -18.8 is closer to target; the audio compositor for anime is doing something different (or the source clip's own audio is louder).
* **Confidence:** HIGH.
* **Tier:** 2.
* **Verification gate:** add a post-render EBU R128 normalisation stage targeting -14 LUFS (via `ffmpeg -af loudnorm=I=-14:LRA=7:TP=-1.5`); re-measure — expect all reels within ±1 LU of -14.

### F-QB-0302 — HIGH — No music-bed mixing code exists in the TTS/render pipeline (per Explore agent Phase 0 finding). The pipeline emits TTS-only audio; there is no bed to duck against

* **Verified from Phase 0 code inspection:** `tts/cascade.py:TTSCascade.synthesize()` outputs MP3 from one of ElevenLabs/OpenAI TTS/Edge-TTS/gTTS. No downstream music-mixer is called by `base_visual_render` or `frame_compositor`.
* **Impact:** the audit's "VO clearly above bed" and "music ducked" checks are N/A because the bed doesn't exist. From an audience-experience perspective, TTS-only short-form video is aurally flat vs. competitors that mix ambient music + voice — but adding music also creates licensing risk per Section 1.3.
* **Confidence:** HIGH (measurement); MEDIUM (interpretation of whether "no music" is a defect or a deliberate compliance choice).
* **Verification gate:** if music is added, the mixing stage must (a) duck below -6 dB during VAD-positive speech regions and (b) use only royalty-free tracks with commercial licence on file (Section 1.1 row 3 HIGH severity).

### F-QB-0303 — HIGH — No royalty-free music license documentation in the repo. The `audio.yaml` files reference no external audio sources; the codebase has no mention of a music library or licence agreement

* **Measured value:** grep `license|licensed|rights|SFX|music_lib|track_id` across `*/config/audio.yaml` and `genlab-core/src/` returns no music-license or track-source references.
* **Benchmark:** Section 1.1 row 3 licensing HIGH confidence: "licensing cleared for commercial/monetized use"; Section 1.3 warns that "business accounts are restricted to limited sound libraries; in-app availability ≠ commercial license."
* **Impact:** N/A today because no music is used (F-QB-0302). If a bed is added in the future, this is a Tier-1 compliance blocker to close BEFORE the first music-bed reel ships. This is a preventive finding, not an existing defect.
* **Confidence:** HIGH.
* **Verification gate:** before any bed is enabled, add `/audio/library/LICENSE.md` or `audio.yaml` `licence_source:` field for every track; scanner in `compliance/` blocks render if a bed track has no licence.

### F-QB-0304 — LOW — My ducking-delta metric produced unusably large "dynamic range" values (~170 dB) on ai_creators/movies/sports; the sliding-RMS windows include silent regions that give log(-∞) via `20*log10(rms + 1e-10)`. Anime's mix has continuous audio → 6.6 dB dynamic range is plausible

* **Measured value:** medians 170+ dB across 3 niches, 6.6 dB for anime.
* **Impact:** my measurement approach is broken for silence-heavy tracks. Not a defect of the reels themselves. Log per METHODOLOGY_ERROR.
* **Confidence:** HIGH (measurement was broken; findings are internal-audit-tooling only).
* **Verification gate:** switch to `pyloudnorm.Meter.loudness_range()` which is EBU R128 LRA (correctly handles silence).

### F-QB-0305 — MEDIUM — Compliance events sample shows ZERO audio-related events in 30 days (`audio_claim`, `content_id_match`, etc. would be expected event_types if platform-side audio-claim policing were logged). None found.

* **Measured value:** Phase 7 compliance_events summary shows: `ai_disclosure_added`, `pre_publish_check`, `platform_policy_block`. No `audio_claim` type.
* **Impact:** either (a) no audio claim has ever fired (plausible given TTS-only), or (b) audio-claim events are logged elsewhere and not routed into `compliance_events`. Either interpretation is compatible with F-QB-0302 (no bed → nothing to claim).
* **Confidence:** MEDIUM.

## Deferral ledger

| Item | Reason |
|---|---|
| Audio-onset vs. cut correlation | Requires audio-onset detection (librosa) + Phase 2 cut times; deferred to Phase 9 |
| Per-niche TTS voice cascade preference vs. rendered audio | Deferred — requires reading TTS logs |
| Loudness range via LRA proper metric | F-QB-0304 methodology fix; deferred |

## What was not measured

* Per-onset cut correlation (Phase 2 has cut timestamps; librosa can produce onset times).
* Real ducking delta (needs VAD; WhisperX not run).
* TTS voice consistency across platforms.

## Sample N: 20 reels. Gaming excluded.
