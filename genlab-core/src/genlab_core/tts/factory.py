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

    Falls back gracefully:
      InfshTTS (Inworld TTS-2) → ElevenLabs → OpenAI → Edge-TTS → gTTS

    **Documented order vs measured reality (2026-09-12).** FIX-T01 measured the
    real head as ``infsh_inworld`` on every niche, and the ElevenLabs tier had
    never been reached: ``character_count: 0``. Two independent reasons, both
    now handled rather than described —

      * the ``elevenlabs`` SDK is not installed on the production host, and this
        factory appended the tier WITHOUT consulting ``.available`` (the
        InfshTTS block above it does), so a tier that could not run was listed
        as though it could;
      * the configured key is on the free plan — 10,000 characters/month against
        roughly 60,000 of need.

    Both are now preconditions in ``ElevenLabsTTS.available``, so the cascade
    reports the depth it actually has. If the account is upgraded and the SDK
    installed, the tier re-enables itself with no code change.

    Always includes at least Edge-TTS + gTTS (free, no API key).
    """
    from genlab_core.tts.cascade import TTSCascade
    from genlab_core.tts.providers import EdgeTTS, GoogleTTS

    providers = []

    # 2026-08-18 (task #200): inference.sh Inworld TTS-2 as tier 1
    # when the canary flag GENLAB_INFSH_TTS_ENABLED is set. Positioned
    # first because Inworld TTS-2 provides emotion steering via inline
    # [brackets] which ElevenLabs+OpenAI don't — the key quality lever
    # for AI-news content. Falls through to the standard cascade on
    # any belt/network/auth failure. InfshTTS.available already
    # short-circuits on flag-off, so this block is a no-op when
    # the operator hasn't flipped the canary.
    try:
        from genlab_core.tts.providers import InfshTTS

        infsh_tier = InfshTTS()
        if infsh_tier.available:
            providers.append(infsh_tier)
            logger.debug("TTS: inference.sh Inworld TTS-2 provider added (tier 1)")
    except Exception as exc:
        logger.warning("TTS: InfshTTS provider FAILED to construct: %s", exc)

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

            # 2026-09-12: CONSULT `.available` — this block appended the tier
            # unconditionally while the InfshTTS block directly above it gated
            # on `.available`. The inconsistency is why a tier with no SDK
            # installed and a free-plan key sat in the cascade for months,
            # failing at synthesis and making the cascade misreport its own
            # depth. `.available` now also checks plan headroom.
            el_tier = ElevenLabsTTS()
            if el_tier.available:
                providers.append(el_tier)
                logger.debug("TTS: ElevenLabs provider added")
            else:
                logger.info(
                    "TTS: ElevenLabs provider SKIPPED — key present but tier "
                    "cannot serve (see the [tts] line above for the reason)"
                )
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
