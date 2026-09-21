"""Ken Burns on a still, at the storyboard's durations.

Two bugs found by rendering rather than reading, both pinned:

1. `zoompan d=<frames>` with a `-loop 1 -t <dur>` input MULTIPLIES: ffmpeg
   feeds dur x fps input frames and d expands every one of them into N
   output frames. A 2.44 s shot rendered as 148.43 s. It is `d=1`.

2. The motion variable is `on`, not `n`. `n` raises "Undefined constant" deep
   in the filter graph, which surfaces as libx264 error -22 and an EMPTY
   OUTPUT FILE — not as a filter error. Nothing says "bad expression".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from genlab_core.still.beats import load_kit, plan_beats
from genlab_core.still.render import (
    FPS,
    Shot,
    UnknownPattern,
    assert_patterns_supported,
    build_motion_filter,
    probe_duration,
    render_shot,
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

SCRIPT = [
    "A dying noblewoman meets her match.",
    "Satoko's got beauty, status, and months to live before illness claims her forever.",
    "When a mysterious assassin named Shinpei targets her, she makes him an insane proposal.",
    "She'll marry him if he protects her, but Shinpei means every word of forever.",
    "Firefly Wedding premieres October ninth, twenty twenty-six.",
]


@pytest.fixture(scope="module")
def still_image(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("still") / "s.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "gradients=s=1080x1920:n=3",
            "-frames:v",
            "1",
            str(p),
        ],
        check=True,
    )
    return p


@pytest.fixture(scope="module")
def kit():
    return load_kit("anime")


class TestFilterExpression:
    def test_duration_is_one_frame_per_input_frame(self, kit):
        """`d=1`, never d=<frames> — the two multiply."""
        vf = build_motion_filter("zoom_in", 2.44, 1.18)
        assert ":d=1:" in vf, f"d must be 1, got: {vf}"

    def test_motion_uses_on_not_n(self, kit):
        """`n` is not a zoompan variable; it fails as an empty output file."""
        for pat in kit["motion"]["ken_burns"]["patterns"]:
            vf = build_motion_filter(pat, 3.0, 1.18)
            assert "*n" not in vf.replace("*on", ""), f"{pat} uses bare n: {vf}"
            assert "on" in vf

    def test_every_kit_pattern_is_implemented(self, kit):
        assert_patterns_supported(kit)

    def test_pan_zooms_vocabulary_is_rejected_loudly(self):
        """media/pan_zoom.py FAILS OPEN on unknown patterns — returns an empty
        filter and a warning, which would render a motionless still. This
        renderer raises instead."""
        for pat in ("ken_burns_slow", "punch_in", "dolly", "no_motion", "typo"):
            with pytest.raises(UnknownPattern, match="no motion filter"):
                build_motion_filter(pat, 3.0, 1.18)


class TestRenderedDuration:
    @pytest.mark.parametrize("pattern", ["zoom_in", "zoom_out", "pan_left", "pan_right"])
    def test_a_shot_is_the_duration_it_was_asked_for(self, pattern, still_image, kit, tmp_path):
        out = tmp_path / f"{pattern}.mp4"
        assert render_shot(Shot(0, still_image, out, pattern, 2.44), kit)
        got = probe_duration(out)
        assert abs(got - 2.44) <= 1.5 / FPS, f"{pattern}: {got}s vs 2.44s"

    def test_the_whole_storyboard_renders_within_a_frame(self, still_image, kit, tmp_path):
        board = plan_beats(SCRIPT, wpm=177, speaking_rate=1.05)
        total = 0.0
        for b in board.beats:
            out = tmp_path / f"{b.index}.mp4"
            assert render_shot(
                Shot(b.index, still_image, out, board.pattern_for(b.index), b.shot_s), kit
            )
            got = probe_duration(out)
            assert abs(got - b.shot_s) <= 0.01 + 1.0 / FPS, f"shot {b.index}"
            total += got
        # Cumulative drift is frame quantisation: sub-frame per shot, summed.
        # The audio stays master at concat, so this bounds the video, not sync.
        assert abs(total - board.total_s) <= len(board.beats) / FPS

    def test_output_is_bt709_and_portrait(self, still_image, kit, tmp_path):
        """`color_space` is what the pipeline actually enforces.

        validate_videos' spec is `color_space: bt709`; it does not check
        color_primaries or color_transfer. With this flag order those two read
        `unknown` from ffprobe — and media/caption_animator.py passes the same
        flags in the same order, so that is existing behaviour across the
        repo, not something this renderer introduces. Asserting primaries here
        would hold this path to a bar the shipped path does not meet.
        """
        out = tmp_path / "c.mp4"
        assert render_shot(Shot(0, still_image, out, "zoom_in", 2.0), kit)
        # key=value, NOT csv=p=0: csv drops the keys and emits fields in the
        # STREAM's order rather than the requested order, so positional
        # unpacking silently reads pix_fmt as color_space.
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,color_space,pix_fmt",
                "-of",
                "default=nw=1",
                str(out),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        fields = dict(line.split("=", 1) for line in probe.strip().splitlines() if "=" in line)
        assert (int(fields["width"]), int(fields["height"])) == (1080, 1920)
        assert fields.get("color_space") == "bt709", fields
        assert fields.get("pix_fmt") == "yuv420p", fields
