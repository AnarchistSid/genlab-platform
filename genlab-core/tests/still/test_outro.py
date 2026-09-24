"""Pins for the outro-card trim.

The pin that matters is the mid-card wipe: the JJK upload's outro contains a
single-frame transition between thumbnail panels. Detection by frame-to-frame
similarity ended the run there and reported a 9.5 s card against a real 19 s
one, which would have left half the subscribe panel inside the searchable
window.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from genlab_core.still import outro

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def _clip(path: Path, *, card_s: float, wipe_at: float | None = None) -> Path:
    """Moving content, then a static card, optionally with a one-frame wipe."""
    content_s = 12.0
    parts = [
        # content: a moving pattern, nothing like the card
        # Temporal noise: real anime content holds only 3-7% of its pixels
        # still between samples, so a mostly-static synthetic pattern is not
        # a faithful control -- it reads as a card.
        f"color=c=gray:size=320x180:rate=10:duration={content_s},"
        f"noise=alls=90:allf=t+u[c]",
        # the card: flat colour with a bright block, held
        f"color=c=#E88020:size=320x180:rate=10:duration={card_s}[k]",
    ]
    graph = ";".join(parts) + ";[c][k]concat=n=2:v=1:a=0[out]"
    if wipe_at is not None:
        # one dark frame inside the card, at wipe_at seconds from the start
        graph = graph.replace(
            "[out]",
            f"[j];[j]drawbox=x=0:y=0:w=iw:h=ih:color=black@1:t=fill:"
            f"enable='between(t,{wipe_at},{wipe_at + 0.1})'[out]")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-filter_complex", graph, "-map", "[out]",
         "-pix_fmt", "yuv420p", "-y", str(path)], check=True)
    return path


def test_finds_a_plain_card(tmp_path):
    o = outro.detect(_clip(tmp_path / "plain.mp4", card_s=9.0))
    assert o.found
    assert o.duration_s == pytest.approx(9.0, abs=1.0)


def test_a_wipe_inside_the_card_does_not_end_it_early(tmp_path):
    """The defect: a 19 s card read as 9.5 s."""
    o = outro.detect(_clip(tmp_path / "wipe.mp4", card_s=19.0, wipe_at=16.0))
    assert o.found
    assert o.duration_s == pytest.approx(19.0, abs=1.5), (
        f"the mid-card wipe truncated the card to {o.duration_s:.1f}s")


def test_no_card_on_moving_content(tmp_path):
    clip = tmp_path / "none.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "color=c=gray:s=320x180:r=10:d=20",
                    "-vf", "noise=alls=90:allf=t+u",
                    "-pix_fmt", "yuv420p", "-y", str(clip)], check=True)
    o = outro.detect(clip)
    assert not o.found and o.usable_end_s == pytest.approx(o.clip_s, abs=0.2)
