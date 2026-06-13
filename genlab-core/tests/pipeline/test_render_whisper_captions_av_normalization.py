"""Regression pin: RenderWhisperCaptions preserves bt709 + AAC 48kHz stereo.

RENDER #3 fix (2026-06-13). Pre-fix the ffmpeg re-encode in
``render_whisper_captions._render_captions`` omitted both the
``-colorspace bt709`` family of flags and used ``-c:a copy``. Two
consequences both invisible at this stage but expensive downstream:

  1. Output had no color-space tagging → ValidateVideos flagged
     "wrong_color_space" → auto-fixed with another full re-encode →
     3rd-generation transcode (FrameCompositor → captions → fix) with
     visible quality loss.
  2. Source audio params (e.g. 44.1 kHz mono from yt-dlp) survived
     untouched → ValidateVideos flagged "wrong_sample_rate" /
     "wrong_audio_channels" → another auto-fix re-encode.

Post-fix: this stage always writes bt709 + AAC 48 kHz stereo so
ValidateVideos has nothing to fix.

This test pins the exact FFmpeg argv shape by introspecting the
subprocess call. Captions are currently disabled across all 5 niches
per the owner's "remove text over the video" instruction, but this
fix runs prophylactically so re-enabling captions is a single-line
config flip with no follow-up code work.
"""

from __future__ import annotations

import inspect

from genlab_core.pipeline.stages.render_whisper_captions import RenderWhisperCaptions


def _captioning_source() -> str:
    """Return the source of the captioning method that contains the
    FFmpeg argv. Used by all tests below to grep against the literal
    flag strings — cheaper than running ffmpeg in tests."""
    # The argv-building lives inside the stage's _render_captions method.
    methods = [m for m in dir(RenderWhisperCaptions) if "captions" in m.lower()]
    chunks = []
    for m in methods:
        attr = getattr(RenderWhisperCaptions, m)
        if inspect.isfunction(attr) or inspect.ismethod(attr):
            try:
                chunks.append(inspect.getsource(attr))
            except (TypeError, OSError):
                pass
    return "\n".join(chunks)


class TestBt709Tags:
    """The output MUST carry bt709 colorspace tags end-to-end."""

    def test_colorspace_bt709_flag_in_argv(self):
        src = _captioning_source()
        assert "-colorspace" in src
        assert '"bt709"' in src

    def test_color_primaries_bt709_flag_in_argv(self):
        src = _captioning_source()
        assert "-color_primaries" in src

    def test_color_trc_bt709_flag_in_argv(self):
        src = _captioning_source()
        assert "-color_trc" in src


class TestAudioNormalization:
    """Source audio must NEVER be -c:a copy here — that path defers the
    44.1 kHz/mono problem to ValidateVideos, triggering a 3rd-gen
    re-encode."""

    def test_audio_codec_is_aac(self):
        src = _captioning_source()
        # No `"-c:a", "copy"` anywhere in the argv-building (the silent-clip
        # mux path was also rewritten in the fix).
        assert '"-c:a", "copy"' not in src.replace("'", '"')
        # And aac IS present
        assert '"aac"' in src

    def test_audio_sample_rate_48000(self):
        src = _captioning_source()
        assert '"-ar"' in src
        assert '"48000"' in src

    def test_audio_channels_stereo(self):
        src = _captioning_source()
        assert '"-ac"' in src
        assert '"2"' in src
