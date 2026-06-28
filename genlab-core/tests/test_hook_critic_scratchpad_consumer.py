"""Pin: hook_critic (_critique_hook_grounded) prepends scratchpad
to its system prompt.

Fourth scratchpad consumer wired:
  1. Hook generation (writing/llm_hook_generator.py) — PR #471 + #474
  2. Gate borderline judge (scheduling/auto_approval_gate.py) — PR #476
  3. Reply critic (engagement/persona_engine.py) — PR #477
  4. THIS PR: Hook critic (_critique_hook_grounded)

Why this surface matters: the hook critic evaluates *generated*
hooks for grounding — "is every proper noun in the hook actually in
the source story?". This is the EXACT failure mode that the weekly
scratchpad surfaces (e.g., "operator rejected the 2026-06-20
Toy Story 4 hook for hallucinated framing"). The same operator-
taste signal that helps generation now helps the critic spot the
issues generation may still produce.

Cost: ~5-10% prompt-token overhead on the ~10% of hook generations
that have the critic enabled (GENLAB_HOOK_CRITIC_ENABLED=1). At
~5 hooks/day × 5 niches × $0.0002/call = ~$0.005/day at most-
permissive activation. Incremental from scratchpad: ~$0.0005/day.

These pins guard:
  - Wire is structurally present (imports + concatenation)
  - Empty scratchpad → no prepend (stock prompt only)
  - Scratchpad placed BEFORE critic system prompt (principle-first)
  - Fail-OPEN: scratchpad read error → empty string → no prepend
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

_LLM_HOOK_GEN_SRC = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "genlab_core"
    / "writing"
    / "llm_hook_generator.py"
)


class TestHookCriticScratchpadWire:
    def setup_method(self):
        self.content = _LLM_HOOK_GEN_SRC.read_text()

    def test_imports_read_scratchpad_in_critic(self):
        """Pin: read_scratchpad imported inside _critique_hook_grounded.
        Lazy import keeps the critic decoupled from scratchpad module
        load-order at process startup."""
        # The scratchpad import appears within the critic function
        # alongside the existing critic try/except scaffolding.
        assert "from genlab_core.learning.scratchpad import read_scratchpad" in self.content

    def test_prepends_before_critic_system_prompt(self):
        """Pin: scratchpad block prepended BEFORE the existing
        _HOOK_CRITIC_SYSTEM_PROMPT. Principle/context-first
        ordering — matches the pattern from PRs #471, #476, #477."""
        assert "## Recent learnings (scratchpad)" in self.content
        assert "critic_system = " in self.content
        # Source-level proof: critic_system falls back to the stock
        # prompt when scratchpad is empty
        assert "else _HOOK_CRITIC_SYSTEM_PROMPT" in self.content

    def test_uses_critic_system_in_llm_call(self):
        """Pin: the messages.create() call uses the (possibly
        scratchpad-prepended) critic_system variable, NOT the raw
        _HOOK_CRITIC_SYSTEM_PROMPT directly. Without this, the wire
        would be dead — system prompt would always be the stock one.

        U-01 (2026-06-27): the literal kwarg became
        ``system=with_prompt_cache(critic_system)`` after prompt-caching
        wiring. Both forms count — assert that critic_system flows into
        the system kwarg (directly or via the cache helper)."""
        assert (
            "system=critic_system" in self.content
            or "system=with_prompt_cache(critic_system)" in self.content
        )

    def test_fail_open_on_scratchpad_read_error(self):
        """Pin: scratchpad read wrapped in try/except with the
        canonical noqa marker. The hook critic ALREADY fails-OPEN
        at the outer try; the scratchpad's inner try ensures the
        augmentation layer can't even propagate to that outer fail-
        OPEN path."""
        assert "scratchpad is augmentation" in self.content
        assert "# noqa: BLE001 — scratchpad is augmentation" in self.content


class TestRuntimeBehaviorWithMock:
    """Runtime test: confirm _critique_hook_grounded actually puts the
    scratchpad text into the system kwarg that goes to the Anthropic
    client. Catches 'read but forgot to interpolate' regressions."""

    def test_scratchpad_text_appears_in_system_prompt(self, monkeypatch):
        """Pin: when read_scratchpad returns content, that content is
        substring-present in the system kwarg the Anthropic client
        receives. With the env flag enabled and a mocked API key."""
        monkeypatch.setenv("GENLAB_HOOK_CRITIC_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        scratch_text = "## Last week\n- Operator rejected 3 movies hooks for too_generic\n"
        monkeypatch.setattr(
            "genlab_core.learning.scratchpad.read_scratchpad",
            lambda: scratch_text,
        )

        # Build a fake Anthropic response (any well-formed JSON in the
        # text payload; the critic parses {grounded, reason})
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text='{"grounded": true, "reason": "ok"}')]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        from genlab_core.writing.llm_hook_generator import _critique_hook_grounded

        with patch("anthropic.Anthropic", return_value=fake_client):
            with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                _critique_hook_grounded(
                    hook="Pixar releases Toy Story 5 trailer",
                    story={
                        "title": "Pixar drops Toy Story 5 first look",
                        "summary": "Footage shows Woody and Buzz returning",
                    },
                    niche_id="movies",
                )

        call_kwargs = fake_client.messages.create.call_args.kwargs
        system_arg = call_kwargs.get("system", "")
        # Scratchpad content must be present in the prompt
        assert "Last week" in system_arg
        assert "Operator rejected 3 movies hooks for too_generic" in system_arg
        # The wrapper header must surround it (matches other consumers)
        assert "## Recent learnings (scratchpad)" in system_arg
        # The original critic prompt MUST still be there (additive)
        assert "grounded" in system_arg.lower()

    def test_empty_scratchpad_no_prepend(self, monkeypatch):
        """Pin: empty scratchpad → no header pollution. Maintains
        exact backwards-compat for installations where the weekly
        Opus timer has not fired yet (pre-PR-#474 behavior)."""
        monkeypatch.setenv("GENLAB_HOOK_CRITIC_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        monkeypatch.setattr(
            "genlab_core.learning.scratchpad.read_scratchpad",
            lambda: "",
        )

        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text='{"grounded": true, "reason": "ok"}')]
        fake_client = MagicMock()
        fake_client.messages.create.return_value = fake_resp

        from genlab_core.writing.llm_hook_generator import _critique_hook_grounded

        with patch("anthropic.Anthropic", return_value=fake_client):
            with patch("genlab_core.intelligence.cost_accumulator.record_anthropic_usage"):
                _critique_hook_grounded(
                    hook="A hook",
                    story={"title": "a", "summary": "b"},
                    niche_id="movies",
                )

        call_kwargs = fake_client.messages.create.call_args.kwargs
        system_arg = call_kwargs.get("system", "")
        # No scratchpad header pollutes the prompt
        assert "Recent learnings" not in system_arg
        # The critic prompt is still there
        assert "grounded" in system_arg.lower()

    def test_disabled_env_skips_llm_call_entirely(self, monkeypatch):
        """Pin: when GENLAB_HOOK_CRITIC_ENABLED is unset, the function
        short-circuits BEFORE the scratchpad read. The scratchpad
        wire must NOT introduce any new behavior in the disabled
        path. Even if scratchpad read would crash, the critic still
        returns (True, 'critic_disabled')."""
        monkeypatch.delenv("GENLAB_HOOK_CRITIC_ENABLED", raising=False)

        def boom():
            raise RuntimeError("scratchpad would have crashed")

        monkeypatch.setattr("genlab_core.learning.scratchpad.read_scratchpad", boom)

        from genlab_core.writing.llm_hook_generator import _critique_hook_grounded

        grounded, reason = _critique_hook_grounded(
            hook="anything", story={"title": "x", "summary": "y"}, niche_id="x"
        )
        assert grounded is True
        assert reason == "critic_disabled"
