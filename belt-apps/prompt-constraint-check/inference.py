"""Find numeric constraints in an LLM prompt that contradict each other.

A prompt that says "HARD word cap: 32 words" and, four lines later, "2-4
sentences of original commentary" is asking for something it forbids. Two to
four sentences of prose is roughly 30-80 words. The model obeys the instruction
it finds most concrete, blows the other, and whatever validates the output
rejects it — every time, including on retry, because the retry inherits the same
contradiction.

That is a real incident, not a hypothetical: it degraded eight generations
across two days before anyone read the two lines against each other. They were
written months apart, each individually reasonable, and no test compared them
because no test knew they were related.

This app converts every numeric constraint it finds into a common unit (words)
and reports the pairs that cannot both be satisfied. It also flags the inverse —
a budget so far above the ask that most of it goes unused.

Heuristics, deliberately: prompts are prose, so the goal is to surface pairs a
human should look at, not to prove infeasibility. Every finding carries the
arithmetic that produced it so you can dismiss it in one read.
"""
import logging
import re
from typing import List, Optional

from inferencesh import BaseApp, BaseAppSetup, File
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Conversion anchors, in words. Ranges reflect ordinary prose; the midpoint is
# what a model given a range actually produces, which is the number that
# matters when predicting whether output will bust a cap.
WORDS_PER_SENTENCE = (14, 17, 22)      # (lean, typical, generous)
WORDS_PER_PARAGRAPH = (40, 60, 90)
CHARS_PER_WORD = 5.7                    # incl. trailing space
WORDS_PER_SECOND_SPEECH = 141 / 60.0    # ~141 wpm, unhurried narration


class Constraint(BaseModel):
    kind: str = Field(description="words | sentences | paragraphs | characters | seconds")
    lo: float = Field(description="Lower bound as written (equals hi when a single value).")
    hi: float = Field(description="Upper bound as written.")
    is_cap: bool = Field(description="True when phrased as a maximum rather than a target.")
    words_lo: float = Field(description="Lower bound converted to words.")
    words_hi: float = Field(description="Upper bound converted to words.")
    words_mid: float = Field(description="Midpoint in words — what a model given a range tends to produce.")
    line: int = Field(description="1-indexed line it was found on.")
    text: str = Field(description="The matched phrase.")


class Conflict(BaseModel):
    severity: str = Field(description="high | medium")
    kind: str = Field(description="contradiction | underuse")
    summary: str = Field(description="One line, with the arithmetic.")
    detail: str = Field(description="What a model will do about it.")
    line_a: int = Field(description="Line of the first constraint.")
    line_b: int = Field(description="Line of the second.")


class AppSetup(BaseAppSetup):
    """Stateless — the scan is pure text analysis."""


class RunInput(BaseModel):
    prompt: str = Field("", description="The prompt text to check. Leave empty and pass `file` instead.")
    file: Optional[File] = Field(None, description="A text file containing the prompt. Used when `prompt` is empty.")
    words_per_sentence: float = Field(
        17.0, description="Typical words per sentence for your domain. Terse marketing copy runs ~10; explanatory prose ~20.",
    )
    speech_words_per_minute: float = Field(
        141.0, description="Speaking rate, when the prompt constrains duration. 141 suits unhurried narration; 150-160 is brisk.",
    )
    underuse_ratio: float = Field(
        0.5, description="Flag when the largest ask fills less than this fraction of the cap. 0 disables the check.",
    )


class RunOutput(BaseModel):
    ok: bool = Field(description="True when no conflicts were found.")
    constraint_count: int = Field(description="How many numeric constraints were parsed.")
    conflict_count: int = Field(description="How many conflicting pairs were reported.")
    constraints: List[Constraint] = Field(description="Everything parsed, in line order.")
    conflicts: List[Conflict] = Field(description="Conflicting pairs, most severe first.")
    report: str = Field(description="Human-readable summary.")


_CAP_WORDS = r"(?:hard\s+)?(?:word\s+)?(?:cap|limit|maximum|max|no more than|at most|under|fewer than|less than|≤|<=)"

_PATTERNS = [
    # "HARD word cap: 32 words" / "no more than 60 words" / "≤ 280 characters"
    (r"(?P<cap>" + _CAP_WORDS + r")[^.\n]{0,24}?(?P<lo>\d+)\s*(?P<unit>words?|characters?|chars?|sentences?|paragraphs?|seconds?|secs?)", True),
    # "2-4 sentences" / "3 to 5 sentences" / "120-150 words"
    (r"(?P<lo>\d+)\s*(?:-|–|to)\s*(?P<hi>\d+)\s*(?P<unit>words?|characters?|chars?|sentences?|paragraphs?|seconds?|secs?)", False),
    # "exactly 2 sentences" / "write 40 words"
    (r"(?:exactly|write|produce|give me|return)\s+(?P<lo>\d+)\s*(?P<unit>words?|characters?|chars?|sentences?|paragraphs?|seconds?|secs?)", False),
]

_UNIT_CANON = {
    "word": "words", "words": "words",
    "character": "characters", "characters": "characters",
    "char": "characters", "chars": "characters",
    "sentence": "sentences", "sentences": "sentences",
    "paragraph": "paragraphs", "paragraphs": "paragraphs",
    "second": "seconds", "seconds": "seconds", "sec": "seconds", "secs": "seconds",
}


def _to_words(kind: str, value: float, wps: float, wpm: float) -> tuple:
    """(lean, typical, generous) word-equivalents for one constraint value."""
    if kind == "words":
        return (value, value, value)
    if kind == "characters":
        w = value / CHARS_PER_WORD
        return (w, w, w)
    if kind == "sentences":
        lean = WORDS_PER_SENTENCE[0] / WORDS_PER_SENTENCE[1] * wps
        gen = WORDS_PER_SENTENCE[2] / WORDS_PER_SENTENCE[1] * wps
        return (value * lean, value * wps, value * gen)
    if kind == "paragraphs":
        return tuple(value * p for p in WORDS_PER_PARAGRAPH)
    if kind == "seconds":
        w = value * (wpm / 60.0)
        return (w, w, w)
    return (value, value, value)


def parse_constraints(text: str, wps: float = 17.0, wpm: float = 141.0) -> List[Constraint]:
    out: List[Constraint] = []
    seen = set()
    for lineno, line in enumerate(text.splitlines(), start=1):
        low = line.lower()
        for pattern, is_cap in _PATTERNS:
            for m in re.finditer(pattern, low):
                unit = _UNIT_CANON.get(m.group("unit"), m.group("unit"))
                lo = float(m.group("lo"))
                hi = float(m.groupdict().get("hi") or lo)
                key = (lineno, unit, lo, hi)
                if key in seen:
                    continue
                seen.add(key)
                wl, _, _ = _to_words(unit, lo, wps, wpm)
                _, wm, _ = _to_words(unit, (lo + hi) / 2, wps, wpm)
                _, _, wh = _to_words(unit, hi, wps, wpm)
                out.append(Constraint(
                    kind=unit, lo=lo, hi=hi, is_cap=is_cap,
                    words_lo=round(wl, 1), words_hi=round(wh, 1), words_mid=round(wm, 1),
                    line=lineno, text=line.strip()[:120],
                ))
    return sorted(out, key=lambda c: c.line)


def find_conflicts(cs: List[Constraint], underuse_ratio: float = 0.5) -> List[Conflict]:
    caps = [c for c in cs if c.is_cap]
    asks = [c for c in cs if not c.is_cap]
    out: List[Conflict] = []
    for cap in caps:
        budget = cap.words_hi
        for ask in asks:
            if ask.line == cap.line:
                continue
            # Contradiction: what the model will TYPICALLY produce busts the cap.
            if ask.words_mid > budget:
                out.append(Conflict(
                    severity="high", kind="contradiction",
                    summary=(f"line {ask.line} asks for {ask.lo:g}-{ask.hi:g} {ask.kind} "
                             f"(~{ask.words_mid:g} words typical) against a "
                             f"{budget:g}-word cap on line {cap.line}"),
                    detail=("A model given a range produces near its middle, so the "
                            "typical compliant answer to this ask already exceeds the "
                            "cap. Expect rejection on the first attempt and on any "
                            "retry that keeps both instructions."),
                    line_a=ask.line, line_b=cap.line,
                ))
            elif ask.words_lo > budget:
                out.append(Conflict(
                    severity="high", kind="contradiction",
                    summary=(f"line {ask.line}'s MINIMUM ({ask.lo:g} {ask.kind}, "
                             f"~{ask.words_lo:g} words) exceeds the {budget:g}-word cap "
                             f"on line {cap.line}"),
                    detail="Even the shortest compliant answer breaks the cap.",
                    line_a=ask.line, line_b=cap.line,
                ))
            elif underuse_ratio > 0 and ask.words_hi < budget * underuse_ratio:
                out.append(Conflict(
                    severity="medium", kind="underuse",
                    summary=(f"line {ask.line} tops out at ~{ask.words_hi:g} words "
                             f"against a {budget:g}-word cap on line {cap.line} "
                             f"({ask.words_hi / budget:.0%} of budget)"),
                    detail=("The cap allows considerably more than the ask permits. If "
                            "the cap was raised to buy room, the ask needs raising too "
                            "or the extra room goes unused."),
                    line_a=ask.line, line_b=cap.line,
                ))
    order = {"high": 0, "medium": 1}
    return sorted(out, key=lambda c: (order[c.severity], c.line_a))


class App(BaseApp):
    async def setup(self, config: AppSetup):
        logger.info("prompt-constraint-check ready (stateless)")

    async def run(self, input_data: RunInput) -> RunOutput:
        text = input_data.prompt or ""
        if not text and input_data.file is not None:
            with open(input_data.file.path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        if not text.strip():
            return RunOutput(ok=True, constraint_count=0, conflict_count=0,
                             constraints=[], conflicts=[],
                             report="No prompt provided — nothing to check.")

        logger.info("checking %d bytes", len(text))
        cs = parse_constraints(text, input_data.words_per_sentence,
                               input_data.speech_words_per_minute)
        conflicts = find_conflicts(cs, input_data.underuse_ratio)
        logger.info("%d constraint(s), %d conflict(s)", len(cs), len(conflicts))

        if not cs:
            report = "No numeric constraints found — nothing to compare."
        elif not conflicts:
            report = (f"Consistent — {len(cs)} constraint(s) parsed, none in conflict.\n\n"
                      + "\n".join(f"  line {c.line}: {c.lo:g}"
                                  + (f"-{c.hi:g}" if c.hi != c.lo else "")
                                  + f" {c.kind}"
                                  + (" (cap)" if c.is_cap else "")
                                  + f"  ≈ {c.words_mid:g} words"
                                  for c in cs))
        else:
            lines = [f"{len(conflicts)} conflict(s) across {len(cs)} constraint(s).", ""]
            for c in conflicts:
                lines.append(f"  [{c.severity}] {c.kind}: {c.summary}")
                lines.append(f"      {c.detail}")
            report = "\n".join(lines)

        return RunOutput(ok=not conflicts, constraint_count=len(cs),
                         conflict_count=len(conflicts), constraints=cs,
                         conflicts=conflicts, report=report)
