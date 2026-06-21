"""Pin: hook_classifier predictions flow out of generate_hook (Lever D1).

Pre-2026-06-21 ``generate_hook`` computed ``clf_score`` per candidate
to rank them, then discarded the score the instant ``best`` was picked.
The scored value lived only in a local variable inside the function;
no caller, no downstream stage, no bandit context ever saw the
HookClassifier's prediction.

Round 1 audit finding #2 flagged this as a BROKEN dormant ML — the
infrastructure trains XGBoost models, the inference pipeline runs,
the score is computed... and then dropped. Lever D1 wires the score
out via an opt-in flag and ``BaseHookStrategy`` persists it on the
story dict.

These pins lock in:

  1. ``return_classifier_score=True`` returns the winning hook's score
     in a 3-tuple alongside hook + style.
  2. Default (no flag) still returns the legacy 2-tuple / str shapes —
     backward compat for the many existing callers.
  3. No-API-key path returns ``(None, None, None)`` for the 3-tuple
     shape.
  4. ``BaseHookStrategy._generate_hook`` persists the score on
     ``story["hook_classifier_score"]`` when present.
  5. None scores are NOT persisted — downstream can use ``.get()``
     cleanly for "no signal".
  6. Legacy 2-tuple return from ``generate_hook`` (test stubs that
     haven't been updated) still works — defensive caller behavior.
"""

from __future__ import annotations

from unittest.mock import patch


class TestGenerateHookSignatures:
    """Pure signature pins — no Anthropic, no classifier. The hook
    generation flow has many failure paths; we want to test the
    NEW return-shape contract independent of API availability."""

    def test_no_api_key_returns_none_triple_when_classifier_flag_set(self, monkeypatch):
        from genlab_core.writing.llm_hook_generator import generate_hook

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = generate_hook(
            {"title": "x"},
            "ai_creators",
            return_style=True,
            return_classifier_score=True,
        )
        # New shape: 3-tuple with all Nones on the no-API-key fallback.
        assert result == (None, None, None), (
            f"Expected (None, None, None) when API key missing; got {result!r}"
        )

    def test_no_api_key_returns_2_tuple_when_only_style_flag(self, monkeypatch):
        from genlab_core.writing.llm_hook_generator import generate_hook

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = generate_hook({"title": "x"}, "ai_creators", return_style=True)
        # Legacy 2-tuple shape preserved when classifier flag absent.
        assert result == (None, None), f"Expected legacy 2-tuple; got {result!r}"

    def test_no_api_key_returns_none_when_no_flags(self, monkeypatch):
        from genlab_core.writing.llm_hook_generator import generate_hook

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = generate_hook({"title": "x"}, "ai_creators")
        assert result is None


class TestBaseHookStrategyPersistsClassifierScore:
    """The downstream consumer — ``BaseHookStrategy._generate_hook``
    calls generate_hook with both flags True and persists the score on
    the story dict so push_to_backlog (and future bandit reward stages)
    can read it. These tests patch ``generate_hook`` directly to avoid
    coupling to API/model availability."""

    def _make_strategy(self):
        """Build a minimal concrete subclass of BaseHookStrategy that
        avoids loading real niche config — tests pin the wire shape,
        not the full strategy behavior."""
        from genlab_core.strategies.base_hooks import BaseHookStrategy

        class _StubStrategy(BaseHookStrategy):
            def __init__(self):
                # Skip super().__init__ to avoid YAML load
                self._niche_id = "ai_creators"
                self._templates = {}
                self._config = {}
                self._banned: set[str] = set()
                self._story_categories = {}

            def _ensure_config(self):
                pass

            def _classify_story(self, story):
                return "default"

            def _substitute_placeholders(self, formula, story):
                # Force formula path to return nothing so LLM path is the
                # only successful return — keeps the test focused on the
                # wire we care about.
                return None

            def _is_banned(self, hook):
                return False

        return _StubStrategy()

    def test_score_persisted_on_story_when_present(self):
        story = {"title": "GPT-5 dropped"}
        with patch(
            "genlab_core.writing.llm_hook_generator.generate_hook",
            return_value=("Sample hook text", "bold_claim", 0.82),
        ):
            hook = self._make_strategy()._generate_hook(story)

        assert hook == "Sample hook text"
        assert story.get("hook_style") == "bold_claim"
        # THE pin: the score made it to the story dict.
        assert story.get("hook_classifier_score") == 0.82, (
            f"Expected hook_classifier_score=0.82 on story dict; got "
            f"{story.get('hook_classifier_score')!r}"
        )

    def test_score_not_persisted_when_none(self):
        story = {"title": "GPT-5 dropped"}
        with patch(
            "genlab_core.writing.llm_hook_generator.generate_hook",
            return_value=("Sample hook text", "bold_claim", None),
        ):
            self._make_strategy()._generate_hook(story)

        # hook_classifier_score should NOT be a key on story when score is None
        # so downstream .get() returns None cleanly without false positives.
        assert "hook_classifier_score" not in story, (
            f"Expected hook_classifier_score absent when None; got story keys: {list(story.keys())}"
        )

    def test_backward_compat_with_legacy_2_tuple_return(self):
        """If generate_hook returns the legacy 2-tuple (some test/mock
        that hasn't been updated), the strategy still works — it just
        doesn't get a classifier score on the story."""
        story = {"title": "GPT-5 dropped"}
        with patch(
            "genlab_core.writing.llm_hook_generator.generate_hook",
            return_value=("Sample hook text", "bold_claim"),
        ):
            hook = self._make_strategy()._generate_hook(story)

        assert hook == "Sample hook text"
        assert story.get("hook_style") == "bold_claim"
        assert "hook_classifier_score" not in story
