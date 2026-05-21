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

    # ElevenLabs — highest quality, requires API key
    if os.environ.get("ELEVENLABS_API_KEY"):
        try:
            from genlab_core.tts.providers import ElevenLabsTTS

            providers.append(ElevenLabsTTS())
            logger.debug("TTS: ElevenLabs provider added")
        except Exception:
            pass

    # OpenAI TTS — good quality, requires API key
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from genlab_core.tts.providers import OpenAITTS

            providers.append(OpenAITTS())
            logger.debug("TTS: OpenAI provider added")
        except Exception:
            pass

    # Edge-TTS — free, neural voices (always available)
    try:
        providers.append(EdgeTTS())
        logger.debug("TTS: Edge-TTS provider added")
    except Exception:
        pass

    # gTTS — free, lowest quality fallback (always available)
    try:
        providers.append(GoogleTTS())
        logger.debug("TTS: gTTS provider added")
    except Exception:
        pass

    if not providers:
        raise RuntimeError("No TTS providers available — install edge-tts or gTTS")

    return TTSCascade(providers=providers)
