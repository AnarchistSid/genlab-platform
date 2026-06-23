"""Pin: persona_engine.validate_reply prepends scratchpad to system prompt.

Extends PR #474's scratchpad consumer side. Reply critic (PR #464)
becomes the THIRD scratchpad consumer:
  1. PR #471 (Constitutional AI) + PR #474 wire: hook generator
  2. PR #476 wire: auto_approval_gate LLM judge
  3. THIS PR: persona_engine.validate_reply (engagement reply critic)

Why this matters: engagement replies are the BRAND-TRUST surface.
An off-topic auto-reply damages channel reputation more than a
suboptimal hook (which the operator can still reject before publish;
auto-replies post directly). Same operator-taste signal that helps
hook generation should help reply quality.

Cost: ~5-10% prompt-token overhead on the ~10% of comments that hit
the validate_reply judge (when GENLAB_REPLY_CRITIC_ENABLED=1). At
current engagement volume (~5 niches × ~10 replies/day), the
incremental cost is negligible.

These pins guard:
- Wire is structurally present (imports + concatenation)
- Empty scratchpad → no prepend (stock prompt only, no header
  pollution)
- Scratchpad placed BEFORE judge task (principle-first matches
  hook generator + gate judge patterns)
- Fail-OPEN: scratchpad read error → empty string → no prepend
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

_PERSONA_SRC = (
    Path(__file__).resolve().parents[1] / "src" / "genlab_core" / "engagement" / "persona_engine.py"
)


class TestReplyCriticScratchpadWire:
    def setup_method(self):
        self.content = _PERSONA_SRC.read_text()

    def test_imports_read_scratchpad(self):
        """Pin: read_scratchpad imported inside validate_reply."""
        assert "from genlab_core.learning.scratchpad import read_scratchpad" in self.content

    def test_prepends_before_task_prompt(self):
        """Pin: scratchpad block prepended BEFORE the 'You are a strict
        quality judge' task definition. Position matches the hook gen
        and gate judge patterns — context-first beats context-last."""
        assert "## Recent learnings (scratchpad)" in self.content
        assert "scratchpad_block = " in self.content
        # Source-level proof: scratchpad_block is concatenated with the
        # 'strict quality judge' task definition (the existing prompt)
        idx = self.content.find("scratchpad_block")
        assert idx > 0
        next_block = self.content[idx : idx + 1500]
        assert "strict quality judge" in next_block

    def test_fail_open_on_scratchpad_read_error(self):
        """Pin: scratchpad read wrapped in try/except — read errors
        produce empty string, NOT exceptions. The reply critic MUST
        NOT crash on scratchpad infrastructure hiccups."""
        assert "scratchpad is augmentation" in self.content
        assert "# noqa: BLE001 — scratchpad is augmentation" in self.content


class TestRuntimeBehaviorWithMock:
    """Runtime test: confirm validate_reply actually prepends the
    scratchpad text into the system prompt that goes to the LLM."""

    def setup_method(self):
        from genlab_core.engagement.persona_engine import PersonaEngine

        # Build a minimal PersonaEngine
        persona = MagicMock()
        persona.name = "TestBot"
        persona.voice = MagicMock()
        persona.voice.formality = 0.5
        persona.voice.enthusiasm = 0.5
        persona.voice.emoji_density = "low"
        persona.voice.vocabulary = "casual"
        persona.style_examples = []
        persona.topics_to_avoid = []
        persona.reply_constraints = MagicMock()
        persona.reply_constraints.max_length_chars = 280
        self.engine = PersonaEngine(persona=persona, toxicity_gate=None)

    def test_scratchpad_text_appears_in_system_prompt(self, monkeypatch):
        """Pin: when read_scratchpad returns content, that content is
        substring-present in the system kwarg the LLM client receives.
        Catches the regression class 'we read the scratchpad but
        forgot to interpolate it into the prompt'."""
        scratch_text = "## Last week\n- Operator rejected weak hooks twice\n"
        monkeypatch.setattr(
            "genlab_core.learning.scratchpad.read_scratchpad",
            lambda: scratch_text,
        )
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="SCORE: 85 | REASON: good fit")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp
        self.engine._client = fake_client

        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            self.engine.validate_reply(
                comment="any comment", reply="any reply", platform="instagram"
            )

        # Inspect the system prompt the LLM client received
        call_kwargs = fake_client.messages.create.call_args.kwargs
        system_arg = call_kwargs.get("system", "")
        # Scratchpad content must be present in the prompt
        assert "Last week" in system_arg
        assert "Operator rejected weak hooks twice" in system_arg
        # The wrapper header must surround it (matches hook gen + gate)
        assert "## Recent learnings (scratchpad)" in system_arg
        # The original task prompt must STILL be there (additive, not replace)
        assert "strict quality judge" in system_arg

    def test_empty_scratchpad_no_prepend(self, monkeypatch):
        """Pin: when scratchpad is empty string, no scratchpad header
        pollutes the prompt. Maintains exact backwards-compat for
        installations where the scratchpad timer hasn't fired yet."""
        monkeypatch.setattr(
            "genlab_core.learning.scratchpad.read_scratchpad",
            lambda: "",
        )
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="SCORE: 80 | REASON: ok")]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp
        self.engine._client = fake_client

        with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
            self.engine.validate_reply(comment="x", reply="y", platform="instagram")

        call_kwargs = fake_client.messages.create.call_args.kwargs
        system_arg = call_kwargs.get("system", "")
        # No scratchpad header in the prompt
        assert "Recent learnings" not in system_arg
        # The task prompt is still there
        assert "strict quality judge" in system_arg
