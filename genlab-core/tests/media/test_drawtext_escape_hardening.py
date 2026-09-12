"""T-14a hardening: every drawtext escape helper against hostile text.

Apostrophes were load-bearing and unnoticed until 2026-09-12, when a caption
filtergraph failed with ``No such filter: '0.000'`` -- a time value parsed as a
filter name because ``Pope's`` closed the ``text='...'`` quote early and
inverted quoting for the rest of the graph.

Two assertions per case, because there are two distinct failures:
  1. the graph BUILDS (a mis-escape usually explodes), and
  2. the text SURVIVES (a mis-escape can also silently drop or substitute a
     character, which is quieter and worse).

The second is checked structurally -- the escaped form must still contain the
original's characters once FFmpeg's quote-hopping idiom is collapsed -- rather
than by OCR, so the test needs no fonts, no rendering and no network.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from genlab_core.media import caption_animator, chart_broll, ffmpeg_utils
from genlab_core.rendering import overlay_compositor

HELPERS = {
    "caption_animator": caption_animator._escape_drawtext,
    "chart_broll": chart_broll._escape_drawtext,
    "ffmpeg_utils_simple": ffmpeg_utils._escape_drawtext_simple,
    "overlay_compositor": overlay_compositor._escape_drawtext,
}

# Helpers that DELIBERATELY substitute U+2019 RIGHT SINGLE QUOTATION MARK for the
# ASCII apostrophe instead of escaping it. For display text (chart labels, title
# overlays) U+2019 is the typographically correct apostrophe, so this is a real
# choice and not a defect -- but it IS a text substitution, so it is pinned here
# explicitly. A helper silently joining or leaving this set is a test failure,
# which is the point: the 2026-09-12 sweep found both of these only because the
# preservation assertion is separate from the "does it build" one.
SUBSTITUTES_TYPOGRAPHIC_APOSTROPHE = {"chart_broll", "overlay_compositor"}

# Strings that have broken, or plausibly could break, a filtergraph.
CASES = [
    ("apostrophe", "Pope's"),
    ("apostrophe_double", "Ubisoft's Assassin's Creed"),
    ("colon", "12:30"),
    ("both", "Pope's 12:30 address"),
    ("comma", "one, two, three"),
    ("percent", "100% clutch"),
    ("backslash", r"back\slash"),
    ("double_quote", 'say "hi"'),
    ("equals", "score=10"),
    ("brackets", "[tag] and [other]"),
    ("non_ascii_latin", "Pokémon"),
    ("non_ascii_cjk", "ジョジョ"),
    ("emoji", "clutch 🎮 play"),
]

_FFMPEG = shutil.which("ffmpeg")


def _collapse_quote_idiom(escaped: str) -> str:
    """Undo the ``'\\''`` quote-hop so the payload can be compared to the input."""
    return escaped.replace("'\\''", "'")


@pytest.mark.parametrize("helper_name", sorted(HELPERS))
@pytest.mark.parametrize("case_name,text", CASES, ids=[c[0] for c in CASES])
def test_escaped_text_preserves_every_character(helper_name, case_name, text):
    """A mis-escape must not silently drop or substitute characters."""
    escaped = HELPERS[helper_name](text)
    collapsed = _collapse_quote_idiom(escaped)
    expected = text
    if helper_name in SUBSTITUTES_TYPOGRAPHIC_APOSTROPHE:
        expected = expected.replace("'", "\u2019")
    # Every non-backslash character of the input survives, in order.
    stripped = collapsed.replace("\\", "")
    assert stripped == expected.replace("\\", ""), (
        f"{helper_name} altered {case_name}: {text!r} -> {escaped!r}"
    )


@pytest.mark.skipif(_FFMPEG is None, reason="ffmpeg not on PATH")
@pytest.mark.parametrize("helper_name", sorted(HELPERS))
@pytest.mark.parametrize("case_name,text", CASES, ids=[c[0] for c in CASES])
def test_escaped_text_builds_a_valid_filtergraph(tmp_path, helper_name, case_name, text):
    """The graph must parse. ``enable=`` follows the text so a quote that
    terminates early exposes a time value as a filter name -- the exact
    production symptom."""
    escaped = HELPERS[helper_name](text)
    src = tmp_path / "in.png"
    subprocess.run(
        [_FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=black:s=320x240:d=1", "-frames:v", "1", str(src)],
        check=True, capture_output=True,
    )
    chain = (
        f"drawtext=text='{escaped}':fontsize=20:fontcolor=white:"
        f"x=5:y=5:enable='between(t,0.000,5.000)'"
    )
    r = subprocess.run(
        [_FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
         "-vf", chain, "-frames:v", "1", str(tmp_path / "out.png")],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, (
        f"{helper_name} produced an unparseable graph for {case_name}: "
        f"{text!r} -> {escaped!r}\n{r.stderr.strip()[:300]}"
    )
