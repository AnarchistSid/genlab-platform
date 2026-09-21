"""Captions carry the SCRIPT's words, with ASR supplying only the timings.

ANIME-13 §4. Reel A burned "nareema" into the frame because the captions were
the transcript of our own synthetic speech: faster-whisper misheard "Nerima",
``fix_names`` needed three edits where its near-miss rule allows two, and the
wrong spelling shipped. Reel B rendered "twenty twenty-six" as "9th, 2026".

Both are the same mistake — asking a recogniser what was said when we wrote
it. The recogniser is only needed for WHEN. This module keeps the timings and
throws the transcript away.
"""

from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from genlab_core.still.audio import Word as AlignedWord
from genlab_core.talk.captions import Word

logger = logging.getLogger(__name__)


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9]", "", w.lower())


def align_script(script_words: list[str], asr: list[AlignedWord]) -> list[Word]:
    """Script words, timed by their matched ASR words.

    Matching is a sequence alignment on normalised tokens, so a misheard word
    aligns positionally even though it does not match textually. Script words
    inside an unmatched run are spread evenly across that run's span — which
    is exactly right for the cases this exists for: "twenty twenty-six" heard
    as "2026" is one ASR token covering two script words, and the two should
    split its duration rather than both claim all of it.
    """
    if not asr:
        raise ValueError("no ASR words — cannot time a script without them")
    if not script_words:
        return []

    sm = SequenceMatcher(
        None, [_norm(w) for w in script_words], [_norm(w.text) for w in asr], autojunk=False
    )
    out: list[Word] = []
    exact = 0

    def spread(lo: int, hi: int, a: float, b: float) -> None:
        """Place script_words[lo:hi] evenly across [a, b]."""
        n = hi - lo
        if n <= 0:
            return
        step = max((b - a) / n, 1e-3)
        for k, i in enumerate(range(lo, hi)):
            out.append(Word(script_words[i], a + k * step, a + (k + 1) * step))

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for off in range(i2 - i1):
                a = asr[j1 + off]
                out.append(Word(script_words[i1 + off], a.start_s, a.end_s))
            exact += i2 - i1
        elif tag in ("replace", "delete", "insert"):
            # Span the ASR words this run covers. When the run has no ASR of
            # its own (an insert), borrow the gap between its neighbours.
            if j2 > j1:
                a, b = asr[j1].start_s, asr[j2 - 1].end_s
            else:
                a = out[-1].b if out else asr[0].start_s
                b = asr[j1].start_s if j1 < len(asr) else (asr[-1].end_s)
                if b <= a:
                    b = a + 0.25 * max(i2 - i1, 1)
            spread(i1, i2, a, b)

    out.sort(key=lambda w: w.a)
    # Monotonic by construction; a spread run can otherwise end after the next
    # exact word starts and make_disjoint would then see overlapping cues.
    for prev, nxt in zip(out, out[1:], strict=False):
        if prev.b > nxt.a:
            prev.b = max(prev.a + 1e-3, nxt.a)

    rate = exact / len(script_words)
    logger.info(
        "[align] %d script words timed from %d ASR words; %.0f%% matched exactly. "
        "The other %d are positioned, not transcribed — which is the point: the "
        "screen shows what we WROTE.",
        len(script_words),
        len(asr),
        rate * 100,
        len(script_words) - exact,
    )
    if rate < 0.5:
        logger.warning(
            "[align] only %.0f%% of script words matched the ASR. Timings for the "
            "rest are interpolated and may drift; check the narration is the "
            "script that was synthesised.",
            rate * 100,
        )
    return out
