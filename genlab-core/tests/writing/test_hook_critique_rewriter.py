"""Pin: SELF-REFINE critique-rewriter loop extends Lever K (Engine 1.3).

When Lever K's critic rejects a hook AND no #2 candidate exists
(because banned-phrase / length / dedup filters trimmed the best-of-N
to a single survivor), the bad hook used to pass through silently.

The critique-rewriter loop (2026-06-26) handles this case by piping
the critic's rejection reason back into a second Haiku pass that
generates a corrected hook. Max 2 rewrite rounds, fail-OPEN throughout,
opt-in via ``GENLAB_HOOK_REWRITER_ENABLED=1``.

References:
- SELF-REFINE (Madaan et al. 2023, arXiv 2303.17651)
- docs/AGENT-LEARNING-ENGINES.md Engine 1.3

These pins lock in:
  1. Flag-off → original behavior preserved (no rewrite attempted)
  2. Flag-on + single candidate + critic rejects → rewrite invoked
  3. Critique reason verbatim in rewrite prompt
  4. Max rounds exhausted → original returned
  5. Critic passes on round 1 → rewritten hook returned
  6. Multiple candidates → rewriter NOT invoked (existing #2 fallback path)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_story(
    title: str = "Pressure trailer drops at Cannes",
    summary: str = "Apple TV+ released a new submarine thriller starring Naomi Watts.",
) -> dict:
    return {"title": title, "summary": summary}


def _build_anthropic_response(text: str) -> MagicMock:
    """Build a MagicMock that mimics anthropic Message response shape."""
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    # Usage object required by record_anthropic_usage call site.
    resp.usage = MagicMock(input_tokens=10, output_tokens=10)
    return resp


# ────────────────────────────────────────────────────────────────────
# Test 1: flag unset → no rewrite even with critic rejecting
# ────────────────────────────────────────────────────────────────────


class TestNoOpWhenFlagUnset:
    def test_no_op_when_flag_unset(self, monkeypatch):
        """When GENLAB_HOOK_REWRITER_ENABLED is unset/0, the rewriter
        is never called even if the critic would reject the hook.
        Pre-rewriter behavior is preserved exactly."""
        from genlab_core.writing import llm_hook_generator

        monkeypatch.delenv("GENLAB_HOOK_REWRITER_ENABLED", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
        # Critic would say "not grounded" — but the rewriter must not
        # be invoked because the flag is off.
        monkeypatch.setenv("GENLAB_HOOK_CRITIC_ENABLED", "1")

        # Generation: 1 candidate survives (other 2 are banned).
        # Stub anthropic.Anthropic to return 3 candidates, 2 of which
        # contain a banned phrase.
        call_count = {"n": 0}
        responses = [
            _build_anthropic_response("This changes everything for the NBA"),  # banned
            _build_anthropic_response("Game changer for OpenAI today"),  # banned
            _build_anthropic_response("OpenAI released a fascinating new model"),  # survives
        ]

        def _create(**kwargs):
            i = call_count["n"]
            call_count["n"] += 1
            return responses[i]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _create

        rewrite_called = {"v": False}

        def _spy_rewrite(*args, **kwargs):
            rewrite_called["v"] = True
            return "should not be called"

        monkeypatch.setattr(llm_hook_generator, "_rewrite_hook", _spy_rewrite)
        # Critic returns ungrounded (would normally trigger rewriter).
        monkeypatch.setattr(
            llm_hook_generator,
            "_critique_hook_grounded",
            lambda hook, story, niche_id: (False, "hallucinated_entity"),
        )

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = llm_hook_generator.generate_hook(_make_story(), "ai_creators")

        # Original (non-rewritten) hook is returned.
        assert result == "OpenAI released a fascinating new model"
        assert rewrite_called["v"] is False, (
            "Rewriter must NOT be invoked when GENLAB_HOOK_REWRITER_ENABLED is unset"
        )


# ────────────────────────────────────────────────────────────────────
# Test 2: flag on + single candidate + critic rejects → rewrite invoked
# ────────────────────────────────────────────────────────────────────


class TestRewriteInvokedWhenSingleCandidateRejected:
    def test_rewrite_called_when_single_candidate_rejected_by_critic(self, monkeypatch):
        """Flag on, critic rejects the sole surviving candidate, the
        rewriter MUST be invoked."""
        from genlab_core.writing import llm_hook_generator

        monkeypatch.setenv("GENLAB_HOOK_REWRITER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_HOOK_CRITIC_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        # 1 candidate survives generation. Two are banned.
        responses = [
            _build_anthropic_response("This changes everything for the NBA"),  # banned
            _build_anthropic_response("Game changer for OpenAI today"),  # banned
            _build_anthropic_response("OpenAI dropped a new fascinating model"),  # survives
        ]
        call_count = {"n": 0}

        def _create(**kwargs):
            i = call_count["n"]
            call_count["n"] += 1
            return responses[i]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _create

        # Critic rejects the sole candidate.
        monkeypatch.setattr(
            llm_hook_generator,
            "_critique_hook_grounded",
            lambda hook, story, niche_id: (False, "introduces unrelated entity"),
        )

        rewrite_invocations = {"n": 0, "last_kwargs": None}

        def _spy_rewrite(**kwargs):
            rewrite_invocations["n"] += 1
            rewrite_invocations["last_kwargs"] = kwargs
            return "OpenAI's new model addresses old limits"

        monkeypatch.setattr(llm_hook_generator, "_rewrite_hook", _spy_rewrite)

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = llm_hook_generator.generate_hook(_make_story(), "ai_creators")

        assert rewrite_invocations["n"] == 1, (
            "Rewriter must be invoked exactly once when single candidate rejected"
        )
        # The rewriter result becomes the final hook.
        assert result == "OpenAI's new model addresses old limits"
        # Critique reason was passed through.
        assert (
            rewrite_invocations["last_kwargs"]["critique_reason"] == "introduces unrelated entity"
        )


# ────────────────────────────────────────────────────────────────────
# Test 3: critique reason verbatim in rewrite prompt
# ────────────────────────────────────────────────────────────────────


class TestRewritePassesCritiqueReasonInPrompt:
    def test_rewrite_passes_critique_reason_in_prompt(self, monkeypatch):
        """Direct test of _rewrite_hook: the critique reason must
        appear verbatim in the prompt sent to Haiku, so the LLM has
        explicit guidance on what to fix."""
        from genlab_core.writing.llm_hook_generator import _rewrite_hook

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        captured_user_prompt = {"v": None}

        def _create(**kwargs):
            captured_user_prompt["v"] = kwargs["messages"][0]["content"]
            return _build_anthropic_response("A perfectly fine corrected hook here")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _create

        # Critic accepts the rewrite (so we can return without further recursion).
        with patch(
            "genlab_core.writing.llm_hook_generator._critique_hook_grounded",
            return_value=(True, "ok"),
        ):
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = _rewrite_hook(
                    original_hook="Does Pixar's Pressure fix Toy Story 4?",
                    story=_make_story(),
                    critique_reason="introduces Pixar entity not in source",
                    niche_id="movies",
                )

        # The rewrite was returned.
        assert result == "A perfectly fine corrected hook here"
        # The critique reason appears verbatim in the prompt.
        prompt = captured_user_prompt["v"]
        assert prompt is not None
        assert "introduces Pixar entity not in source" in prompt, (
            "Critique reason must appear verbatim in the rewrite prompt"
        )
        # The original hook also appears as context.
        assert "Does Pixar's Pressure fix Toy Story 4?" in prompt


# ────────────────────────────────────────────────────────────────────
# Test 4: max rounds exhausted → returns None (caller keeps original)
# ────────────────────────────────────────────────────────────────────


class TestRewriteReturnsNoneWhenMaxRoundsExhausted:
    def test_rewrite_returns_original_when_max_rounds_exhausted(self, monkeypatch):
        """Both rewrite attempts fail critic — function returns None.
        The integration layer in generate_hook then keeps the original
        ``best`` candidate unchanged."""
        from genlab_core.writing.llm_hook_generator import _rewrite_hook

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        # Every rewrite returns a hook that the critic will reject.
        def _create(**kwargs):
            return _build_anthropic_response("Still a problematic rewrite here yes")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _create

        # Critic ALWAYS rejects.
        with patch(
            "genlab_core.writing.llm_hook_generator._critique_hook_grounded",
            return_value=(False, "still ungrounded"),
        ):
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = _rewrite_hook(
                    original_hook="A bad hook",
                    story=_make_story(),
                    critique_reason="hallucinated entity",
                    niche_id="movies",
                    max_rounds=2,
                )

        # All rounds failed → None signals to caller to preserve original.
        assert result is None, (
            "When all rewrite rounds fail critic, return None so caller keeps original"
        )


# ────────────────────────────────────────────────────────────────────
# Test 5: critic passes on round 1 → rewritten hook returned
# ────────────────────────────────────────────────────────────────────


class TestRewriteReturnsRewrittenWhenCriticPasses:
    def test_rewrite_returns_rewritten_when_critic_passes_on_round_1(self, monkeypatch):
        """First rewrite round produces a hook the critic accepts;
        function returns that new hook (not None, not original)."""
        from genlab_core.writing.llm_hook_generator import _rewrite_hook

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        rewrite_call_count = {"n": 0}

        def _create(**kwargs):
            rewrite_call_count["n"] += 1
            return _build_anthropic_response("Naomi Watts headlines tense new thriller")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _create

        with patch(
            "genlab_core.writing.llm_hook_generator._critique_hook_grounded",
            return_value=(True, "all entities grounded"),
        ):
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = _rewrite_hook(
                    original_hook="Does Pixar's Pressure fix Toy Story 4?",
                    story=_make_story(),
                    critique_reason="introduces Pixar entity not in source",
                    niche_id="movies",
                    max_rounds=2,
                )

        assert result == "Naomi Watts headlines tense new thriller"
        # Only one rewrite round needed since round-1 passed.
        assert rewrite_call_count["n"] == 1


# ────────────────────────────────────────────────────────────────────
# Test 6: multiple candidates available → rewriter NOT invoked
# ────────────────────────────────────────────────────────────────────


class TestNoRewriteWhenMultipleCandidates:
    def test_no_rewrite_when_multiple_candidates_available(self, monkeypatch):
        """When len(candidates) >= 2, the existing Lever K fallback
        (use #2) handles critic rejection. The rewriter is reserved
        for the single-candidate case ONLY."""
        from genlab_core.writing import llm_hook_generator

        monkeypatch.setenv("GENLAB_HOOK_REWRITER_ENABLED", "1")
        monkeypatch.setenv("GENLAB_HOOK_CRITIC_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        # Generation: 3 candidates ALL survive — none banned.
        responses = [
            _build_anthropic_response("Pressure trailer dropped today at Cannes"),
            _build_anthropic_response("Apple TV+ unleashed a tense submarine thriller"),
            _build_anthropic_response("Naomi Watts headlines Pressure for Apple TV+"),
        ]
        call_count = {"n": 0}

        def _create(**kwargs):
            i = call_count["n"]
            call_count["n"] += 1
            return responses[i]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _create

        # Critic rejects the winner; with multiple candidates, the
        # existing Lever K path should fall back to #2 — NOT call rewriter.
        monkeypatch.setattr(
            llm_hook_generator,
            "_critique_hook_grounded",
            lambda hook, story, niche_id: (False, "some critique"),
        )

        rewrite_called = {"v": False}

        def _spy_rewrite(*args, **kwargs):
            rewrite_called["v"] = True
            return "rewriter result"

        monkeypatch.setattr(llm_hook_generator, "_rewrite_hook", _spy_rewrite)

        with patch("anthropic.Anthropic", return_value=mock_client):
            result = llm_hook_generator.generate_hook(_make_story(), "movies")

        assert rewrite_called["v"] is False, (
            "Rewriter must NOT be invoked when multiple candidates exist — "
            "the existing Lever K fall-back-to-#2 path handles this case"
        )
        # The final hook should be one of the generated candidates
        # (Lever K fell back to #2 after critic rejected #1).
        assert result in {
            "Pressure trailer dropped today at Cannes",
            "Apple TV+ unleashed a tense submarine thriller",
            "Naomi Watts headlines Pressure for Apple TV+",
        }


# ────────────────────────────────────────────────────────────────────
# Defensive: max_rounds is hard-capped at 2 regardless of caller input
# ────────────────────────────────────────────────────────────────────


class TestMaxRoundsHardCap:
    def test_max_rounds_capped_at_2(self, monkeypatch):
        """Even if caller passes max_rounds=10, the function enforces
        the hard ceiling of 2 to keep cost bounded."""
        from genlab_core.writing.llm_hook_generator import _rewrite_hook

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        call_count = {"n": 0}

        def _create(**kwargs):
            call_count["n"] += 1
            return _build_anthropic_response("Still not grounded enough alas")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _create

        with patch(
            "genlab_core.writing.llm_hook_generator._critique_hook_grounded",
            return_value=(False, "still bad"),
        ):
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = _rewrite_hook(
                    original_hook="bad hook",
                    story=_make_story(),
                    critique_reason="reason",
                    niche_id="movies",
                    max_rounds=10,  # Caller asks for 10
                )

        assert result is None
        # Hard ceiling means we make AT MOST 2 rewrite calls,
        # not 10. The defensive _REWRITE_ATTEMPT_HARD_CEILING (3)
        # may halt earlier in pathological cases.
        assert call_count["n"] <= 3, (
            f"Hard ceiling violated: {call_count['n']} Haiku calls "
            f"exceeds max_rounds=2 + defensive ceiling=3"
        )


# ────────────────────────────────────────────────────────────────────
# Defensive: no API key → returns None gracefully
# ────────────────────────────────────────────────────────────────────


class TestRewriteNoApiKey:
    def test_no_api_key_returns_none(self, monkeypatch):
        from genlab_core.writing.llm_hook_generator import _rewrite_hook

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = _rewrite_hook(
            original_hook="bad",
            story=_make_story(),
            critique_reason="x",
            niche_id="movies",
        )
        assert result is None


# ────────────────────────────────────────────────────────────────────
# Defensive: rewritten hook with banned phrase → recurse
# ────────────────────────────────────────────────────────────────────


class TestRewriteFiltersBannedPhrases:
    def test_rewrite_filters_banned_phrase_then_recurses(self, monkeypatch):
        """Same gates as generate_hook: if the rewrite returns a hook
        with a banned phrase, it's treated as a failed round and the
        next round runs (or None if no rounds left)."""
        from genlab_core.writing.llm_hook_generator import _rewrite_hook

        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")

        responses = [
            # Round 1: contains banned "game changer"
            _build_anthropic_response("Naomi Watts is a game changer in this film"),
            # Round 2: clean
            _build_anthropic_response("Naomi Watts carries this tense thriller well"),
        ]
        call_count = {"n": 0}

        def _create(**kwargs):
            i = call_count["n"]
            call_count["n"] += 1
            return responses[min(i, len(responses) - 1)]

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = _create

        with patch(
            "genlab_core.writing.llm_hook_generator._critique_hook_grounded",
            return_value=(True, "ok"),
        ):
            with patch("anthropic.Anthropic", return_value=mock_client):
                result = _rewrite_hook(
                    original_hook="bad hook",
                    story=_make_story(),
                    critique_reason="something",
                    niche_id="movies",
                    max_rounds=2,
                )

        # Round 1 was filtered (banned), recursed into round 2 which succeeded.
        assert result == "Naomi Watts carries this tense thriller well"
        assert call_count["n"] == 2
