"""R-50: to_sentence_case must fix lowercase sentence starts WITHOUT damaging
proper nouns / acronyms, and must be idempotent.
"""

from __future__ import annotations

import pytest
from genlab_core.writing.text_case import to_sentence_case


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        # Leading lowercase -> capitalized.
        ("wait so claude is smarter", "Wait so claude is smarter"),
        # Already cased -> unchanged.
        ("Wait so claude is smarter", "Wait so claude is smarter"),
        # Multi-sentence: each start capitalized, after ". ".
        ("the meta shifted. competitive got wild", "The meta shifted. Competitive got wild"),
        # Acronyms / proper nouns mid-sentence are preserved (additive-only).
        ("AI is great. claude too", "AI is great. Claude too"),
        ("MKBHD just dropped a video", "MKBHD just dropped a video"),
        # Leading emoji / non-letter: first letter still capitalized.
        ("🔥 wait this is wild", "🔥 Wait this is wild"),
        # "." not followed by whitespace is NOT a sentence boundary.
        ("example.com is down", "Example.com is down"),
        # Repeated terminal punctuation: boundary is the one followed by space.
        ("what?? really though", "What?? Really though"),
    ],
)
def test_sentence_case_cases(raw: str, expected: str) -> None:
    assert to_sentence_case(raw) == expected


def test_never_lowercases_existing_capitals() -> None:
    # The transform only promotes lowercase -> uppercase, never the reverse.
    text = "OpenAI shipped GPT. NASA watched LG closely"
    out = to_sentence_case(text)
    for token in ("OpenAI", "GPT", "NASA", "LG"):
        assert token in out


def test_idempotent() -> None:
    samples = [
        "wait so claude is smarter than its trainers??",
        "the {game} community is eating rn",
        "🔥 sora made a full broadcast. we're cooked",
    ]
    for s in samples:
        once = to_sentence_case(s)
        assert to_sentence_case(once) == once


def test_fixes_template_formula_lowercase() -> None:
    # R-51: the gaming degraded-mode formulas were all-lowercase.
    assert to_sentence_case("the Fortnite community is eating rn").startswith("The ")
