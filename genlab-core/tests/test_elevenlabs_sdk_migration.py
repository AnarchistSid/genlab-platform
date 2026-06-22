"""Pin: ElevenLabs TTS uses the new ``text_to_speech.convert(...)`` API.

The ``elevenlabs`` SDK removed its old top-level ``client.generate(...)``
method in favor of ``client.text_to_speech.convert(voice_id=...,
text=..., model_id=..., voice_settings=..., output_format=...)``. Pre-
fix, every pipeline call hit ``AttributeError: 'ElevenLabs' object has
no attribute 'generate'`` and the TTS cascade silently fell through to
OpenAI/Edge-TTS/gTTS. Operators got lower-quality audio on every reel.

These pins lock in:

  1. ElevenLabsTTS calls ``client.text_to_speech.convert`` (not the
     removed ``.generate``)
  2. Old param names (``voice``, ``model``) are NOT passed — would
     TypeError on the new API
  3. New param names (``voice_id``, ``model_id``) ARE passed
  4. AttributeError on missing ``.generate`` would crash today —
     regression canary that future SDK migrations don't silently
     re-introduce the bug
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Skip the whole file when the optional ``elevenlabs`` SDK isn't
# installed (e.g. on the test-core CI matrix which builds genlab-core
# without the [tts] extras). ``patch("elevenlabs.client.ElevenLabs")``
# would otherwise fail at the `getter()` step with ModuleNotFoundError
# before the test body even runs.
pytest.importorskip("elevenlabs")

from genlab_core.tts.providers import ElevenLabsTTS  # noqa: E402


def _provider(tmp_path):
    """Build a minimal ElevenLabsTTS with synthetic config."""
    return ElevenLabsTTS(
        api_key="fake_key",
        voice_id="voice_abc",
        model="eleven_turbo_v2",
        stability=0.5,
        similarity_boost=0.5,
        style=0.0,
        output_format="mp3_44100_128",
    )


class TestNewSDKAPIPath:
    def test_calls_text_to_speech_convert_not_generate(self, tmp_path):
        """Pin: the SDK migration's whole point — we MUST call
        ``client.text_to_speech.convert(...)`` and MUST NOT call the
        removed ``client.generate(...)``."""
        provider = _provider(tmp_path)
        output_path = tmp_path / "out.mp3"

        # Build a fake client where .text_to_speech.convert returns a
        # known iterator, and where .generate would AttributeError
        fake_client = MagicMock()
        # convert returns the new-API-style iterator of bytes
        fake_client.text_to_speech.convert.return_value = iter([b"audio_chunk"])
        # Make .generate explicitly raise to catch any regression
        # where someone re-introduces it as an alias
        fake_client.generate.side_effect = AttributeError(
            "regression: .generate should never be called"
        )

        with patch("elevenlabs.client.ElevenLabs", return_value=fake_client):
            # Patch save so it writes to disk for the size>0 success check
            with patch("elevenlabs.save") as mock_save:

                def fake_save(audio_iter, path):
                    # Realistically consume the iterator + write a small file
                    data = b"".join(audio_iter)
                    output_path.write_bytes(data)

                mock_save.side_effect = fake_save
                result = provider.synthesize("hello world", output_path)

        assert result.success, f"Expected success but got: {result.error}"
        # The pin: convert was called, generate was NOT
        fake_client.text_to_speech.convert.assert_called_once()
        fake_client.generate.assert_not_called()

    def test_passes_new_param_names(self, tmp_path):
        """Pin: convert is called with ``voice_id`` + ``model_id`` (new
        param names), NOT the old ``voice`` + ``model`` which would
        TypeError on the new SDK."""
        provider = _provider(tmp_path)
        output_path = tmp_path / "out.mp3"

        fake_client = MagicMock()
        fake_client.text_to_speech.convert.return_value = iter([b"audio"])

        with patch("elevenlabs.client.ElevenLabs", return_value=fake_client):
            with patch("elevenlabs.save") as mock_save:
                mock_save.side_effect = lambda a, p: output_path.write_bytes(b"x")
                provider.synthesize("hi", output_path)

        call_kwargs = fake_client.text_to_speech.convert.call_args.kwargs
        # New SDK parameter names present
        assert "voice_id" in call_kwargs
        assert "model_id" in call_kwargs
        assert call_kwargs["voice_id"] == "voice_abc"
        assert call_kwargs["model_id"] == "eleven_turbo_v2"
        # Old SDK parameter names ABSENT (would TypeError on new SDK)
        assert "voice" not in call_kwargs
        assert "model" not in call_kwargs


class TestRegressionGuard:
    def test_attribute_error_on_generate_is_the_pre_fix_failure_mode(self, tmp_path):
        """Pin: documents the EXACT pre-fix failure mode. If anyone
        ever reverts to ``client.generate(...)`` on the new SDK, this
        test would catch it because the mocked .generate raises
        AttributeError."""
        provider = _provider(tmp_path)
        fake_client = MagicMock()
        # The new SDK's behavior: .generate doesn't exist
        # MagicMock won't reproduce that by default (returns more MagicMocks),
        # so we manually configure .generate to raise AttributeError.
        del fake_client.generate  # forces AttributeError on access
        fake_client.text_to_speech.convert.return_value = iter([b"audio"])

        with patch("elevenlabs.client.ElevenLabs", return_value=fake_client):
            with patch("elevenlabs.save") as mock_save:
                mock_save.side_effect = lambda a, p: (tmp_path / "out.mp3").write_bytes(b"x")
                result = provider.synthesize("hi", tmp_path / "out.mp3")

        # Post-fix code MUST succeed because it now uses convert,
        # not generate. If this fails with AttributeError, someone
        # re-introduced the bug.
        assert result.success
