"""TTSProvider Protocol and TTSResult dataclass.

All TTS providers (ElevenLabs, OpenAI, Edge-TTS, gTTS) implement
TTSProvider so they can be used interchangeably by TTSCascade.

Usage:
    from genlab_core.interfaces.tts import TTSProvider, TTSResult

    class MyProvider:
        @property
        def name(self) -> str: return "my_tts"
        @property
        def available(self) -> bool: return True
        def synthesize(self, text, output_path) -> TTSResult: ...
        def estimate_cost(self, text) -> float: return 0.0
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class TTSResult:
    """Result of a TTS synthesis operation."""

    success: bool
    output_path: str = ""
    provider: str = ""
    duration: float = 0.0
    elapsed: float = 0.0
    file_size_mb: float = 0.0
    error: str = ""
    cost_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v or k == "success"}


@runtime_checkable
class TTSProvider(Protocol):
    """Protocol for TTS provider implementations.

    Each provider wraps one TTS engine (ElevenLabs, OpenAI, etc.)
    and handles SDK imports, API authentication, and synthesis.
    """

    @property
    def name(self) -> str:
        """Short identifier for this provider (e.g. 'elevenlabs', 'edge_tts')."""
        ...

    @property
    def available(self) -> bool:
        """Whether this provider can currently synthesize (SDK installed + credentials set)."""
        ...

    def synthesize(self, text: str, output_path: Path | str) -> TTSResult:
        """Synthesize text to an audio file.

        Args:
            text: Pre-cleaned text to synthesize.
            output_path: Where to write the audio file.

        Returns:
            TTSResult with success status, duration, cost, and metadata.
        """
        ...

    def estimate_cost(self, text: str) -> float:
        """Estimate cost in USD for synthesizing this text. 0.0 for free providers."""
        ...
