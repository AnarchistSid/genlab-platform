"""The narration prompt must never ask for more than its own cap allows.

2026-08-22. `_build_narration_hint` emitted a hardcoded "2-4 sentences" at
every window size. At BB's 16s window the cap is 32 words, and 2-4 sentences of
spoken commentary is roughly 30-80 — so the prompt asked for something it
simultaneously forbade. The model obeyed the sentence instruction and blew the
cap, on attempt 1 and again on the 85% retry which inherits the same
contradiction.

Eight consecutive blueprints degraded `script_too_long` across the 08-21 and
08-22 fires before this was traced to the prompt rather than the model.

Third instance of the NARR-11 class: one contract, two implementers, allowed to
drift. There the word cap and the validator each hardcoded a speaking rate;
here the word cap and the sentence ask each hardcoded a length. These tests pin
the *relationship*, so a future window change cannot reintroduce it.
"""
from __future__ import annotations

import pathlib
import re

import pytest
import yaml

from genlab_core.publishing.narration_gate import get_narration_config
from genlab_core.writing.video_content_writer import _build_narration_hint

WORDS_PER_SENTENCE_MIN = 14
WORDS_PER_SENTENCE_TYPICAL = 17


def _cap(hint: str) -> int:
    return int(re.search(r"HARD word cap: (\d+) words", hint).group(1))


def _sentences(hint: str) -> tuple[int, int]:
    """(min, max). Short windows render "exactly N" rather than a range."""
    m = re.search(r"- (\d+)-(\d+) sentences of ORIGINAL", hint)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"- exactly (\d+) sentences? of ORIGINAL", hint)
    return int(m.group(1)), int(m.group(1))


class TestPromptIsSelfConsistent:
    @pytest.mark.parametrize("target", [10.0, 12.0, 16.0, 20.0, 22.0, 28.0, 40.0, 60.0])
    def test_requested_sentences_fit_inside_the_cap(self, target):
        """The floor of the sentence ask must be satisfiable within the cap.

        This is the exact defect: at 16s the prompt asked for a minimum of 2
        sentences (~28-40 words) against a 32-word cap, so the *minimum*
        compliant answer was already at or over the limit.
        """
        hint = _build_narration_hint(target, wpm=141, fit_margin=0.0)
        cap = _cap(hint)
        lo, hi = _sentences(hint)

        # Assert against the MIDPOINT at typical length, not the floor.
        #
        # The floor is too generous to catch the real defect: at 16s the old
        # prompt asked "2-4 sentences" against a 32-word cap, and 2 x 14 = 28
        # fits — so a floor-based assertion passed while production degraded
        # eight blueprints in a row. A model given "2-4" writes 3, at ~17 words
        # a sentence, which is 51 words against a 32-word cap. The midpoint is
        # what the model actually targets, so it is what the pin must check.
        midpoint = (lo + hi) / 2
        projected = midpoint * WORDS_PER_SENTENCE_TYPICAL
        assert projected <= cap, (
            f"at target={target}s the prompt asks for {lo}-{hi} sentences; the "
            f"midpoint ({midpoint}) at ~{WORDS_PER_SENTENCE_TYPICAL} words is "
            f"~{projected:.0f} words against a {cap}-word cap — a compliant "
            "answer to the sentence instruction violates the word cap"
        )

    @pytest.mark.parametrize("target", [16.0, 22.0, 28.0, 40.0])
    def test_upper_ask_does_not_wildly_undershoot_the_budget(self, target):
        """The converse failure: raising the window without widening the ask
        would buy headroom the model is instructed not to use."""
        hint = _build_narration_hint(target, wpm=141, fit_margin=0.0)
        cap = _cap(hint)
        _, hi = _sentences(hint)
        assert hi * WORDS_PER_SENTENCE_TYPICAL >= cap * 0.6, (
            f"at target={target}s the cap is {cap} words but the ask tops out "
            f"at {hi} sentences (~{hi * WORDS_PER_SENTENCE_TYPICAL} words) — "
            "the budget is being under-used"
        )

    def test_ask_is_always_at_least_one_sentence(self):
        """A range is preferred, but short windows legitimately collapse to a
        single sentence — offering "1-2" when only 18 words fit is the same
        overshoot this whole class is about, one size down."""
        for target in (8.0, 16.0, 28.0, 60.0):
            lo, hi = _sentences(_build_narration_hint(target, wpm=141))
            assert 1 <= lo <= hi, f"invalid sentence range {lo}-{hi} at {target}s"

    def test_sentence_ask_grows_with_the_window(self):
        prev = (0, 0)
        for target in (10.0, 16.0, 22.0, 28.0, 40.0):
            cur = _sentences(_build_narration_hint(target, wpm=141))
            assert cur >= prev, f"{target}s asks {cur}, less than the shorter window's {prev}"
            prev = cur

    def test_sentence_ask_is_derived_not_hardcoded(self):
        """Behavioural, not textual.

        The first version of this test grepped the function source for the
        literal "2-4 sentences" — and failed, because that string appears in
        the comment explaining the bug. Same trap as every other grep-the-prose
        false positive: it matched documentation, not behaviour.

        A derived range varies with the window; a hardcoded one cannot.
        """
        asks = {_sentences(_build_narration_hint(t, wpm=141)) for t in (10.0, 28.0, 60.0)}
        assert len(asks) > 1, (
            f"every window produced the same sentence ask {asks} — the range is "
            "hardcoded again and will contradict the cap at some window size"
        )


def _bb_window_seconds() -> list:
    root = pathlib.Path(__file__).resolve().parents[3]
    cfg = yaml.safe_load((root / "BlackboxBrief" / "config" / "visuals.yaml").read_text())
    found: list = []

    def walk(node):
        if isinstance(node, dict):
            if "window_seconds" in node:
                found.append(node["window_seconds"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(cfg)
    return found


class TestBBWindowRaise:
    """#226: BB 16s -> 28s."""

    def test_bb_window_is_28(self):
        found = _bb_window_seconds()
        assert found, "no window_seconds anywhere in BB visuals.yaml"
        assert 28 in found, f"expected a 28s window, found {found}"

    def test_28s_clears_the_duration_guard(self):
        """The 2026-07-09 bump to 16s existed so HighlightMoment ALONE clears
        the >=15s guard, keeping arm attribution alive when motion_compositor
        skips intro/outro. 28s must not regress that."""
        assert all(w >= 15 for w in _bb_window_seconds())

    def test_28s_gives_a_usable_narration_budget(self):
        cfg = get_narration_config(None)
        hint = _build_narration_hint(
            28.0, wpm=cfg["tts_rates"]["inworld"], fit_margin=cfg["fit_margin"]
        )
        assert _cap(hint) >= 55, f"28s should yield ~61 words, got {_cap(hint)}"
        assert _sentences(hint) == (3, 4), f"expected 3-4 at 28s, got {_sentences(hint)}"
