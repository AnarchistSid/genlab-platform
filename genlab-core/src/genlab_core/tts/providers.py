"""Built-in TTS provider implementations.

Each provider wraps one TTS SDK and implements the TTSProvider protocol.
All SDKs are lazy-imported — a missing package means available=False,
not an import error.

Providers:
    InfshTTS       — inference.sh Inworld TTS-2 (~$0.014/reel, emotion steering)
    ElevenLabsTTS  — ElevenLabs API ($0.30/1K chars, highest quality)
    OpenAITTS      — OpenAI TTS API ($0.015/1K chars, good quality)
    EdgeTTS        — Microsoft Edge TTS (free, neural voices)
    GoogleTTS      — Google Translate TTS (free, lowest quality)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from genlab_core.interfaces.tts import TTSResult

logger = logging.getLogger(__name__)

# Cost constants
_ELEVENLABS_COST_PER_CHAR = 0.30 / 1000  # $0.30 per 1K chars
_OPENAI_TTS_COST_PER_CHAR = 0.015 / 1000  # $15 per 1M chars
_INFSH_INWORLD_COST_PER_CHAR = 35.0 / 1_000_000  # $35/M chars = $0.000035/char


def _probe_duration(path: Path) -> float:
    """Probe audio duration via ffprobe. Returns 0.0 on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
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
        api_key: str | None = None,
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
                success=False,
                provider=self.name,
                error="ElevenLabs not available (missing SDK or API key)",
            )

        start = time.time()
        try:
            from elevenlabs import VoiceSettings, save
            from elevenlabs.client import ElevenLabs

            # 2026-06-22 SDK migration: the ``elevenlabs`` client's old
            # top-level ``.generate(...)`` was removed; the new API is
            # ``client.text_to_speech.convert(voice_id=..., model_id=...,
            # text=..., voice_settings=..., output_format=...)``. Param
            # names renamed: ``voice`` → ``voice_id``, ``model`` →
            # ``model_id``. Returns an ``Iterator[bytes]`` which ``save``
            # still accepts unchanged. Until this migration, every
            # pipeline silently fell through to the OpenAI/Edge-TTS/gTTS
            # fallback cascade — operators never heard ElevenLabs-quality
            # audio because the SDK call AttributeError'd before reaching
            # the API.
            client = ElevenLabs(api_key=self._api_key)
            audio = client.text_to_speech.convert(
                voice_id=self.voice_id,
                text=text,
                voice_settings=VoiceSettings(
                    stability=self.stability,
                    similarity_boost=self.similarity_boost,
                    style=self.style,
                    use_speaker_boost=True,
                ),
                model_id=self.model,
                output_format=self.output_format,
            )
            save(audio, str(output_path))

        except Exception as exc:
            elapsed = time.time() - start
            logger.error("ElevenLabs API error (%.1fs): %s", elapsed, exc)
            return TTSResult(
                success=False,
                provider=self.name,
                error=f"ElevenLabs API error: {exc}",
                elapsed=round(elapsed, 2),
            )

        elapsed = time.time() - start
        if not output_path.exists() or output_path.stat().st_size == 0:
            return TTSResult(
                success=False,
                provider=self.name,
                error="ElevenLabs returned empty audio",
                elapsed=round(elapsed, 2),
            )

        duration = _probe_duration(output_path)
        cost = self.estimate_cost(text)

        logger.info(
            "ElevenLabs: %.1fs audio, $%.4f (%d chars, %.1fs elapsed)",
            duration,
            cost,
            len(text),
            elapsed,
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
        api_key: str | None = None,
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
                success=False,
                provider=self.name,
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
                success=False,
                provider=self.name,
                error=f"OpenAI TTS API error: {exc}",
                elapsed=round(elapsed, 2),
            )

        elapsed = time.time() - start
        if not output_path.exists() or output_path.stat().st_size == 0:
            return TTSResult(
                success=False,
                provider=self.name,
                error="OpenAI TTS returned empty audio",
                elapsed=round(elapsed, 2),
            )

        duration = _probe_duration(output_path)
        cost = self.estimate_cost(text)

        logger.info(
            "OpenAI TTS: %.1fs audio, $%.4f (voice=%s, model=%s, %.1fs elapsed)",
            duration,
            cost,
            self.voice,
            self.model,
            elapsed,
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
                success=False,
                provider=self.name,
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
                success=False,
                provider=self.name,
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
                success=False,
                provider=self.name,
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
                success=False,
                provider=self.name,
                error=f"gTTS error: {exc}",
                elapsed=round(elapsed, 2),
            )

        elapsed = time.time() - start
        if not output_path.exists() or output_path.stat().st_size == 0:
            return TTSResult(
                success=False,
                provider=self.name,
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


class InfshTTS:
    """inference.sh Inworld TTS-2 provider — task #200 (2026-08-18).

    Uses ``belt app run inworld/text-to-speech-2`` via the shared
    ``genlab_core.integrations.belt_client`` subprocess wrapper.
    Inworld TTS-2 supports emotion steering via inline ``[brackets]``
    (``[excited]``, ``[laugh]``, ``[pause]``) and 100+ languages.

    Positioned tier-1 in ``factory.build_tts_cascade`` when the
    canary flag ``GENLAB_INFSH_TTS_ENABLED`` is set, so it displaces
    ElevenLabs as the default when belt is authed. Falls through to
    the standard cascade on any failure.

    Cost: ~$35/M chars via Inworld TTS-2 (verified 2026-08-18
    ``belt app pricing inworld/text-to-speech-2``). A typical 400-
    char reel script → $0.014.
    """

    def __init__(
        self,
        voice_id: str = "Ashley",
        speaking_rate: float = 1.0,
        delivery_mode: str = "BALANCED",
    ) -> None:
        self.voice_id = voice_id
        self.speaking_rate = speaking_rate
        self.delivery_mode = delivery_mode

    @property
    def name(self) -> str:
        return "infsh_inworld"

    @property
    def available(self) -> bool:
        """True when the canary flag is on AND belt is on PATH.
        Auth state is checked lazily inside synthesize."""
        if os.environ.get("GENLAB_INFSH_TTS_ENABLED", "").strip().lower() not in (
            "1", "true", "yes", "on",
        ):
            return False
        return shutil.which("belt") is not None

    def estimate_cost(self, text: str) -> float:
        return len(text or "") * _INFSH_INWORLD_COST_PER_CHAR

    def synthesize(self, text: str, output_path: Path | str) -> TTSResult:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        start = time.time()

        from genlab_core.integrations.belt_client import run_app

        result = run_app(
            "inworld/text-to-speech-2",
            {
                "text": text,
                "voice_id": self.voice_id,
                "speaking_rate": self.speaking_rate,
                "delivery_mode": self.delivery_mode,
                "audio_encoding": "MP3",
                "sample_rate_hertz": 44100,
                "language": "en-US",
            },
            timeout_seconds=90,
        )
        elapsed = time.time() - start

        if not result.ok or not result.output:
            return TTSResult(
                success=False,
                provider=self.name,
                error=f"belt run failed: {result.error}",
                elapsed=round(elapsed, 2),
            )

        audio_url = (
            result.output.get("audio")
            or result.output.get("audio_output")
            or result.output.get("output")
        )
        if not audio_url or not isinstance(audio_url, str):
            return TTSResult(
                success=False,
                provider=self.name,
                error=(
                    f"no audio URL in output; keys={list(result.output.keys())}"
                ),
                elapsed=round(elapsed, 2),
            )

        try:
            req = urllib.request.Request(
                audio_url,
                headers={"User-Agent": "GenLab/1.0 infsh_tts"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp, \
                    open(output_path, "wb") as f:
                shutil.copyfileobj(resp, f)
        except Exception as exc:  # noqa: BLE001
            return TTSResult(
                success=False,
                provider=self.name,
                error=f"audio download failed: {exc}",
                elapsed=round(elapsed, 2),
            )

        if not output_path.exists() or output_path.stat().st_size == 0:
            return TTSResult(
                success=False,
                provider=self.name,
                error="downloaded file empty",
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
            cost_estimate=self.estimate_cost(text),
        )
