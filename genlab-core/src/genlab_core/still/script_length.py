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


def target_words(target_s: float, wpm: float) -> int:
    if target_s <= 0 or wpm <= 0:
        raise ValueError(f"target_s={target_s} wpm={wpm} — both must be positive")
    return round(target_s * wpm / 60.0)


def check(beats: list[str], *, target_s: float, wpm: float) -> LengthCheck:
    """Measure a script against its target. Does not raise — see ``enforce``."""
    from genlab_core.still import delivery

    n = sum(len(delivery.words(b)) for b in beats)
    target = target_words(target_s, wpm)
    return LengthCheck(n, target, round(target * MIN_FRACTION), wpm, target_s)


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
    n = target_words(target_s, wpm)
    return (
        f"Write approximately {n} words in total — this narration is read aloud "
        f"at {wpm:.0f} words per minute and must fill about {target_s:.0f} seconds. "
        f"A script shorter than {round(n * MIN_FRACTION)} words will be rejected."
    )
