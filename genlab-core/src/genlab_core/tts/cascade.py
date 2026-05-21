"""TTSCascade — multi-provider TTS with fallback and circuit breakers.

Tries providers in order. If a provider fails, the next one is tried.
After repeated failures, a provider's circuit breaker opens and it is
skipped entirely until the reset timeout elapses.

Usage:
    from genlab_core.tts import TTSCascade
    from genlab_core.tts.providers import ElevenLabsTTS, OpenAITTS, EdgeTTS, GoogleTTS

    cascade = TTSCascade(providers=[
        ElevenLabsTTS(api_key="..."),
        OpenAITTS(api_key="..."),
        EdgeTTS(),
        GoogleTTS(),
    ])
    result = cascade.synthesize("Hello world", "output.mp3")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from genlab_core.interfaces.tts import TTSProvider, TTSResult
from genlab_core.tts._text_cleaner import clean_text_for_tts

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreaker:
    """Per-provider circuit breaker.

    States:
        closed    — normal operation, requests flow through
        open      — provider is failing, requests are rejected immediately
        half_open — after reset_timeout, one request is allowed through to test recovery

    Transitions:
        closed → open:      after max_failures consecutive failures
        open → half_open:   after reset_timeout seconds elapse
        half_open → closed: on success
        half_open → open:   on failure
    """

    max_failures: int = 3
    reset_timeout: float = 60.0
    _failure_count: int = field(default=0, init=False, repr=False)
    _last_failure_time: float = field(default=0.0, init=False, repr=False)
    _state: str = field(default="closed", init=False, repr=False)

    @property
    def state(self) -> str:
        """Current state: 'closed', 'open', or 'half_open'."""
        if self._state == "open":
            if time.monotonic() - self._last_failure_time >= self.reset_timeout:
                self._state = "half_open"
        return self._state

    @property
    def is_open(self) -> bool:
        """True if the circuit is open (provider should be skipped)."""
        return self.state == "open"

    def record_success(self) -> None:
        """Record a successful call — resets failure count, closes circuit."""
        self._failure_count = 0
        self._state = "closed"

    def record_failure(self) -> None:
        """Record a failed call — may trip the circuit open."""
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.max_failures:
            self._state = "open"

    def reset(self) -> None:
        """Manually reset to closed state."""
        self._failure_count = 0
        self._state = "closed"


class TTSCascade:
    """TTS with ordered fallback chain and per-provider circuit breakers.

    Args:
        providers:     Ordered list of TTSProvider implementations. First
                       available provider wins; failed providers fall through.
        max_failures:  Failures before a provider's circuit breaker opens.
        reset_timeout: Seconds before an open circuit allows a test request.
    """

    def __init__(
        self,
        providers: list[TTSProvider],
        max_failures: int = 3,
        reset_timeout: float = 60.0,
    ) -> None:
        if not providers:
            raise ValueError("TTSCascade requires at least one provider")
        self._providers = list(providers)
        self._breakers: dict[str, CircuitBreaker] = {
            p.name: CircuitBreaker(
                max_failures=max_failures,
                reset_timeout=reset_timeout,
            )
            for p in providers
        }

    def synthesize(
        self,
        text: str,
        output_path: Path | str,
        clean: bool = True,
    ) -> TTSResult:
        """Synthesize text to audio, trying providers in order.

        Args:
            text:        Text to synthesize.
            output_path: Where to write the audio file.
            clean:       Whether to clean text before synthesis (default True).

        Returns:
            TTSResult from the first successful provider, or a failure result
            listing all providers that were tried.
        """
        if clean:
            text = clean_text_for_tts(text)

        if not text or len(text) < 5:
            return TTSResult(success=False, error="Text too short after cleaning")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        tried: list[str] = []

        for provider in self._providers:
            breaker = self._breakers[provider.name]

            if breaker.is_open:
                tried.append(f"{provider.name}(circuit-open)")
                logger.debug(
                    "TTSCascade: skipping %s (circuit open, %d failures)",
                    provider.name,
                    breaker._failure_count,
                )
                continue

            if not provider.available:
                tried.append(f"{provider.name}(unavailable)")
                continue

            logger.debug(
                "TTSCascade: trying %s (%d/%d)",
                provider.name,
                len(tried) + 1,
                len(self._providers),
            )

            try:
                result = provider.synthesize(text, output_path)
                if result.success:
                    breaker.record_success()
                    if tried:
                        logger.info(
                            "TTSCascade: %s succeeded (after %s failed: %s)",
                            provider.name,
                            len(tried),
                            ", ".join(tried),
                        )
                    return result

                logger.warning(
                    "TTSCascade: %s failed: %s",
                    provider.name,
                    result.error,
                )
                tried.append(provider.name)
                breaker.record_failure()

            except Exception as exc:
                logger.warning(
                    "TTSCascade: %s exception: %s",
                    provider.name,
                    exc,
                )
                tried.append(provider.name)
                breaker.record_failure()

        return TTSResult(
            success=False,
            error=f"All providers failed. Tried: {', '.join(tried)}",
        )

    def estimate_cost(self, text: str) -> float:
        """Estimate cost from the first available provider in the chain."""
        text = clean_text_for_tts(text)
        for provider in self._providers:
            breaker = self._breakers[provider.name]
            if not breaker.is_open and provider.available:
                return provider.estimate_cost(text)
        return 0.0

    def available_providers(self) -> dict[str, bool]:
        """Report which providers are currently available."""
        return {p.name: p.available for p in self._providers}

    def get_breaker(self, provider_name: str) -> CircuitBreaker:
        """Get the circuit breaker for a specific provider."""
        return self._breakers[provider_name]
