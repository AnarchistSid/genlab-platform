"""How many words a reel's script needs, and the gate that says so.

ANIME-13 §7. TOUGEN ANKI's pipeline script is 27 words. Rendered correctly it
produced a correct, gate-passing reel of 10.69 s against a 15 s platform
floor — nothing in the renderer could fix it, because the only way to stretch
27 words is to hold stills longer, which is what the longest-static rule
exists to forbid.

27 words is what "no target" produces. The target is arithmetic:

    words = target_seconds * measured_wpm / 60

``measured_wpm`` is per VOICE and per ENGINE, never assumed. Measured on the
same 79-word script: Inworld/Sarah at speaking_rate 1.05 delivers 177 wpm;
ElevenLabs v3 / laura at stability 0.0, style 0.90 delivers 139. Same script,
same words, a 27% difference in how long the reel runs. A target computed
against the wrong engine's rate is a target for a different reel.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Below this fraction of target the script cannot fill the reel, and no
#: amount of editing downstream will rescue it.
MIN_FRACTION = 0.70


class ScriptTooShort(ValueError):
    """The script cannot fill a publishable reel. Reject at the writer."""


@dataclass(frozen=True)
class LengthCheck:
    words: int
    target: int
    floor: int
    wpm: float
    target_s: float

    @property
    def ok(self) -> bool:
        return self.words >= self.floor

    @property
    def predicted_s(self) -> float:
        return self.words * 60.0 / self.wpm

    def row(self) -> str:
        return (
            f"{self.words} words vs target {self.target} (floor {self.floor}) "
            f"at {self.wpm:.0f} wpm -> {self.predicted_s:.1f}s of narration"
        )


def target_words(
    target_s: float, wpm: float, *, tail_buffer_s: float = 2.0, fit_margin: float = 0.05
) -> int:
    """The word target, from the VALIDATOR's own helper.

    This used to be ``round(target_s * wpm / 60)`` — the packet's arithmetic,
    which ignores the 2 s music-bed tail and the fit margin. At a 38 s target
    and 139 wpm it said 88 words while the validator's cap was 79, so this
    module reported a shortfall the validator would have called too LONG.
    That is one contract with two implementers for the fourth time in this
    file's history; there is now one.
    """
    if target_s <= 0 or wpm <= 0:
        raise ValueError(f"target_s={target_s} wpm={wpm} — both must be positive")
    from genlab_core.writing.narration_validator import word_cap

    return word_cap(target_s, int(round(wpm)), tail_buffer_s, fit_margin)


def check(beats: list[str], *, target_s: float, wpm: float) -> LengthCheck:
    """Measure a script against its target. Does not raise — see ``enforce``."""
    from genlab_core.still import delivery
    from genlab_core.writing.narration_validator import word_floor

    n = sum(len(delivery.words(b)) for b in beats)
    target = target_words(target_s, wpm)
    floor = word_floor(target_s, int(round(wpm)), 2.0, 0.05, MIN_FRACTION)
    return LengthCheck(n, target, floor, wpm, target_s)


def enforce(beats: list[str], *, target_s: float, wpm: float) -> LengthCheck:
    """Reject a script that cannot fill the reel.

    Raised at the WRITER, not at the renderer: by render time the only
    remedies left are ones the kit forbids.
    """
    c = check(beats, target_s=target_s, wpm=wpm)
    if not c.ok:
        raise ScriptTooShort(f"script_too_short: {c.row()}")
    logger.info("[script] %s", c.row())
    return c


def prompt_clause(target_s: float, wpm: float) -> str:
    """The sentence the writer's prompt must carry.

    In the prompt, not only in the gate: a gate that rejects without the
    prompt ever stating the target turns a solvable instruction into a retry
    loop.
    """
    from genlab_core.writing.narration_validator import word_floor

    n = target_words(target_s, wpm)
    floor = word_floor(target_s, int(round(wpm)), 2.0, 0.05, MIN_FRACTION)
    return (
        f"Write approximately {n} words in total — this narration is read aloud "
        f"at {wpm:.0f} words per minute and must fill about {target_s:.0f} seconds. "
        f"A script shorter than {floor} words will be rejected."
    )
