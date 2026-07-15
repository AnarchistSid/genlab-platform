"""Factory function for building TTSCascade with available providers.

Usage:
    from genlab_core.tts.factory import build_tts_cascade
    cascade = build_tts_cascade()
    result = cascade.synthesize(text, output_path)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def build_tts_cascade():
    """Build TTSCascade with all available providers.

    Falls back gracefully: ElevenLabs → OpenAI → Edge-TTS → gTTS.
    Always includes at least Edge-TTS + gTTS (free, no API key).
    """
    from genlab_core.tts.cascade import TTSCascade
    from genlab_core.tts.providers import EdgeTTS, GoogleTTS

    providers = []

    # 2026-07-14 (media audit F9): elevated silent `pass` to WARNING.
    # Prior state: any construction exception (auth error, SDK
    # version incompat) silently dropped the provider — highest-
    # quality tier could disappear with zero log signal. Operator
    # saw ElevenLabs configured in .env but reels shipped with
    # gTTS-quality audio for weeks. Fixed: WARNING logs the reason.
    # ElevenLabs — highest quality, requires API key
    if os.environ.get("ELEVENLABS_API_KEY"):
        try:
            from genlab_core.tts.providers import ElevenLabsTTS

            providers.append(ElevenLabsTTS())
            logger.debug("TTS: ElevenLabs provider added")
        except Exception as exc:
            logger.warning("TTS: ElevenLabs provider FAILED to construct: %s", exc)

    # OpenAI TTS — good quality, requires API key
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from genlab_core.tts.providers import OpenAITTS

            providers.append(OpenAITTS())
            logger.debug("TTS: OpenAI provider added")
        except Exception as exc:
            logger.warning("TTS: OpenAI provider FAILED to construct: %s", exc)

    # Edge-TTS — free, neural voices (always available)
    try:
        providers.append(EdgeTTS())
        logger.debug("TTS: Edge-TTS provider added")
    except Exception as exc:
        logger.warning("TTS: Edge-TTS provider FAILED to construct: %s", exc)

    # gTTS — free, lowest quality fallback (always available)
    try:
        providers.append(GoogleTTS())
        logger.debug("TTS: gTTS provider added")
    except Exception as exc:
        logger.warning("TTS: gTTS provider FAILED to construct: %s", exc)

    if not providers:
        raise RuntimeError("No TTS providers available — install edge-tts or gTTS")

    return TTSCascade(providers=providers)
