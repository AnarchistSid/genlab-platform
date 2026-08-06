# QB-FIX-02 V1 — TTS Presence Test (Measure-Only)

**Date:** 2026-08-06 21:35 IST
**Verdict:** TTS is **ABSENT** from final rendered reels.

## Method

Definitive one-command test per §3: whisper-transcribe final reel audio; if transcript = GenLab caption text, TTS is present; if transcript = source video dialogue / music only, TTS is absent.

## Setup

Two reels selected — one from each of the two niches whose F4 batch 1 blueprints just approved (movies + anime, the two highest copyright-exposure niches with the newly-boosted source audio):

| Niche | Reel | Blueprint | Duration | Audio |
|-------|------|-----------|----------|-------|
| movies | `a4fd2c5f6e94fc79_reel.mp4` (INHERIT) | `e625488a…` | 18.6s | AAC 48kHz stereo |
| anime | `91e68d0bfc50ae37_reel.mp4` (Tanya S2) | `586686be…` | 16.1s | AAC 48kHz stereo |

Whisper model: `small`, English, run against final rendered reel (post-transformation, post-loudnorm).

## What the transcript should contain if TTS is present

Movies INHERIT blueprint text (what edge-tts/openai-tts/elevenlabs would speak):
> **hook:** Thai body horror that makes Tusk look restrained
> **caption:** This teaser just rewired my entire nervous system. Thai horror isn't playing by Western rules anymore—practical effects, genuine dread, and a premise that gets worse the...

Anime Tanya S2 blueprint text:
> **hook:** Why is NUT adapting Tanya's most brutal arc?
> **caption:** NUT just announced Season 2 and they're going straight into the light novel's darkest stretch. Tanya's about to face…

## What the transcript actually contained

**Movies INHERIT (full transcript):**
```
What's wrong with you?
You can't remember me?
I am...
Varanath.
I haven't seen you for almost 50 years.
Why haven't you changed at all?
```

**Anime Tanya S2 (full transcript):**
```
Damn it, who's that?!
There's a window here!
Maruko, I'm coming!
What's wrong?
Spice!
It's a trap!
I've already killed you with my group leader class!
It's better than a stormy wind.
It's a curse.
If you put in a lion demon, it's over.
```

Both transcripts are dialogue from the SOURCE video (horror trailer character "Varanath"; anime dubbed action dialog). Zero overlap with the GenLab-written hook or caption text on either reel.

## Verdict

**TTS is confirmed absent from GenLab reels.** The 4-tier TTS cascade (ElevenLabs → OpenAI → Edge-TTS → gTTS) runs on every render, produces an `_audio.mp3`, and the only downstream consumer is `render_whisper_captions.py:130` for WhisperX word-level caption timing. `_audio.mp3` is never muxed into the final reel.

`audio_replacer`'s `[0:a]` is the source video's original audio, `[1:a]` is the music bed (when a music bed applies). The graph is a 2-input `amix`, not 3. No TTS input is wired.

## Consequences

Confirmed per §3:

1. **Phase 3 premise was wrong twice.** Not only "no music bed exists" (fixed by F0 discovery of 125-track library + live `transformation_orchestrator.music_mood`), but also "the pipeline emits TTS-only audio." Every reel = borrowed video + borrowed audio + optional music bed + captions of unspoken text.
2. **ElevenLabs credential blocker (H1) is not a quality blocker.** It affects WhisperX word-timing accuracy only. Reels with edge-tts fallback sound identical to reels with ElevenLabs because neither TTS gets to the ear.
3. **OpenAI credit exhaustion is likewise timing-only.** Edge-tts and gTTS fallbacks have no aural consequence.
4. **F3a-2 audio-mix rebalancing is the ONLY audio-quality lever.** The `source_audio_duck_db` value directly sets what the audience hears; there is no VO to blend with. This is why §1's watch is manual and why V2 (per-niche source_audio reassessment) matters — the source track IS the entire aural signal.

## Recommendation for CLAUDE.md

This is a product-definition fact. Add a section to `CLAUDE.md` §STRICT VIDEO REQUIREMENTS or a new §AUDIO ARCHITECTURE explaining:

> **No spoken narration.** Every reel's audio track is the source video's original audio (trailer dialog, gameplay SFX, anime dialog, sports commentary, tech creator voice) mixed with an optional royalty-free music bed. The `_audio.mp3` produced by the TTS cascade (ElevenLabs → OpenAI → Edge-TTS → gTTS) is consumed only by WhisperX for word-level caption timing. It is never muxed into the reel. Adding narration requires converting `audio_replacer`'s 2-input `amix` filter graph to a 3-input graph with a new mix-ratio (source, music bed, TTS) — this is a separate work item, not a bug.

Not touching CLAUDE.md in this pass per V1's measure-only scope. Recommendation filed.

## What NOT to do

Do **not** add a TTS mux in this pass. If narration is wanted, the mux is its own work item with its own mix-ratio design question.

## Artifacts

- `.audit/QB-FIX-02/v1_tts_test/movies_INHERIT.mp4` (reel used for test)
- `.audit/QB-FIX-02/v1_tts_test/anime_TANYA.mp4` (reel used for test)
- `.audit/QB-FIX-02/v1_tts_test/movies_INHERIT.txt` (whisper transcript)
- `.audit/QB-FIX-02/v1_tts_test/anime_TANYA.txt` (whisper transcript)
