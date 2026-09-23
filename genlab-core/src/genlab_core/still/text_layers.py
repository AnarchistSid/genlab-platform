"""Two text layers: headline slams, and the caption plate.

ANIME-14 §1 and §3. The genre runs two registers at once and they do
different jobs:

* **Headline** — the hook's tension line, then the two or three facts as
  kinetic slams on downbeats. Big, high contrast, safe zone, short on screen.
  This is the layer a viewer reads in the first second and the one that makes
  the facts land before the card ever appears.
* **Caption** — the narration's own words, small, on a plate, at the bottom.
  Built from the SCRIPT with ASR timings (see ``still.align``), never from a
  transcript of our own synthetic speech.

They must not collide, so they live in reserved bands: headline in the upper
third, captions at 0.72 h. Nothing here writes into the middle of the frame,
which is where faces are.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: Safe zone. Platform UI eats the top ~8% and the bottom ~14% of a reel.
SAFE_TOP = 0.10
SAFE_BOTTOM = 0.86
#: The headline band — upper third, above a centred face.
HEADLINE_Y = "h*0.20"
#: The hook's tension line sits slightly lower so it reads as the title card.
HOOK_Y = "h*0.36"

HOOK_MAX_WORDS = 5
HOOK_ONSET_MAX_S = 1.5
#: Kinetic slam duration bounds from the packet.
SLAM_MIN_S = 0.6
SLAM_MAX_S = 1.0


class HookRejected(ValueError):
    """A tension line that would fail the on-screen gate."""


@dataclass(frozen=True)
class Hook:
    """The tension line: 2-5 words, derived from the story's TURN.

    Not sentence one of the narration. The narration's first line introduces;
    the hook states the thing that makes the story worth watching. "Firefly
    Wedding premieres in October" is an introduction. "She married her
    assassin." is a turn.
    """

    text: str
    onset_s: float = 0.3
    duration_s: float = 2.2

    def __post_init__(self) -> None:
        n = len(self.text.split())
        if not 2 <= n <= HOOK_MAX_WORDS:
            raise HookRejected(
                f"hook is {n} words; the band is 2-{HOOK_MAX_WORDS}. A longer line "
                "is a sentence, and a sentence is not read in the first second."
            )
        if self.onset_s > HOOK_ONSET_MAX_S:
            raise HookRejected(f"hook onset {self.onset_s}s is past {HOOK_ONSET_MAX_S}s")
        if re.search(r"[.;:]$", self.text.strip()):
            raise HookRejected("a tension line does not end on punctuation")


@dataclass(frozen=True)
class Slam:
    """One fact, on a downbeat. Upper-cased because the genre upper-cases."""

    text: str
    at_s: float
    duration_s: float = 0.8

    def __post_init__(self) -> None:
        if not SLAM_MIN_S <= self.duration_s <= SLAM_MAX_S:
            raise ValueError(
                f"slam {self.duration_s}s outside {SLAM_MIN_S}-{SLAM_MAX_S}s: shorter "
                "is unreadable, longer stops being a slam and becomes a caption"
            )


def _textfile(textdir: Path, tag: str, text: str) -> str:
    """Stage text in a file. See ``still.overlay._textfile`` for why: there is
    no escape for a quote inside a quoted filtergraph value."""
    from genlab_core.still.overlay import _textfile as stage

    return stage(textdir, tag, text)


#: Character-width ratio for the default sans at a given fontsize. Same
#: constant talk/captions.py uses for its own width budget.
CHAR_W_RATIO = 0.58
#: Text may occupy this share of the frame width.
MAX_TEXT_WIDTH_FRAC = 0.90
FRAME_W = 1080


def fit_lines(
    text: str, *, max_size: int, min_size: int = 54, max_lines: int = 2
) -> tuple[list[str], int]:
    """Split into at most ``max_lines`` and return the size that FITS.

    The caption path learned this in ANIME-12 and the hook path was written
    fresh without it: "SHE MARRIED HER ASSASSIN" at size 106 is 1474 px on a
    1080 px frame, and `x=(w-text_w)/2` is then NEGATIVE, so it clips off both
    edges at once. A hook that cannot be read is worse than no hook.

    Balanced by width rather than word count, because a two-word line and a
    four-word line can be the same length on screen.
    """
    budget = FRAME_W * MAX_TEXT_WIDTH_FRAC
    words = text.split()

    def width(line: str, size: int) -> float:
        return len(line) * CHAR_W_RATIO * size

    # Evaluate EVERY line count and take the largest size, not the first one
    # that happens to fit. Returning at n_lines=1 as soon as it fits gave
    # "SHE MARRIED HER ASSASSIN" on one line at 69 px when the same words
    # split across two lines fit at 118 — the hook is the biggest text in the
    # reel and picking the first legal answer made it the smallest.
    best: tuple[int, int, list[str]] | None = None  # (size, -spread, lines)
    for n_lines in range(1, max_lines + 1):
        if n_lines == 1:
            cand = [[" ".join(words)]]
        else:
            cand = [[" ".join(words[:k]), " ".join(words[k:])] for k in range(1, len(words))]
        for lines in cand:
            if any(not ln for ln in lines):
                continue
            longest = max(len(ln) for ln in lines)
            size = min(max_size, int(budget / (longest * CHAR_W_RATIO)))
            if size < min_size:
                continue
            spread = max(len(ln) for ln in lines) - min(len(ln) for ln in lines)
            key = (size, -spread, lines)
            if best is None or key[:2] > best[:2]:
                best = key
    if best:
        return best[2], best[0]

    # Nothing fits inside max_lines at min_size: shrink rather than clip.
    lines = [" ".join(words[: len(words) // 2]), " ".join(words[len(words) // 2 :])]
    longest = max(len(ln) for ln in lines)
    logger.warning(
        "[text] %r does not fit %d lines at >=%dpx; shrinking to %dpx",
        text,
        max_lines,
        min_size,
        int(budget / (longest * CHAR_W_RATIO)),
    )
    return lines, max(int(budget / (longest * CHAR_W_RATIO)), 32)


def hook_filters(
    hook: Hook, textdir: Path, accent: str = "#FFFFFF", font: str | None = None
) -> str:
    """The tension line, punching in and gone.

    The punch is STEPPED — constant sizes in adjacent windows — not a
    time-varying ``fontsize`` expression. drawtext accepts an expression there
    and this ffmpeg build SEGFAULTS on it (rc=-11, empty stderr, which reads
    as "the command was rejected" rather than "ffmpeg crashed"). Stepping also
    looks more like the genre: anime titling snaps between sizes.

    Scale-punching the TEXT rather than the frame is deliberate — zooming the
    frame would move the footage the hook is sitting on.
    """
    ff = f":fontfile={font}" if font else ""
    lines, base = fit_lines(hook.text.upper(), max_size=118)
    n = len(lines)
    steps = [(0.00, 0.09, 0.80), (0.09, 0.18, 1.12), (0.18, hook.duration_s, 1.0)]
    parts = []
    for li, line in enumerate(lines):
        path = _textfile(textdir, f"hook_{li}", line)
        rise = n - 1 - li
        for a, b, mult in steps:
            size = max(28, int(base * mult))
            t0, t1 = hook.onset_s + a, hook.onset_s + b
            y = f"{HOOK_Y}-{rise}*{size + 14}"
            parts.append(
                f"drawtext=textfile='{path}':fontsize={size}"
                f":fontcolor=white:borderw=6:bordercolor=black@0.85"
                f":box=1:boxcolor=black@0.35:boxborderw=24"
                f":x=(w-text_w)/2:y={y}"
                f":enable='between(t,{t0:.3f},{t1:.3f})'{ff}"
            )
    return ",".join(parts)


def slam_filters(
    slams: list[Slam], textdir: Path, accent: str = "#FFFFFF", font: str | None = None
) -> str:
    """The facts, one per downbeat, in the headline band."""
    accent_hex = accent.lstrip("#")
    parts = []
    for i, s in enumerate(slams):
        path = _textfile(textdir, f"slam_{i:02d}", s.text.upper())
        ff = f":fontfile={font}" if font else ""
        end = s.at_s + s.duration_s
        # WHITE fill with the brand accent as the border, not accent fill.
        # #7B3FE4 on a dark anime frame is low contrast — legible, but not the
        # "big, high-contrast" the slam is for. White carries the reading and
        # the accent still marks it as ours.
        lines, size = fit_lines(s.text.upper(), max_size=84, max_lines=1)
        parts.append(
            f"drawtext=textfile='{path}':fontsize={size}"
            f":fontcolor=white:borderw=6:bordercolor=0x{accent_hex}"
            f":box=1:boxcolor=black@0.55:boxborderw=22"
            f":x=(w-text_w)/2:y={HEADLINE_Y}"
            f":enable='between(t,{s.at_s:.3f},{end:.3f})'{ff}"
        )
    return ",".join(parts)


def facts_to_slams(
    facts: dict,
    beats: list[float],
    *,
    start_after_s: float = 3.0,
    max_slams: int = 3,
    duration_s: float = 0.8,
) -> list[Slam]:
    """Pick the two or three facts worth slamming, and put them on downbeats.

    Premiere date first, then studio, then episode count — the order a viewer
    cares about for an upcoming show. A fact with no value is skipped rather
    than slammed empty, which is how "EPISODES" alone ends up on screen.
    """
    order = [
        ("premiere", str(facts.get("premiere") or "")),
        ("studio", str(facts.get("studio") or "")),
        ("episodes", f"{facts.get('episodes')} EPISODES" if facts.get("episodes") else ""),
    ]
    usable = [v for _, v in order if v.strip()][:max_slams]
    downbeats = [b for b in beats if b >= start_after_s]
    out: list[Slam] = []
    for i, text in enumerate(usable):
        if i * 2 >= len(downbeats):
            logger.warning(
                "[text] only %d downbeats after %.1fs for %d slams — dropping the rest "
                "rather than placing one off the grid",
                len(downbeats),
                start_after_s,
                len(usable),
            )
            break
        out.append(Slam(text=text, at_s=downbeats[i * 2], duration_s=duration_s))
    return out


def check_hook_gate(
    hook: Hook, *, frame_text_fraction: float, max_frame_text: float = 0.03
) -> list[str]:
    """§1's gate, as a list of failures. Empty means it passed."""
    problems = []
    if hook.onset_s > HOOK_ONSET_MAX_S:
        problems.append(f"hook onset {hook.onset_s:.2f}s > {HOOK_ONSET_MAX_S}s")
    n = len(hook.text.split())
    if n > HOOK_MAX_WORDS:
        problems.append(f"hook is {n} words > {HOOK_MAX_WORDS}")
    if frame_text_fraction > max_frame_text:
        problems.append(
            f"the frame under the hook carries {frame_text_fraction:.1%} of its own "
            f"text (limit {max_frame_text:.0%}) — two headlines at once"
        )
    return problems
