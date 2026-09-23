"""Captions for the TALK template.

RENDER-01 Port 7. Oracle: `captions_v2.py` from the Dana/Ngannou v4 build
(CONTENT-03/04/05).

PHRASE-FIRST, AND WHY BOTH SIMPLER RULES FAILED
-----------------------------------------------
v1 broke every 5 words and produced tails like "of" and "life" -- a line ending
on a function word reads as a truncation, because the eye expects the noun that
never comes. The first v2 attempt broke on every comma instead and produced
one-word cues ("man.", "guys.", "Well,"), which flash past unread.

So: build WHOLE PHRASES from sentence ends and real pauses, split only what is
over the ceiling, and never leave a fragment under two words. `MAX_WORDS` is a
CEILING, not the segmentation rule -- that distinction is the whole fix.

TALK IS SPEECH-LED
------------------
Everything ducks to the voice here, where ACTION ducks to the music. Cue timing
comes from the words' own timestamps; nothing is placed on a grid.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_WORDS = 5  # a CEILING, not the segmentation rule
PAUSE_BREAK = 0.25  # >250 ms between words is a phrase boundary
FRAME_W = 1080
MAX_LINE_PX = 0.90 * FRAME_W
FONT_SZ = 76
CHAR_W = 0.58 * FONT_SZ
MIN_GAP = 0.040  # cue[i].end <= cue[i+1].start - 40 ms
MIN_START = 0.12  # frame 0 must be caption-free (see make_disjoint)
TAIL_PAD = 0.08

FUNCTION_WORDS = frozenset(
    """of a an the and to in with at for on that i is was it he she they we you
    my his her their but or as so if from by this these those be been""".split()
)
SENT_END = frozenset(".?!")


@dataclass
class Word:
    t: str
    a: float  # start, seconds
    b: float  # end, seconds
    name: bool = False


@dataclass
class Cue:
    words: list[Word]
    start: float = 0.0
    end: float = 0.0
    lines: list[list[Word]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.t for w in self.words)


def px(s: str) -> float:
    return len(s) * CHAR_W


def _bare(w: Word) -> str:
    return w.t.strip(".,?!").lower()


def norm(t: str) -> str:
    return re.sub(r"[^a-z]", "", t.lower())


def load_words(segments: list[dict], off: float = 0.0, end: float = 1e9) -> list[Word]:
    """Whisper segments -> WORDS, merging subword fragments and contractions.

    Whisper marks a word start with a LEADING SPACE; a fragment without one
    continues the previous token. That is what turned "Ngannou" into
    "N" + "gan" + "u", and no amount of downstream name-fixing recovers a name
    that was never one token.
    """
    raw = []
    for seg in segments:
        o = seg.get("offsets", {})
        a, b = o.get("from", 0) / 1000.0, o.get("to", 0) / 1000.0
        if b <= off or a >= end:
            continue
        raw.append({"t": seg.get("text", ""), "a": a - off, "b": b - off})

    merged: list[dict] = []
    for w in raw:
        bare = w["t"].strip()
        if not bare:
            continue
        starts_word = w["t"].startswith(" ") or not merged or bare[0] in ".,?!-\u2014"
        if not starts_word and merged:
            merged[-1]["t"] += bare
            merged[-1]["b"] = w["b"]
        else:
            merged.append({"t": bare, "a": w["a"], "b": w["b"]})

    out: list[Word] = []
    for w in merged:
        tail = w["t"].lstrip("'\u2019").lower()
        if (
            out
            and w["t"][:1] in "'\u2019"
            and tail in {"s", "t", "re", "ll", "ve", "d", "m"}
            and w["a"] - out[-1].b < 0.30
        ):
            out[-1].t += "\u2019" + tail
            out[-1].b = w["b"]
        else:
            out.append(Word(w["t"], w["a"], w["b"]))
    return out


def _edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Levenshtein, stopping once it exceeds ``cap``.

    The previous rule compared characters POSITIONALLY and added the length
    difference. That is not an edit distance: one inserted letter shifts every
    character after it, so each one counts again. Measured on the case this
    exists for, "Nerima" misheard as "nareema" — positional scoring gave 3
    (two mismatches plus a length difference) against a limit of 2, so the
    repair declined and the wrong spelling was burned into the frame. True
    Levenshtein is 2: substitute one letter, insert one.
    """
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _collapse_runs(w: str) -> str:
    """Collapse repeated letters: "nareema" -> "narema".

    ASR doubles vowels in proper nouns constantly, and each doubling costs a
    full edit. "Nerima" came back as "nareema" — a true Levenshtein of 3,
    which is over any budget safe enough to keep. Collapsed, it is 2, and the
    budget does not have to move. Raising the budget to 3 instead would admit
    "marina" for "Nerima"; this admits only the doubling.
    """
    out = []
    for ch in w:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _near_miss_budget(key: str) -> int:
    """How many edits a name of this length may absorb.

    Scaled rather than fixed. Two edits on a four-letter word is most of the
    word and starts matching unrelated tokens; two edits on a seven-letter
    proper noun is one mis-heard vowel and one inserted letter, which is
    exactly what ASR does to names.
    """
    if len(key) >= 6:
        return 2
    if len(key) >= 4:
        return 1
    return 0


def fix_names(words: list[Word], names: list[str]) -> tuple[list[Word], list[tuple[str, str]]]:
    """Restore names WHOLE from the metadata prompt, multi-word ones first.

    Multi-word first so "Dana White" wins over "Dana". The near-miss rule catches
    what whisper does to proper nouns without letting it invent: same first
    letter, length within 2, at most 2 edits. A half-corrected name is worse than
    an uncorrected one, and an invented one is worse still.
    """
    out = [Word(w.t, w.a, w.b, w.name) for w in words]
    singles = {norm(n): n for n in names if " " not in n}
    multis = [(n.split(), [norm(p) for p in n.split()]) for n in names if " " in n]
    changes: list[tuple[str, str]] = []

    i = 0
    while i < len(out):
        for parts, keys in multis:
            k = len(parts)
            if i + k <= len(out) and [norm(out[i + j].t) for j in range(k)] == keys:
                for j, p in enumerate(parts):
                    trail = out[i + j].t[len(out[i + j].t.rstrip(".,?!")) :]
                    if out[i + j].t.strip(".,?!") != p:
                        changes.append((out[i + j].t, p))
                    out[i + j].t = p + trail
                    out[i + j].name = True
                i += k - 1
                break
        i += 1

    for w in out:
        key = norm(w.t)
        trail = w.t[len(w.t.rstrip(".,?!")) :]
        if key in singles:
            if w.t != singles[key] + trail:
                changes.append((w.t, singles[key] + trail))
            w.t, w.name = singles[key] + trail, True
            continue
        best: tuple[int, str, str] | None = None
        for nk, nv in singles.items():
            if not key or nk[0] != key[0] or abs(len(nk) - len(key)) > 2:
                continue
            budget = _near_miss_budget(nk)
            if budget == 0:
                continue
            dist = min(
                _edit_distance(nk, key, cap=budget),
                _edit_distance(_collapse_runs(nk), _collapse_runs(key), cap=budget),
            )
            if 0 < dist <= budget and (best is None or dist < best[0]):
                best = (dist, nv, nk)
        if best is not None:
            # NOTE the trail is deliberately NOT re-appended here. The
            # pre-existing behaviour drops it on a near-miss repair, and the
            # approved TALK render depends on that: keeping it turned "Gan."
            # into "Gane.", which segment() reads as a sentence end, and the
            # Dana v4 transcript went from 47 cues to 48. Whether dropping it
            # is right is a separate question from the distance metric this
            # change is about, so it is left alone and filed.
            changes.append((w.t, best[1]))
            w.t, w.name = best[1], True
    return out, changes


def recase(words: list[Word]) -> list[Word]:
    """Sentence case: capitals only at real sentence starts, names, and I."""
    out = [Word(w.t, w.a, w.b, w.name) for w in words]
    sentence_start = True
    for w in out:
        core = w.t.rstrip(".,?!").replace("\u2019", "'").rstrip()
        if w.name:
            pass  # keep as written in the list
        elif core.lower() in {"i", "i'm", "i'll", "i've", "i'd"}:
            w.t = w.t[0].upper() + w.t[1:] if w.t else w.t
        elif sentence_start:
            w.t = w.t[0].upper() + w.t[1:].lower() if w.t else w.t
        else:
            w.t = w.t.lower() if not w.t.isupper() or len(w.t) > 3 else w.t
            if w.t.upper() == "UFC":
                w.t = "UFC"
        sentence_start = w.t.rstrip()[-1:] in SENT_END
    return out


def segment(words: list[Word]) -> list[list[Word]]:
    """Phrases first, then split over the ceiling, then no fragment under two."""
    phrases: list[list[Word]] = []
    cur: list[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        hard = w.t.rstrip()[-1:] in SENT_END
        pause = (i + 1 < len(words)) and (words[i + 1].a - w.b > PAUSE_BREAK)
        if hard or pause:
            phrases.append(cur)
            cur = []
    if cur:
        phrases.append(cur)

    cues: list[list[Word]] = []
    for ph in phrases:
        while len(ph) > MAX_WORDS:
            best, best_score = None, None
            for k in range(2, min(MAX_WORDS, len(ph) - 2) + 1):
                score = k  # prefer fuller lines
                if _bare(ph[k - 1]) in FUNCTION_WORDS:
                    score -= 100  # never end here
                if ph[k - 1].t.rstrip().endswith(","):
                    score += 40
                if ph[k - 1].name and ph[k].name:
                    score -= 100  # never split a name
                if best_score is None or score > best_score:
                    best, best_score = k, score
            cues.append(ph[:best])
            ph = ph[best:]
        if ph:
            cues.append(ph)

    out: list[list[Word]] = []
    for c in cues:
        if out and len(c) < 2 and len(out[-1]) + len(c) <= MAX_WORDS + 1:
            out[-1].extend(c)
        else:
            out.append(c)
    if len(out) > 1 and len(out[0]) < 2:  # a leading stub has no previous
        out[1] = out[0] + out[1]
        out.pop(0)
    return out


def layout(cue: list[Word]) -> list[list[Word]]:
    """One line if it fits, else two balanced by WIDTH -- not by word count.

    Balanced by width because a two-word line and a four-word line can be the
    same length on screen, and it is the screen the viewer reads.
    """
    if len(cue) <= MAX_WORDS and px(" ".join(w.t for w in cue)) <= MAX_LINE_PX:
        return [cue]
    best, best_cost = None, None
    for k in range(2, len(cue) - 1):  # >= 2 words each side
        l1, l2 = cue[:k], cue[k:]
        if _bare(l1[-1]) in FUNCTION_WORDS:
            continue
        if l1[-1].name and l2[0].name:
            continue  # would split a full name
        w1 = px(" ".join(x.t for x in l1))
        w2 = px(" ".join(x.t for x in l2))
        if max(w1, w2) > MAX_LINE_PX:
            continue
        cost = abs(w1 - w2)
        if best_cost is None or cost < best_cost:
            best, best_cost = (l1, l2), cost
    if best:
        return [best[0], best[1]]
    mid = max(2, min(len(cue) - 2, len(cue) // 2))
    return [cue[:mid], cue[mid:]]


def make_disjoint(cues: list[list[Word]], captions_end: float | None = None) -> list[Cue]:
    """At most ONE cue visible at any instant, and never on the first frame.

    Frame 0 and the final frame must both be caption-free or a loop-back cannot
    match -- measured 1.35 against 2.61 when the opening cue was pinned to 0.00.
    A word may start fractionally before the window, so it is clamped FORWARD to
    MIN_START rather than back to zero.
    """
    spans = [[max(MIN_START, c[0].a), max(w.b for w in c) + TAIL_PAD, c] for c in cues]
    for i in range(len(spans) - 1):
        if spans[i][1] > spans[i + 1][0] - MIN_GAP:
            spans[i][1] = spans[i + 1][0] - MIN_GAP
    if spans and captions_end is not None:
        spans[-1][1] = min(spans[-1][1], captions_end)
    out = []
    for a, b, c in spans:
        if b > a + 0.05:
            cue = Cue(words=c, start=a, end=b)
            cue.lines = layout(c)
            out.append(cue)
    return out


def build(
    words: list[Word], names: list[str] | None = None, captions_end: float | None = None
) -> list[Cue]:
    """words -> cues, ready to burn."""
    w, _ = fix_names(words, names or [])
    w = recase(w)
    return make_disjoint(segment(w), captions_end)


# ───────────────────────── gates ────────────────────────────────────────────


def function_word_endings(cues: list[Cue]) -> list[str]:
    """Lines ending on a function word. Must be empty."""
    bad = []
    for c in cues:
        for line in c.lines:
            if line and _bare(line[-1]) in FUNCTION_WORDS:
                bad.append(" ".join(w.t for w in line))
    return bad


def orphans(cues: list[Cue]) -> list[str]:
    """Cues of fewer than two words. Must be empty."""
    return [c.text for c in cues if len(c.words) < 2]


def max_visible(cues: list[Cue], step: float = 0.1) -> int:
    """Most cues on screen at any 100 ms sample. Must be <= 1."""
    if not cues:
        return 0
    t, worst = 0.0, 0
    end = max(c.end for c in cues)
    while t <= end:
        worst = max(worst, sum(1 for c in cues if c.start <= t < c.end))
        t += step
    return worst
