"""PR #FrameCompositorAudio (2026-07-11) — audio bitrate 320k → 192k.

Source pin on the frame_compositor's ``-b:a`` flag to prevent a
regression back to 320k that would re-trigger Meta 2207082 container
processing errors on Instagram (as happened to anime + movies + sports
on today's 06:30 UTC publish before this PR shipped).

Full behavioural tests would need a real ffmpeg invocation with a
representative video — the source pin is the pragmatic backstop.
Consistent with the other frame_compositor pin patterns in the media
test suite.
"""

from __future__ import annotations

import re
from pathlib import Path


def _normalise(src: str) -> str:
    return re.sub(r"\s+", " ", src)


def test_audio_bitrate_is_192k_not_320k():
    """The ffmpeg render flags must specify ``-b:a 192k``. Any accidental
    revert to 320k (or bump above IG's ~192k safe threshold) re-opens
    the 2207082 container-processing failure class."""
    import genlab_core.media.frame_compositor as mod

    src = _normalise(Path(mod.__file__).read_text())

    # The flags list literal — normalisation collapses whitespace so
    # the multi-line list becomes a single-line sequence.
    assert '"-b:a", "192k"' in src, (
        "frame_compositor must render at 192k audio (2026-07-11 fix). "
        "See Meta error 2207082 comment in the module for rationale."
    )
    # Belt-and-suspenders — explicit hostile check for the pre-fix value.
    assert '"-b:a", "320k"' not in src, (
        "frame_compositor still has 320k audio hardcoded — reverts to "
        "the pre-2026-07-11 state that caused today's IG failures. "
        "See the module comment block for context."
    )


def test_audio_matches_meta_platform_spec():
    """The compositor's 192k matches
    ``PLATFORM_SPECS[Instagram].audio_bitrate = '192k'`` in ffmpeg.py.
    If either drifts, IG uploads will re-fail with 2207082."""
    from genlab_core.media.ffmpeg import PLATFORM_SPECS, Platform

    ig_spec = PLATFORM_SPECS[Platform.INSTAGRAM]
    assert ig_spec.audio_bitrate == "192k", (
        "PLATFORM_SPECS[Instagram] audio_bitrate diverged from "
        "192k — the compositor is now at 192k and will produce masters "
        "that no longer match the platform spec."
    )
