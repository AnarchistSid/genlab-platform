"""Script sentences -> beats -> shots.

The storyboard comes from the script's beats, never from cuts in footage:
there is no footage on this path. One sentence of narration is one beat; each
beat gets one shot, clamped to the kit's 3-5 s window.

Why the clamp is the kit's and not a constant here: under 3 s a still reads
as a flicker, over 5 s it is a held frame and trips ``longest_static``. Those
are per-niche judgements, so they live in the YAML the operator can diff.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_KITS = Path(__file__).parent / "kits"

#: Sentence-ish split. Deliberately simple: the script is authored one
#: sentence per line by the narration writer, so this only has to cope with a
#: line that carries two.
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

#: A "number" for kinetic type: digits, or a spelled-out date/ordinal the TTS
#: will read as one. "October ninth" is a number to a viewer even though it
#: contains no digit.
_NUMBER = re.compile(
    r"\b(\d[\d,.]*|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"ninth|tenth|first|second|third|twenty|thirty|hundred|thousand|million)\b",
    re.I,
)


def effective_wpm(wpm: float, speaking_rate: float) -> float:
    """Words per minute actually delivered.

    ``wpm`` is the tier's MEASURED prediction rate; ``speaking_rate`` is the
    delivery multiplier sent to the TTS call. Keeping them separate is the
    whole point: writing a delivery target into the predictor under-predicts
    duration and every script it sizes gets thrown away as vo_overrun.
    """
    if wpm <= 0:
        raise ValueError(f"wpm must be positive, got {wpm}")
    if speaking_rate <= 0:
        raise ValueError(f"speaking_rate must be positive, got {speaking_rate}")
    return wpm * speaking_rate


@dataclass(frozen=True)
class Beat:
    """One sentence of narration and the shot that carries it."""

    index: int
    text: str
    words: int
    start_s: float
    spoken_s: float
    shot_s: float
    is_hook: bool
    names: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    #: (k, n) — this shot k of n covering one sentence of narration.
    shot_of: tuple[int, int] = (1, 1)

    @property
    def end_s(self) -> float:
        return self.start_s + self.shot_s


@dataclass(frozen=True)
class Storyboard:
    beats: tuple[Beat, ...]
    hero_index: int
    total_s: float
    kit: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def hero(self) -> Beat:
        return self.beats[self.hero_index]

    def pattern_for(self, index: int) -> str:
        """Ken Burns direction, alternating so the reel does not drift one way."""
        pats = self.kit["motion"]["ken_burns"]["patterns"]
        return pats[index % len(pats)]

    @property
    def longest_static_s(self) -> float:
        return max((b.shot_s for b in self.beats), default=0.0)


def load_kit(name: str = "anime") -> dict[str, Any]:
    path = _KITS / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(
            f"no STILL kit {name!r} at {path}; kits are per-niche and must be "
            "authored, not defaulted — a missing palette would silently render "
            "in some other niche's look"
        )
    kit = yaml.safe_load(path.read_text())
    b = kit["beats"]
    # Refuse an infeasible kit rather than silently breaking min_shot_s.
    if b["max_shot_s"] < 2 * b["min_shot_s"]:
        raise ValueError(
            f"kit {name!r}: max_shot_s ({b['max_shot_s']}) must be at least "
            f"2 x min_shot_s ({b['min_shot_s']}), else some narration lengths "
            "have no valid shot count and the splitter breaks the minimum"
        )
    if b["max_shot_s"] > kit["gates"]["longest_static_s"]:
        raise ValueError(
            f"kit {name!r}: max_shot_s ({b['max_shot_s']}) exceeds its own "
            f"longest_static gate ({kit['gates']['longest_static_s']}) — every "
            "reel would fail the gate it ships with"
        )
    return kit


def _names_in(text: str) -> tuple[str, ...]:
    """Capitalised tokens that are not sentence-initial.

    Deliberately conservative: kinetic type firing on the wrong word is worse
    than not firing, so a name must be capitalised MID-sentence to count.
    """
    toks = text.split()
    out = []
    for i, tok in enumerate(toks):
        bare = tok.strip(".,!?;:'\"—-")
        if i > 0 and bare[:1].isupper() and len(bare) > 2:
            out.append(bare)
    return tuple(dict.fromkeys(out))


def plan_beats(
    lines: list[str],
    *,
    wpm: float,
    speaking_rate: float,
    kit: dict[str, Any] | None = None,
) -> Storyboard:
    """Turn a narration script into a storyboard.

    ``lines`` is the script, one sentence per line as the narration writer
    emits it. A line carrying two sentences is split; a fragment shorter than
    the kit's ``merge_below_s`` is merged into its predecessor rather than
    given a shot, because a 0.8 s still is a blink.
    """
    kit = kit or load_kit()
    b = kit["beats"]
    rate = effective_wpm(wpm, speaking_rate)

    sentences: list[str] = []
    for line in lines:
        for part in _SENTENCE.split(line.strip()):
            if part.strip():
                sentences.append(part.strip())
    if not sentences:
        raise ValueError("no sentences in the script — nothing to storyboard")

    # Merge fragments too short to hold a shot.
    merged: list[str] = []
    for s in sentences:
        spoken = len(s.split()) / rate * 60.0
        if merged and spoken < b["merge_below_s"]:
            merged[-1] = f"{merged[-1]} {s}"
        else:
            merged.append(s)

    beats: list[Beat] = []
    t = 0.0
    idx = 0
    for text in merged:
        words = len(text.split())
        spoken = words / rate * 60.0
        # A beat longer than one shot is SPLIT, never held. Holding it would
        # push longest_static past the kit's own gate -- max_shot_s is bounded
        # by gates.longest_static_s for exactly this reason.
        # Choose HOW MANY shots so each lands inside [min, max]; never clamp
        # the duration afterwards. Clamping made every shot exactly
        # min_shot_s, so 19.95 s of narration became 24.0 s of video and each
        # still drifted away from the sentence it illustrates. The stills
        # change WITH the narration or they are decoration.
        n_shots = max(1, round(spoken / ((b["min_shot_s"] + b["max_shot_s"]) / 2)))
        while n_shots > 1 and spoken / n_shots < b["min_shot_s"]:
            n_shots -= 1
        while spoken / n_shots > b["max_shot_s"]:
            n_shots += 1
        per = spoken / n_shots
        for k in range(n_shots):
            shot = per
            beats.append(
                Beat(
                    index=idx,
                    text=text,
                    words=words,
                    start_s=round(t, 3),
                    spoken_s=round(per, 3),
                    shot_s=round(shot, 3),
                    is_hook=(idx == 0),
                    names=_names_in(text),
                    numbers=tuple(m.group(0) for m in _NUMBER.finditer(text)),
                    shot_of=(k + 1, n_shots),
                )
            )
            t += shot
            idx += 1

    # The hero is the beat that names the subject: the first beat carrying a
    # name, else the longest. It is the one that earns a parallax pass.
    hero = next((x.index for x in beats if x.names), None)
    if hero is None:
        hero = max(beats, key=lambda x: x.words).index

    return Storyboard(
        beats=tuple(beats), hero_index=hero, total_s=round(t, 3), kit=kit
    )
