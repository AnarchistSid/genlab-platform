"""Pin: gate's LLM judge prepends scratchpad to system prompt.

The 2026-06-22 weekly scratchpad (PR #474) ships an Opus-synthesized
weekly reflection over the past 7 days of episodic_events. PR #474's
first consumer is the hook generator. This PR extends the consumer
side to the gate's LLM judge — the highest-stakes LLM call in the
pipeline (its verdict ships content).

Why this matters: the gate's borderline-confidence decisions get the
same 'what we learned this week' context the hook generator has. If
last week's operator rejected 8 hooks for 'too_generic', the gate's
LLM judge gets that context AND can be more skeptical of marginal
generic-looking blueprints.

These pins guard:
- Wire is structurally present (imports + concatenation)
- Empty scratchpad → uses stock prompt (no header pollution)
- Fail-OPEN: scratchpad read error → empty string → no prepend
- Scratchpad placed BEFORE judge prompt (principle-first ordering,
  matches Constitutional AI / hook generator pattern)
"""

from __future__ import annotations

from pathlib import Path

_GATE_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "genlab_core"
    / "scheduling"
    / "auto_approval_gate.py"
)


class TestGateJudgeScratchpadWire:
    def setup_method(self):
        self.content = _GATE_SRC.read_text()

    def test_imports_read_scratchpad(self):
        """Pin: read_scratchpad imported from scratchpad module."""
        assert "from genlab_core.learning.scratchpad import read_scratchpad" in self.content

    def test_prepends_before_judge_system_prompt(self):
        """Pin: when scratchpad is non-empty, it's prepended BEFORE
        the existing _LLM_JUDGE_SYSTEM_PROMPT. Order matters —
        Constitutional AI research: principle/context-first beats
        principle-last. Matches the hook generator's prepend pattern."""
        # The concatenation pattern: scratchpad block + separator + existing prompt
        assert "## Recent learnings (scratchpad)" in self.content
        # The prompt is set conditionally based on whether scratchpad is empty
        assert "judge_system = " in self.content
        # When the scratchpad is present, the system prompt is the
        # concatenation; when empty, it falls through to the stock prompt
        assert "judge_system = _LLM_JUDGE_SYSTEM_PROMPT" in self.content
        assert "_LLM_JUDGE_SYSTEM_PROMPT" in self.content

    def test_uses_judge_system_in_llm_call(self):
        """Pin: the messages.create() call uses the (possibly
        scratchpad-prepended) judge_system variable, not the raw
        _LLM_JUDGE_SYSTEM_PROMPT directly. Without this, the wire
        would be dead — the system prompt would always be the stock
        one regardless of scratchpad content.

        U-01 (2026-06-27): the literal kwarg became
        ``system=with_prompt_cache(judge_system)`` after prompt-caching
        wiring. Both forms count — assert that judge_system flows into
        the system kwarg (directly or via the cache helper)."""
        assert (
            "system=judge_system" in self.content
            or "system=with_prompt_cache(judge_system)" in self.content
        )

    def test_fail_open_on_scratchpad_read_error(self):
        """Pin: scratchpad read wrapped in try/except — read errors
        produce empty string, NOT exceptions. Gate judge MUST NEVER
        crash on scratchpad infrastructure hiccups."""
        # The canonical scratchpad-is-augmentation phrase
        assert "scratchpad is augmentation" in self.content
        # The BLE001 noqa marker on the catch-all
        assert "# noqa: BLE001 — scratchpad is augmentation" in self.content
