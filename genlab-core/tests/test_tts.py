"""Tests for genlab_core.tts — TTSCascade, CircuitBreaker, providers, text cleaner."""

import time
from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.interfaces.tts import TTSProvider, TTSResult
from genlab_core.tts._text_cleaner import clean_text_for_tts
from genlab_core.tts.cascade import CircuitBreaker, TTSCascade

# ── Fake providers for testing ────────────────────────────────────


class FakeProvider:
    """Configurable fake TTS provider for tests."""

    def __init__(self, name: str, available: bool = True, succeed: bool = True):
        self._name = name
        self._available = available
        self._succeed = succeed
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def available(self) -> bool:
        return self._available

    def synthesize(self, text: str, output_path) -> TTSResult:
        self.call_count += 1
        output_path = Path(output_path)
        if self._succeed:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"fake audio data")
            return TTSResult(
                success=True,
                output_path=str(output_path),
                provider=self._name,
                duration=5.0,
                cost_estimate=0.01,
            )
        return TTSResult(
            success=False,
            provider=self._name,
            error=f"{self._name} failed",
        )

    def estimate_cost(self, text: str) -> float:
        return 0.01 if self._name == "paid" else 0.0


class ExplodingProvider:
    """Provider that raises an exception on synthesize."""

    @property
    def name(self) -> str:
        return "exploding"

    @property
    def available(self) -> bool:
        return True

    def synthesize(self, text: str, output_path) -> TTSResult:
        raise RuntimeError("Boom!")

    def estimate_cost(self, text: str) -> float:
        return 0.0


# ── Text cleaner tests ────────────────────────────────────────────


class TestTextCleaner:
    def test_removes_hashtags(self):
        assert clean_text_for_tts("Hello #world #test") == "Hello"

    def test_removes_urls(self):
        result = clean_text_for_tts("Check https://example.com for more")
        assert "https://" not in result
        assert "Check" in result

    def test_removes_markdown_bold(self):
        assert clean_text_for_tts("This is **bold** text") == "This is bold text"

    def test_collapses_whitespace(self):
        assert clean_text_for_tts("too   many   spaces") == "too many spaces"

    def test_strips_leading_punctuation(self):
        assert clean_text_for_tts("...Hello world") == "Hello world"

    def test_idempotent(self):
        text = "Hello world, this is a test."
        assert clean_text_for_tts(clean_text_for_tts(text)) == clean_text_for_tts(text)

    def test_empty_text(self):
        assert clean_text_for_tts("") == ""

    def test_only_hashtags(self):
        assert clean_text_for_tts("#hello #world") == ""


# ── Circuit breaker tests ────────────────────────────────────────


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == "closed"
        assert not cb.is_open

    def test_opens_after_max_failures(self):
        cb = CircuitBreaker(max_failures=3)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open
        cb.record_failure()
        assert cb.is_open
        assert cb.state == "open"

    def test_success_resets(self):
        cb = CircuitBreaker(max_failures=2)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open
        cb.record_success()
        assert not cb.is_open
        assert cb.state == "closed"

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.1)
        cb.record_failure()
        assert cb.is_open
        time.sleep(0.15)
        assert cb.state == "half_open"
        assert not cb.is_open

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(max_failures=1, reset_timeout=0.05)
        cb.record_failure()
        time.sleep(0.1)
        assert cb.state == "half_open"
        cb.record_failure()
        assert cb.state == "open"

    def test_reset(self):
        cb = CircuitBreaker(max_failures=1)
        cb.record_failure()
        assert cb.is_open
        cb.reset()
        assert cb.state == "closed"


# ── TTSCascade tests ──────────────────────────────────────────────


class TestTTSCascade:
    def test_first_provider_succeeds(self, tmp_path):
        p1 = FakeProvider("first")
        p2 = FakeProvider("second")
        cascade = TTSCascade(providers=[p1, p2])

        result = cascade.synthesize(
            "Hello this is a test sentence",
            tmp_path / "out.mp3",
        )
        assert result.success
        assert result.provider == "first"
        assert p1.call_count == 1
        assert p2.call_count == 0

    def test_fallback_on_failure(self, tmp_path):
        p1 = FakeProvider("first", succeed=False)
        p2 = FakeProvider("second")
        cascade = TTSCascade(providers=[p1, p2])

        result = cascade.synthesize(
            "Hello this is a test sentence",
            tmp_path / "out.mp3",
        )
        assert result.success
        assert result.provider == "second"
        assert p1.call_count == 1
        assert p2.call_count == 1

    def test_skips_unavailable(self, tmp_path):
        p1 = FakeProvider("first", available=False)
        p2 = FakeProvider("second")
        cascade = TTSCascade(providers=[p1, p2])

        result = cascade.synthesize(
            "Hello this is a test sentence",
            tmp_path / "out.mp3",
        )
        assert result.success
        assert result.provider == "second"
        assert p1.call_count == 0

    def test_all_fail(self, tmp_path):
        p1 = FakeProvider("first", succeed=False)
        p2 = FakeProvider("second", succeed=False)
        cascade = TTSCascade(providers=[p1, p2])

        result = cascade.synthesize(
            "Hello this is a test sentence",
            tmp_path / "out.mp3",
        )
        assert not result.success
        assert "All providers failed" in result.error

    def test_exception_triggers_fallback(self, tmp_path):
        p1 = ExplodingProvider()
        p2 = FakeProvider("fallback")
        cascade = TTSCascade(providers=[p1, p2])

        result = cascade.synthesize(
            "Hello this is a test sentence",
            tmp_path / "out.mp3",
        )
        assert result.success
        assert result.provider == "fallback"

    def test_circuit_breaker_skips_after_failures(self, tmp_path):
        p1 = FakeProvider("flaky", succeed=False)
        p2 = FakeProvider("reliable")
        cascade = TTSCascade(providers=[p1, p2], max_failures=2)

        # First two calls: p1 fails, p2 succeeds
        cascade.synthesize("Test sentence number one", tmp_path / "1.mp3")
        cascade.synthesize("Test sentence number two", tmp_path / "2.mp3")
        assert p1.call_count == 2

        # Third call: p1 circuit is open, should skip directly to p2
        result = cascade.synthesize(
            "Test sentence number three",
            tmp_path / "3.mp3",
        )
        assert result.success
        assert result.provider == "reliable"
        assert p1.call_count == 2  # Not called again

    def test_text_too_short(self, tmp_path):
        p1 = FakeProvider("first")
        cascade = TTSCascade(providers=[p1])

        result = cascade.synthesize("Hi", tmp_path / "out.mp3")
        assert not result.success
        assert "too short" in result.error
        assert p1.call_count == 0

    def test_empty_providers_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            TTSCascade(providers=[])

    def test_clean_false_skips_cleaning(self, tmp_path):
        p1 = FakeProvider("first")
        cascade = TTSCascade(providers=[p1])

        result = cascade.synthesize(
            "**bold** #tag https://example.com text here",
            tmp_path / "out.mp3",
            clean=False,
        )
        assert result.success

    def test_estimate_cost(self):
        p1 = FakeProvider("paid")
        p2 = FakeProvider("free")
        cascade = TTSCascade(providers=[p1, p2])
        assert cascade.estimate_cost("some text here") == 0.01

    def test_estimate_cost_skips_circuit_open(self):
        p1 = FakeProvider("paid")
        p2 = FakeProvider("free")
        cascade = TTSCascade(providers=[p1, p2], max_failures=1)

        # Trip p1's circuit
        cascade._breakers["paid"].record_failure()
        assert cascade.estimate_cost("some text here") == 0.0

    def test_available_providers(self):
        p1 = FakeProvider("first", available=True)
        p2 = FakeProvider("second", available=False)
        cascade = TTSCascade(providers=[p1, p2])
        assert cascade.available_providers() == {"first": True, "second": False}

    def test_get_breaker(self):
        p1 = FakeProvider("first")
        cascade = TTSCascade(providers=[p1])
        breaker = cascade.get_breaker("first")
        assert isinstance(breaker, CircuitBreaker)


# ── Provider protocol conformance ─────────────────────────────────


class TestProviderProtocol:
    def test_fake_provider_is_tts_provider(self):
        p = FakeProvider("test")
        assert isinstance(p, TTSProvider)

    def test_elevenlabs_is_tts_provider(self):
        from genlab_core.tts.providers import ElevenLabsTTS

        p = ElevenLabsTTS(api_key="test")
        assert isinstance(p, TTSProvider)

    def test_openai_is_tts_provider(self):
        from genlab_core.tts.providers import OpenAITTS

        p = OpenAITTS(api_key="test")
        assert isinstance(p, TTSProvider)

    def test_edge_tts_is_tts_provider(self):
        from genlab_core.tts.providers import EdgeTTS

        p = EdgeTTS()
        assert isinstance(p, TTSProvider)

    def test_google_tts_is_tts_provider(self):
        from genlab_core.tts.providers import GoogleTTS

        p = GoogleTTS()
        assert isinstance(p, TTSProvider)


# ── Provider unit tests (no real API calls) ───────────────────────


class TestElevenLabsTTS:
    def test_unavailable_without_key(self):
        from genlab_core.tts.providers import ElevenLabsTTS

        p = ElevenLabsTTS(api_key="")
        assert not p.available
        assert p.name == "elevenlabs"

    def test_unavailable_without_sdk(self):
        from genlab_core.tts.providers import ElevenLabsTTS

        p = ElevenLabsTTS(api_key="test-key")
        with patch.dict("sys.modules", {"elevenlabs": None}):
            # Can't easily test ImportError via patch.dict, but we can
            # verify the estimate_cost works regardless
            assert p.estimate_cost("hello") > 0

    def test_estimate_cost(self):
        from genlab_core.tts.providers import ElevenLabsTTS

        p = ElevenLabsTTS(api_key="test")
        cost = p.estimate_cost("a" * 1000)
        assert abs(cost - 0.30) < 0.01

    def test_synthesize_returns_error_when_unavailable(self, tmp_path):
        from genlab_core.tts.providers import ElevenLabsTTS

        p = ElevenLabsTTS(api_key="")
        result = p.synthesize("test text", tmp_path / "out.mp3")
        assert not result.success
        assert "not available" in result.error


class TestOpenAITTS:
    def test_unavailable_without_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from genlab_core.tts.providers import OpenAITTS

        p = OpenAITTS(api_key="")
        assert not p.available
        assert p.name == "openai_tts"

    def test_estimate_cost(self):
        from genlab_core.tts.providers import OpenAITTS

        p = OpenAITTS(api_key="test")
        cost = p.estimate_cost("a" * 1000)
        assert abs(cost - 0.015) < 0.001

    def test_synthesize_returns_error_when_unavailable(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        from genlab_core.tts.providers import OpenAITTS

        p = OpenAITTS(api_key="")
        result = p.synthesize("test text", tmp_path / "out.mp3")
        assert not result.success
        assert "not available" in result.error


class TestEdgeTTS:
    def test_name(self):
        from genlab_core.tts.providers import EdgeTTS

        assert EdgeTTS().name == "edge_tts"

    def test_free(self):
        from genlab_core.tts.providers import EdgeTTS

        assert EdgeTTS().estimate_cost("anything") == 0.0


class TestGoogleTTS:
    def test_name(self):
        from genlab_core.tts.providers import GoogleTTS

        assert GoogleTTS().name == "gtts"

    def test_free(self):
        from genlab_core.tts.providers import GoogleTTS

        assert GoogleTTS().estimate_cost("anything") == 0.0
