"""The corruption gate, pinned against a recorded corrupt stream.

Every RECORDED_CORRUPT string below came off `belt app run
anthropic/claude-haiku-4-5` on 2026-09-21 in its default wait-for-completion
mode. 7 of 10 runs on one prompt came back like this. The cause was the CLI's
stream reassembly racing a fast model, not the model and not our Python:

    belt CLI, haiku, wait-for-completion   7/10 corrupt
    MCP client (HTTP API), haiku           0/3
    belt CLI, sonnet, wait-for-completion  0/10
    belt CLI --no-wait + `task get`        0/8

fallback.py now submits with --no-wait and reads the settled task. These pins
guard the gate that sits behind that fix.
"""

from __future__ import annotations

import pytest
from genlab_core.llm import fallback as fb
from genlab_core.llm.output_integrity import (
    LLMOutputCorrupt,
    artifacts,
    assert_clean,
    is_corrupt,
    wordlist_available,
)

RECORDED_CORRUPT = [
    "The water cycle begins when solar energy ev fromaporates water oceans, lakes, and rivers",
    "Evaporation occurs when the sun's heat transforms water fromeans, lakes, and rivers",
    "When these droplets accum theseulate and become heavy enough, precipitation falls",
    "As clim this vaporbs higher, it cools and undergoes condensation",
    "A dying no her matchblewoman meets.",
    "She'll marry him if he prot Shinpei meansects her—but every word of forever.",
    "Face to face in herizes the assass chambers, Satoko recognin's intent",
    "with theest known Mariana Trench being the deep point at nearly 7 miles down",
    "Moonlight glints off steel as the assassin'sagger finds its mark— dbut Lady Akira",
]

RECORDED_CLEAN = [
    "The water cycle continuously recycles Earth's water through four key processes.",
    "Water collects in bodies of water and soil, where it either soaks into groundwater.",
    "Many deep sea creatures produce light through bioluminescence, using chemical reactions.",
    "Satoko has beauty, status, and months to live before illness claims her forever.",
    "Firefly Wedding premieres October ninth, twenty twenty-six.",
    "Condensation forms clouds; precipitation returns water to the surface as rainfall or snowfall.",
    "Shinpei infiltrates the noble estate under cover of darkness, tasked with eliminating Satoko.",
    "URGENT: Audit and fix engagement metric ingestion pipeline nested under 'data' sub-objects.",
    "Sunlight heats surface water, driving evaporation into water vapour that rises and cools.",
]


class TestDetector:
    @pytest.mark.parametrize("text", RECORDED_CORRUPT)
    def test_recorded_corruption_is_detected(self, text):
        assert is_corrupt(text), f"missed: {text!r}"

    @pytest.mark.parametrize("text", RECORDED_CLEAN)
    def test_clean_output_is_not_flagged(self, text):
        assert not is_corrupt(text), f"false positive {artifacts(text)} in {text!r}"

    def test_it_names_the_artifact_not_just_a_boolean(self):
        """An operator needs the token, not a yes/no."""
        found = artifacts("droplets accum theseulate and become heavy")
        assert "theseulate" in found

    def test_assert_clean_raises_with_the_call_site(self):
        with pytest.raises(LLMOutputCorrupt, match=r"llm_output_corrupt:writer"):
            assert_clean("water fromeans lakes", where="writer")
        assert assert_clean("water from lakes and rivers", where="writer")


class TestGateCannotSilentlyNoOp:
    """A gate that passes everything is worse than no gate."""

    def test_the_wordlist_ships_with_the_module(self):
        """/usr/share/dict/words exists on the Mac and NOT on the prod VPS.

        A system-dictionary detector would have been live in dev and silently
        dead in production — rule #17's exact shape. The list is vendored, so
        this must hold in both places.
        """
        assert wordlist_available(), "vendored wordlist missing — the gate is inoperative"

    def test_a_missing_wordlist_is_reported_not_swallowed(self, monkeypatch, caplog):
        import logging

        from genlab_core.llm import output_integrity as oi

        oi._words.cache_clear()
        monkeypatch.setattr(oi, "_WORDS_PATH", oi._WORDS_PATH.with_name("absent.gz"))
        with caplog.at_level(logging.ERROR):
            assert oi._words() == frozenset()
        oi._words.cache_clear()
        assert any("INOPERATIVE" in r.message for r in caplog.records), (
            "a missing wordlist must log at ERROR — otherwise callers believe "
            "output was checked when nothing was"
        )


class TestTransport:
    def test_belt_submits_without_waiting(self):
        """The CLI's wait-for-completion mode is what spliced the text."""
        import inspect

        src = inspect.getsource(fb.call_belt_haiku_fallback)
        assert '"--no-wait"' in src, (
            "belt must submit and then read the settled task; the CLI's "
            "wait-for-completion reassembly spliced fragments into words on "
            "7 of 10 runs"
        )
        assert '"task"' in src and '"get"' in src, "the settled task must be polled"

    def test_corrupt_output_raises_rather_than_returning(self, monkeypatch):
        class _Proc:
            def __init__(self, stdout):
                self.returncode = 0
                self.stdout = stdout
                self.stderr = ""

        corrupt = "The water cycle begins when solar energy ev fromaporates water oceans"

        def fake_run(cmd, *a, **k):
            if "run" in cmd:
                return _Proc('{"id":"t1"}')
            import json as j

            return _Proc(
                j.dumps({"status_text": "completed", "output": {"response": corrupt}})
            )

        monkeypatch.setattr("subprocess.run", fake_run)
        with pytest.raises(LLMOutputCorrupt, match="llm_output_corrupt:belt"):
            fb.call_belt_haiku_fallback("s", "u", 400, 0.7)
