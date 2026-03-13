"""Built-in TTS provider implementations.

Each provider wraps one TTS SDK and implements the TTSProvider protocol.
All SDKs are lazy-imported — a missing package means available=False,
not an import error.

Providers:
    ElevenLabsTTS  — ElevenLabs API ($0.30/1K chars, highest quality)
    OpenAITTS      — OpenAI TTS API ($0.015/1K chars, good quality)
    EdgeTTS        — Microsoft Edge TTS (free, neural voices)
    GoogleTTS      — Google Translate TTS (free, lowest quality)
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from genlab_core.interfaces.tts import TTSResult

logger = logging.getLogger(__name__)

# Cost constants
_ELEVENLABS_COST_PER_CHAR = 0.30 / 1000  # $0.30 per 1K chars
_OPENAI_TTS_COST_PER_CHAR = 0.015 / 1000  # $15 per 1M chars


def _probe_duration(path: Path) -> float:
    """Probe audio duration via ffprobe. Returns 0.0 on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def _file_size_mb(path: Path) -> float:
    """Get file size in MB."""
    try:
        return round(path.stat().st_size / (1024 * 1024), 3)
    except Exception:
        return 0.0


class ElevenLabsTTS:
    """ElevenLabs TTS provider.

    Args:
        api_key:          API key (default: ELEVENLABS_API_KEY env var).
        voice_id:         Voice ID (default: Antoni).
        model:            Model ID (default: eleven_multilingual_v2).
        stability:        Voice stability 0.0-1.0 (default: 0.85).
        similarity_boost: Voice similarity 0.0-1.0 (default: 0.75).
        style:            Style exaggeration 0.0-1.0 (default: 0.0).
        output_format:    Audio format string (default: mp3_44100_192).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: str = "ErXwobaYiN019PkySvjV",
        model: str = "eleven_multilingual_v2",
        stability: float = 0.85,
        similarity_boost: float = 0.75,
        style: float = 0.0,
        output_format: str = "mp3_44100_192",
    ) -> None:
        self._api_key = (api_key or os.getenv("ELEVENLABS_API_KEY", "")).strip()
        self.voice_id = voice_id
        self.model = model
        self.stability = stability
        self.similarity_boost = similarity_boost
        self.style = style
        self.output_format = output_format

    @property
    def name(self) -> str:
        return "elevenlabs"

    @property
    def available(self) -> bool:
        if not self._api_key:
            return False
        try:
            import elevenlabs  # noqa: F401
            return True
        except ImportError:
            return False

    def estimate_cost(self, text: str) -> float:
        return round(len(text) * _ELEVENLABS_COST_PER_CHAR, 4)

    def synthesize(self, text: str, output_path: Path | str) -> TTSResult:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.available:
            return TTSResult(
                success=False, provider=self.name,
                error="ElevenLabs not available (missing SDK or API key)",
            )

        start = time.time()
        try:
            from elevenlabs.client import ElevenLabs
            from elevenlabs import VoiceSettings, save

            client = ElevenLabs(api_key=self._api_key)
            audio = client.generate(
                text=text,
                voice=self.voice_id,
                voice_settings=VoiceSettings(
                    stability=self.stability,
                    similarity_boost=self.similarity_boost,
                    style=self.style,
                    use_speaker_boost=True,
                ),
                model=self.model,
                output_format=self.output_format,
            )
            save(audio, str(output_path))

        except Exception as exc:
            elapsed = time.time() - start
            logger.error("ElevenLabs API error (%.1fs): %s", elapsed, exc)
            return TTSResult(
                success=False, provider=self.name,
                error=f"ElevenLabs API error: {exc}",
                elapsed=round(elapsed, 2),
            )

        elapsed = time.time() - start
        if not output_path.exists() or output_path.stat().st_size == 0:
            return TTSResult(
                success=False, provider=self.name,
                error="ElevenLabs returned empty audio",
                elapsed=round(elapsed, 2),
            )

        duration = _probe_duration(output_path)
        cost = self.estimate_cost(text)

        logger.info(
            "ElevenLabs: %.1fs audio, $%.4f (%d chars, %.1fs elapsed)",
            duration, cost, len(text), elapsed,
        )

        return TTSResult(
            success=True,
            output_path=str(output_path),
            provider=self.name,
            duration=duration,
            elapsed=round(elapsed, 2),
            file_size_mb=_file_size_mb(output_path),
            cost_estimate=cost,
        )


class OpenAITTS:
    """OpenAI TTS provider.

    Args:
        api_key: API key (default: OPENAI_API_KEY env var).
        voice:   Voice name (alloy, echo, fable, onyx, nova, shimmer).
        model:   Model ID ("tts-1" or "tts-1-hd").
        speed:   Speech speed 0.25-4.0 (1.0 = natural).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        voice: str = "onyx",
        model: str = "tts-1-hd",
        speed: float = 1.0,
    ) -> None:
        self._api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.voice = voice
        self.model = model
        self.speed = speed

    @property
    def name(self) -> str:
        return "openai_tts"

    @property
    def available(self) -> bool:
        if not self._api_key:
            return False
        try:
            import openai  # noqa: F401
            return True
        except ImportError:
            return False

    def estimate_cost(self, text: str) -> float:
        return round(len(text) * _OPENAI_TTS_COST_PER_CHAR, 4)

    def synthesize(self, text: str, output_path: Path | str) -> TTSResult:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.available:
            return TTSResult(
                success=False, provider=self.name,
                error="OpenAI TTS not available (missing SDK or API key)",
            )

        start = time.time()
        try:
            from openai import OpenAI as OpenAIClient

            client = OpenAIClient(api_key=self._api_key)
            response = client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=text,
                speed=self.speed,
                response_format="mp3",
            )
            response.stream_to_file(str(output_path))

        except Exception as exc:
            elapsed = time.time() - start
            logger.error("OpenAI TTS API error (%.1fs): %s", elapsed, exc)
            return TTSResult(
                success=False, provider=self.name,
                error=f"OpenAI TTS API error: {exc}",
                elapsed=round(elapsed, 2),
            )

        elapsed = time.time() - start
        if not output_path.exists() or output_path.stat().st_size == 0:
            return TTSResult(
                success=False, provider=self.name,
                error="OpenAI TTS returned empty audio",
                elapsed=round(elapsed, 2),
            )

        duration = _probe_duration(output_path)
        cost = self.estimate_cost(text)

        logger.info(
            "OpenAI TTS: %.1fs audio, $%.4f (voice=%s, model=%s, %.1fs elapsed)",
            duration, cost, self.voice, self.model, elapsed,
        )

        return TTSResult(
            success=True,
            output_path=str(output_path),
            provider=self.name,
            duration=duration,
            elapsed=round(elapsed, 2),
            file_size_mb=_file_size_mb(output_path),
            cost_estimate=cost,
        )


class EdgeTTS:
    """Microsoft Edge TTS provider (free, neural voices via WebSocket).

    Args:
        voice: SSML voice name (default: en-US-AndrewNeural).
        rate:  Speech rate adjustment (default: "+5%").
        pitch: Pitch adjustment (default: "-2Hz").
    """

    def __init__(
        self,
        voice: str = "en-US-AndrewNeural",
        rate: str = "+5%",
        pitch: str = "-2Hz",
    ) -> None:
        self.voice = voice
        self.rate = rate
        self.pitch = pitch

    @property
    def name(self) -> str:
        return "edge_tts"

    @property
    def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    def estimate_cost(self, text: str) -> float:
        return 0.0

    def synthesize(self, text: str, output_path: Path | str) -> TTSResult:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.available:
            return TTSResult(
                success=False, provider=self.name,
                error="edge-tts package not installed",
            )

        start = time.time()

        import edge_tts as _edge_tts

        async def _generate():
            communicate = _edge_tts.Communicate(
                text=text,
                voice=self.voice,
                rate=self.rate,
                pitch=self.pitch,
            )
            await communicate.save(str(output_path))

        try:
            asyncio.run(_generate())
        except RuntimeError:
            # Already in an async context — fall back to thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(lambda: asyncio.run(_generate())).result(timeout=30)

        elapsed = time.time() - start
        if not output_path.exists() or output_path.stat().st_size == 0:
            return TTSResult(
                success=False, provider=self.name,
                error="Edge-TTS produced empty output",
                elapsed=round(elapsed, 2),
            )

        duration = _probe_duration(output_path)

        return TTSResult(
            success=True,
            output_path=str(output_path),
            provider=self.name,
            duration=duration,
            elapsed=round(elapsed, 2),
            file_size_mb=_file_size_mb(output_path),
            cost_estimate=0.0,
        )


class GoogleTTS:
    """Google Translate TTS provider (free, lowest quality).

    Args:
        lang: Language code (default: "en").
        tld:  Top-level domain for accent (default: "com").
    """

    def __init__(self, lang: str = "en", tld: str = "com") -> None:
        self.lang = lang
        self.tld = tld

    @property
    def name(self) -> str:
        return "gtts"

    @property
    def available(self) -> bool:
        try:
            import gtts  # noqa: F401
            return True
        except ImportError:
            return False

    def estimate_cost(self, text: str) -> float:
        return 0.0

    def synthesize(self, text: str, output_path: Path | str) -> TTSResult:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.available:
            return TTSResult(
                success=False, provider=self.name,
                error="gtts package not installed",
            )

        start = time.time()
        try:
            from gtts import gTTS

            tts = gTTS(text=text, lang=self.lang, tld=self.tld)
            tts.save(str(output_path))

        except Exception as exc:
            elapsed = time.time() - start
            logger.error("gTTS error (%.1fs): %s", elapsed, exc)
            return TTSResult(
                success=False, provider=self.name,
                error=f"gTTS error: {exc}",
                elapsed=round(elapsed, 2),
            )

        elapsed = time.time() - start
        if not output_path.exists() or output_path.stat().st_size == 0:
            return TTSResult(
                success=False, provider=self.name,
                error="gTTS produced empty output",
                elapsed=round(elapsed, 2),
            )

        duration = _probe_duration(output_path)

        return TTSResult(
            success=True,
            output_path=str(output_path),
            provider=self.name,
            duration=duration,
            elapsed=round(elapsed, 2),
            file_size_mb=_file_size_mb(output_path),
            cost_estimate=0.0,
        )
