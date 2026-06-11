# Audio Policy — Video-First Source Audio

**Decision recorded:** 2026-06-11 as the R-39 (c) closure choice.

## TL;DR

GenLab uses **the source video's audio**. The render path never
synthesizes audio. The TTS cascade exists for the audio stage
(narration generation) and the captions stage (whisper alignment) —
not for the render path.

If a source video has no audio track at all, validate-time autofix
muxes in a **silent `anullsrc` bed**, not TTS narration. Silent
bed > synthesized voice because:

* The viewer expects the source's actual audio (game commentary, news
  voice, anime dialogue). TTS over a silent clip would sound off-brand
  and AI-generated.
* TTS-on-silence requires per-clip script generation, timing alignment
  to visual beats, and pronunciation tuning — all multi-stage work
  with quality risk.
* Silent muxing satisfies the platform requirement (audio stream must
  exist, regardless of content) at zero quality risk.

## What the render path does

```
1. Take the source video's audio as-is.
2. If the source has NO audio stream → validate-time autofix mixes
   in a silent stereo @ 48kHz bed (R-39 b).
3. Re-encode to platform specs.
4. No render-time TTS, no source-audio replacement.
```

The relevant code: `genlab-core/src/genlab_core/pipeline/stages/validate_videos.py`
— see `_fix()` for the autofix that adds silence to no-audio sources
(landed via R-39 a+b in PR #138).

## What TTS is actually for

The `genlab_core.tts` module is used by **two specific stages, not by
render**:

| Stage | Module | Purpose |
|---|---|---|
| `generate_audio.py` | `genlab_core.tts.factory.build_tts_cascade` | Niche-specific narration audio for stages that explicitly opt in |
| `render_whisper_captions.py` | `genlab_core.tts.factory.build_tts_cascade` | Whisper word-timing alignment for caption synchronization |

Neither stage feeds audio into the **render** path. The render path
consumes only source-video audio + silent fallback.

## Why this matters for the R-39 audit

The audit row (R-39 LOW) flagged three render-time spec gaps:

* (a) no max-duration trim → fixed in PR #138
* (b) silent/no-audio sources render then dropped → fixed in PR #138
* **(c) "the TTS cascade is never wired into the render path (the
   documented ElevenLabs→gTTS audio guarantee is doc-only)"**

The audit's recommendation for (c) was a deliberate OR:

> trim at render; **wire TTS or document video-first source-audio policy**

This doc is the second half of that OR. The choice to use **video-first
source audio + silent fallback** instead of TTS narration in the render
path is intentional, not an oversight. The TTS cascade is correctly
scoped to stages where text-to-speech is the actual goal.

## Implementation notes for future contributors

* **Adding a new render-stage feature?** Don't reach for TTS unless
  the feature explicitly needs synthesized speech. The render path's
  contract is: source audio in, source audio out, silent bed on
  empty.
* **Hitting a "no audio" failure?** Don't bypass — validate-time
  autofix handles it. If the autofix isn't firing, fix the autofix
  (see PR #138 for the pattern), don't add TTS to render.
* **Need TTS for a non-render context** (narration audio bed for a
  text-overlay-only reel, accessibility captions, etc.)? Use the
  audio stage (`generate_audio.py`) — that's exactly what it's for.
* The "documented ElevenLabs→gTTS audio guarantee" the audit
  referenced in CLAUDE.md is **specifically scoped to the audio
  stage**, not to render. If a future CLAUDE.md edit makes that
  scoping unclear, point it back at this policy doc.
