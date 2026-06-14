"""Pin the GenerateAudio → TTSCascade.synthesize call shape.

The 2026-06-14 prod-log probe found this exception firing 31 times
in 24 hours, one per pipeline run × niche:

    TypeError: TTSCascade.synthesize() got an unexpected keyword
    argument 'voice'

generate_audio.py passed ``voice=voice`` to ``cascade.synthesize(...)``,
but the cascade's signature is ``synthesize(text, output_path,
clean=True)`` — no ``voice`` parameter. Same for every
TTSProvider.synthesize down the chain. Voices are configured
per-provider at construction (e.g. ``ELEVENLABS_VOICE_ID`` env var),
not per-call.

Result: every TTS attempt raised TypeError → silenced render path
skipped silence-trim + ffmpeg post-processing → pipeline degraded
to no-audio output.

This pin locks in the post-fix call shape so the regression can't
return. If the cascade is ever extended to support per-call voice
routing, the caller needs to be updated alongside it.
"""

from __future__ import annotations

import inspect

from genlab_core.tts.cascade import TTSCascade


def test_cascade_synthesize_does_not_accept_voice_kwarg():
    """If the cascade signature ever grows a ``voice`` parameter, the
    caller in generate_audio.py needs to be updated to pass it.
    Pin the current architecture so a future signature extension
    triggers a deliberate review of the caller path."""
    sig = inspect.signature(TTSCascade.synthesize)
    params = set(sig.parameters.keys())
    assert "voice" not in params, (
        "TTSCascade.synthesize now accepts 'voice' — extend the "
        "generate_audio.py caller to pass it (and pin the new "
        "behavior here)."
    )
    # And pin the parameters it DOES have, so a future rename doesn't
    # silently break the caller.
    assert {"text", "output_path", "clean"} <= params, (
        f"TTSCascade.synthesize signature drifted — got {sorted(params)}. "
        "generate_audio.py uses ``text=`` and ``output_path=`` kwargs; "
        "any rename here needs the caller updated."
    )


def test_generate_audio_does_not_pass_voice_kwarg():
    """Pin the post-fix shape of the call site. Re-introducing
    ``voice=voice`` would resurrect the prod TypeError we just
    closed."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "genlab_core"
        / "pipeline"
        / "stages"
        / "generate_audio.py"
    )
    src = source.read_text()
    # The call is multi-line — search for the cascade.synthesize call's
    # body. The negative assertion is "no voice= argument".
    # Constrain to a window around the cascade.synthesize call so we
    # don't false-match elsewhere.
    call_idx = src.find("cascade.synthesize(")
    assert call_idx != -1, "cascade.synthesize call site moved — update this test"
    # Look at the next ~300 chars (the call body + closing paren)
    window = src[call_idx : call_idx + 300]
    assert "voice=" not in window, (
        "generate_audio.py reintroduced ``voice=`` kwarg to "
        "cascade.synthesize. TTSCascade.synthesize doesn't accept "
        "it — every TTS call will raise TypeError as it did before "
        "the 2026-06-14 fix."
    )
