"""genlab_core.tts — Text-to-speech cascade with multi-provider fallback.

Used by two specific stages — *not* by the render path:

* ``pipeline.stages.generate_audio`` — niche-specific narration audio
  for stages that explicitly opt in.
* ``pipeline.stages.render_whisper_captions`` — Whisper word-timing
  alignment for caption synchronization.

**Render path: video-first source audio.** The render pipeline uses
the *source video's* audio. If a source has no audio stream at all,
validate-time autofix muxes in a silent ``anullsrc`` stereo bed at
48 kHz (R-39 b in PR #138) — NOT TTS narration. Synthesizing voice
over a silent clip would sound AI-generated and off-brand; the
silent-mux satisfies the platform "audio stream must exist"
requirement at zero quality risk.

This was the R-39 (c) closure choice (audit allowed "wire TTS OR
document video-first source-audio policy"). See
``docs/audio-policy.md`` for the full architecture rationale and
implementation notes.

Cascade order (when TTS *is* called from one of the two stages
above): ElevenLabs → OpenAI TTS → Edge-TTS → gTTS.
"""

from genlab_core.interfaces.tts import TTSProvider, TTSResult
from genlab_core.tts.cascade import CircuitBreaker, TTSCascade

__all__ = ["TTSCascade", "TTSResult", "TTSProvider", "CircuitBreaker"]
