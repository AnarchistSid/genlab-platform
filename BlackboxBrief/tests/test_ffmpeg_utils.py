"""Tests for execution/utils/ffmpeg_utils.py constants and helpers.

P1.5:  FINAL_VIDEO_PARAMS must include -r 30 for consistent framerate.
P1.6:  No hardcoded 192k audio bitrate anywhere — canonical value is 256k.
P1.14: Clip pre-processing timeout must scale with duration.
"""


# ── P1.5: FINAL_VIDEO_PARAMS must contain -r 30 ────────────────────


def test_final_video_params_contains_framerate():
    """FINAL_VIDEO_PARAMS must include '-r' followed by '30'."""
    from genlab_core.media.ffmpeg_utils import FINAL_VIDEO_PARAMS

    assert "-r" in FINAL_VIDEO_PARAMS, "Missing '-r' flag in FINAL_VIDEO_PARAMS"
    idx = FINAL_VIDEO_PARAMS.index("-r")
    assert idx + 1 < len(FINAL_VIDEO_PARAMS), "'-r' flag has no value after it"
    assert FINAL_VIDEO_PARAMS[idx + 1] == "30", f"Expected '-r' value '30', got '{FINAL_VIDEO_PARAMS[idx + 1]}'"


def test_final_video_params_framerate_before_gop():
    """-r must appear before -g (GOP depends on fps)."""
    from genlab_core.media.ffmpeg_utils import FINAL_VIDEO_PARAMS

    r_idx = FINAL_VIDEO_PARAMS.index("-r")
    g_idx = FINAL_VIDEO_PARAMS.index("-g")
    assert r_idx < g_idx, f"-r at index {r_idx} should come before -g at index {g_idx}"


# ── P1.6: No 192k audio bitrate in pipeline files ──────────────────


def test_final_audio_params_uses_256k():
    """FINAL_AUDIO_PARAMS must specify 256k audio bitrate."""
    from genlab_core.media.ffmpeg_utils import FINAL_AUDIO_PARAMS

    idx = FINAL_AUDIO_PARAMS.index("-b:a")
    assert FINAL_AUDIO_PARAMS[idx + 1] == "256k", f"Expected '256k', got '{FINAL_AUDIO_PARAMS[idx + 1]}'"


def test_no_192k_in_render_text_overlays():
    """render_text_overlays.py must not hardcode 192k audio bitrate."""
    from pathlib import Path

    # Check genlab-core canonical version (BB execution/ copy archived in Sprint 66)
    src = (
        Path(__file__).resolve().parent.parent.parent
        / "genlab-core"
        / "src"
        / "genlab_core"
        / "pipeline"
        / "stages"
        / "render_text_overlays.py"
    )
    if not src.exists():
        # Fall back to BB location for backward compat
        src = Path(__file__).resolve().parent.parent / "execution" / "render_text_overlays.py"
    content = src.read_text()
    # Check that no literal "192k" appears in non-comment code lines
    for lineno, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert '"192k"' not in stripped, f"render_text_overlays.py line {lineno} still has hardcoded '192k': {stripped}"


def test_no_192k_in_assemble_video_reel():
    """assemble_video_reel.py must not hardcode 192k audio bitrate.

    Skip when the file has been removed by a refactor — the regression
    this guards (a hardcoded 192k bitrate) only matters if the file
    exists. The check via genlab_core.media.ffmpeg's PLATFORM_SPECS
    covers the same invariant for the current rendering path.
    """
    from pathlib import Path

    import pytest

    src = Path(__file__).resolve().parent.parent / "execution" / "assemble_video_reel.py"
    if not src.exists():
        pytest.skip(
            "assemble_video_reel.py removed in refactor; bitrate now enforced via genlab_core.media.ffmpeg PLATFORM_SPECS"
        )
    content = src.read_text()
    for lineno, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert '"192k"' not in stripped, f"assemble_video_reel.py line {lineno} still has hardcoded '192k': {stripped}"
