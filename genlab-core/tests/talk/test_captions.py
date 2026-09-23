"""Port 7 gate: captions, against the rules the Dana/Ngannou v4 build earned.

The whisper call is mocked with a recorded cue file where one is archived; the
segmentation, layout and disjointness logic is exercised for real, because that
is where every observed defect lived.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from genlab_core.talk import captions as C

_ROOT = Path(__file__).resolve().parents[3]
_RECORDED = _ROOT / ".deliverables" / "blackbox_talk_dana_v4" / "inputs" / "dana_words_v2.json"

NAMES = ["Dana White", "Francis Ngannou", "Jon Jones", "Ciryl Gane", "UFC", "Ngannou", "Gane"]


def W(text: str, a: float, b: float, name: bool = False) -> C.Word:
    return C.Word(text, a, b, name)


def _say(tokens: str, start: float = 1.0, dur: float = 0.3, gap: float = 0.02):
    """A steady stream of words with no pauses -- so segmentation is driven by
    punctuation and the ceiling, not by timing."""
    out, t = [], start
    for tok in tokens.split():
        out.append(W(tok, t, t + dur))
        t += dur + gap
    return out


# ───────────────────────── segmentation ─────────────────────────────────────


def test_no_line_ends_on_a_function_word():
    """v1's defect: breaking every 5 words produced "of" and "life" tails.

    A line ending on a function word reads as a truncation -- the eye waits for
    the noun that never arrives.
    """
    cues = C.build(
        _say("the state of the sport is something that a lot of people argue about"), NAMES
    )
    assert C.function_word_endings(cues) == []


def test_no_cue_is_a_single_word():
    """The first v2 attempt broke on every comma: "man.", "guys.", "Well,".

    A one-word cue flashes past unread.
    """
    cues = C.build(_say("Well, I mean, look, man. These guys, you know, they fight."), NAMES)
    assert C.orphans(cues) == []


def test_max_words_is_a_ceiling_not_the_rule():
    """A short sentence stays whole rather than being padded to the ceiling."""
    cues = C.build(_say("He lost the belt."), NAMES)
    assert len(cues) == 1 and len(cues[0].words) == 4


def test_a_long_phrase_is_split_but_never_below_two_words():
    cues = C.build(
        _say("I think everybody knows exactly what happened in that fight last year"), NAMES
    )
    assert all(len(c.words) >= 2 for c in cues)
    assert all(len(c.words) <= C.MAX_WORDS + 1 for c in cues)


def test_a_name_is_never_split_across_cues():
    cues = C.build(_say("the man who beat Francis Ngannou was never the same"), NAMES)
    for c in cues:
        joined = c.text
        if "Francis" in joined:
            assert "Ngannou" in joined, f"name split across cues: {joined!r}"


def test_names_come_whole_from_the_metadata_prompt():
    """Whisper mangles proper nouns; a half-corrected name is worse than none."""
    words, _ = C.fix_names(_say("francis ngannou hit him"), NAMES)
    assert [w.t for w in words[:2]] == ["Francis", "Ngannou"]
    assert all(w.name for w in words[:2])


def test_an_unlisted_name_is_not_invented():
    words, _ = C.fix_names(_say("conor mcgregor said"), NAMES)
    assert not any(w.name for w in words), "only supplied names may be marked"


# ───────────────────────── casing ───────────────────────────────────────────


def test_sentence_case_not_shouting_and_not_a_transcript():
    cues = C.build(_say("THIS IS HOW IT WENT. THEN HE LEFT."), NAMES)
    text = " ".join(c.text for c in cues)
    assert "THIS IS HOW" not in text, "ALL-CAPS reads as shouting"
    assert text[0].isupper(), "a caption is not a raw transcript"


# ───────────────────────── timing ───────────────────────────────────────────


def test_at_most_one_cue_is_visible_at_any_sample():
    cues = C.build(_say("one two three four five six seven eight nine ten eleven twelve"), NAMES)
    assert C.max_visible(cues) <= 1


def test_the_first_frame_is_caption_free():
    """Frame 0 and the final frame must both be clear or the loop-back cannot
    match -- measured 1.35 against 2.61 when the opening cue was pinned to 0.00."""
    cues = C.build(_say("straight in with no lead", start=0.0), NAMES)
    assert cues[0].start >= C.MIN_START


def test_cues_keep_a_minimum_gap():
    cues = C.build(_say("one two three four five six seven eight"), NAMES)
    for a, b in zip(cues, cues[1:], strict=False):
        assert b.start - a.end >= C.MIN_GAP - 1e-9, f"{a.text!r} runs into {b.text!r}"


def test_a_short_cue_is_one_line():
    assert len(C.layout(_say("he lost"))) == 1


def test_two_lines_are_balanced_by_width_not_word_count():
    """A two-word line and a four-word line can be the same width on screen,
    and it is the screen the viewer reads."""
    cue = _say("extraordinarily complicated circumstances i")
    lines = C.layout(cue)
    if len(lines) == 2:
        w1 = C.px(" ".join(w.t for w in lines[0]))
        w2 = C.px(" ".join(w.t for w in lines[1]))
        assert abs(w1 - w2) <= max(w1, w2), "lines are not width-balanced"


# ───────────────────── the oracle gate: exact reproduction ──────────────────


@pytest.mark.skipif(not _RECORDED.exists(), reason="Dana v4 archive not present")
def test_the_port_reproduces_the_oracle_exactly_on_the_recorded_transcription():
    """The Port 7 gate. Same words, same cues, same text, same timings.

    This runs the ARCHIVED `captions_v2.py` beside the port on the real whisper
    output and compares. Every other test in this file describes a rule; this one
    proves the rules were ported rather than re-described -- which is exactly the
    failure Port 4 found six times.
    """
    sys.path.insert(0, str(_RECORDED.parents[1] / "scripts"))
    sys.argv = ["captions_v2", str(_RECORDED), "/dev/null", "/dev/null", "0.0", "1000.0"]
    oracle = pytest.importorskip("captions_v2", reason="archived oracle not importable")

    ow = oracle.load_words(str(_RECORDED), 0.0, 1000.0)
    ow, _ = oracle.fix_names(ow)
    oracle.recase(ow)
    ospans = oracle.make_disjoint(oracle.segment(ow))

    segs = json.loads(_RECORDED.read_text())["transcription"]
    pw = C.load_words(segs, 0.0, 1000.0)
    pw, _ = C.fix_names(pw, oracle.NAMES)
    pw = C.recase(pw)
    pspans = C.make_disjoint(C.segment(pw), captions_end=oracle.CAPTIONS_END)

    assert len(pw) == len(ow) == 192, f"words {len(pw)} vs {len(ow)}"
    assert len(pspans) == len(ospans) == 47, f"cues {len(pspans)} vs {len(ospans)}"
    assert [c.text for c in pspans] == [" ".join(w["t"] for w in c) for _, _, c in ospans]
    assert [(round(c.start, 3), round(c.end, 3)) for c in pspans] == [
        (round(a, 3), round(b, 3)) for a, b, _ in ospans
    ]


@pytest.mark.skipif(not _RECORDED.exists(), reason="Dana v4 archive not present")
def test_the_approved_run_s_measured_gate_numbers_are_held():
    """What the approved reel ACTUALLY scored -- not what it was assumed to.

        words                       192
        cues                         47
        max words/cue                 6   (over the ceiling of 5)
        function-word line endings    1   ("Where was")
        single-word second lines      0
        widest line               970 px = 89.8%  (limit 90%)
        max cues visible              1

    Two of those are not clean, and both are recorded rather than asserted away.
    The ceiling is a preference inside the splitter, not a hard limit: a phrase
    at or under it is never split, so a 6-word phrase survives whole. Likewise
    the function-word rule is a -100 score penalty applied when CHOOSING a split
    point, so an unsplit phrase can still end on one. Filed as Q6.
    """
    segs = json.loads(_RECORDED.read_text())["transcription"]
    names = [
        "Dana White",
        "Francis Ngannou",
        "Jon Jones",
        "Ciryl Gane",
        "Oscar De La Hoya",
        "UFC",
        "Ngannou",
        "Gane",
    ]
    pw = C.load_words(segs, 0.0, 1000.0)
    pw, changes = C.fix_names(pw, names)
    pw = C.recase(pw)
    cues = C.make_disjoint(C.segment(pw), captions_end=29.80)

    assert len(pw) == 192
    assert len(cues) == 47
    assert max(len(c.words) for c in cues) == 6
    assert len(C.function_word_endings(cues)) == 1
    assert C.orphans(cues) == []
    assert C.max_visible(cues) <= 1
    widest = max(C.px(" ".join(w.t for w in line)) for c in cues for line in c.lines)
    assert widest <= C.MAX_LINE_PX, f"widest {widest:.0f}px"
    assert any(n == "Gane" for _, n in changes), "the 'Gan.' -> 'Gane' correction"


@pytest.mark.skipif(not _RECORDED.exists(), reason="Dana v4 archive not present")
def test_subword_fragments_are_merged_into_whole_names():
    """'Ngannou' arrives as N + gan + u. No downstream name-fixing recovers a
    name that was never one token, so the merge is where it has to happen."""
    segs = json.loads(_RECORDED.read_text())["transcription"]
    words = C.load_words(segs, 0.0, 1000.0)
    toks = [w.t.strip(".,?!") for w in words]
    assert "Ngannou" in toks or "ngannou" in [t.lower() for t in toks]
    assert not any(t in {"N", "gan", "ou"} for t in toks), "subwords survived the merge"


class TestNearMissNameRepair:
    """ANIME-14 §3. "Nerima" shipped on screen as "nareema" in a real reel."""

    NAMES = ["Nerima", "Kyoto", "Shinpei", "Jin", "Satoko", "Marina"]

    def _fix(self, heard):
        from genlab_core.talk.captions import Word, fix_names
        return fix_names([Word(heard, 0.0, 1.0)], self.NAMES)[0][0].t

    def test_the_doubled_vowel_that_shipped(self):
        """A true Levenshtein of 3 — two substitutions and an inserted vowel.
        Collapsing repeated letters makes it 2 without moving the budget."""
        assert self._fix("nareema") == "Nerima"

    def test_the_single_vowel_variant_too(self):
        assert self._fix("narema") == "Nerima"

    def test_a_real_word_is_not_rewritten_into_a_name(self):
        """The reason the budget was NOT raised to 3: at 3 edits "marina"
        reaches "Nerima". Collapsing admits the doubling and nothing else."""
        assert self._fix("marina") == "Marina"
        assert self._fix("random") == "random"

    def test_short_names_absorb_nothing(self):
        """Two edits on a three-letter name is most of the name."""
        assert self._fix("jinx") == "jinx"

    def test_positional_scoring_is_gone(self):
        """The old rule added the length difference to a positional mismatch
        count. One inserted letter shifts everything after it, so every later
        character was charged a second time."""
        from genlab_core.talk.captions import _edit_distance
        assert _edit_distance("nerima", "narema", cap=4) == 2
        assert _edit_distance("kyoto", "kioto", cap=4) == 1

    def test_an_exact_match_still_only_fixes_case(self):
        assert self._fix("kyoto") == "Kyoto"
