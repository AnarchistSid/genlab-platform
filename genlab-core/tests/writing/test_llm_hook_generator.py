"""Tests for llm_hook_generator."""

from unittest.mock import MagicMock, patch

from genlab_core.writing.llm_hook_generator import (
    _BANNED_PHRASES,
    NICHE_STYLE,
    generate_hook,
)


def _make_story(title="Jokic drops 40 in Game 7", summary="Historic performance"):
    return {"title": title, "summary": summary}


def _mock_anthropic_success(hook_text: str):
    """Create a mock anthropic.Anthropic that returns the given hook text."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text=hook_text)]
    mock_client.messages.create.return_value = mock_response
    return MagicMock(return_value=mock_client)


class TestGenerateHook:
    """Tests for the generate_hook function."""

    def test_returns_none_when_no_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = generate_hook(_make_story(), "sports")
            assert result is None

    def test_returns_none_when_anthropic_not_installed(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict("sys.modules", {"anthropic": None}):
                result = generate_hook(_make_story(), "sports")
                assert result is None

    def test_returns_hook_on_success(self):
        mock_cls = _mock_anthropic_success("Jokic owned Game 7")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                result = generate_hook(_make_story(), "sports")
        assert result == "Jokic owned Game 7"

    def test_truncates_long_hook(self):
        long_hook = "A " * 50  # 100 chars
        mock_cls = _mock_anthropic_success(long_hook)
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                result = generate_hook(_make_story(), "sports")
        assert result is not None
        assert len(result) <= 60

    def test_rejects_banned_phrase(self):
        mock_cls = _mock_anthropic_success("This changes everything for the NBA")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                result = generate_hook(_make_story(), "sports")
        assert result is None

    def test_rejects_duplicate_hook(self):
        mock_cls = _mock_anthropic_success("Jokic owned Game 7")
        used = {"jokic owned game 7"}
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                result = generate_hook(_make_story(), "sports", used)
        assert result is None

    def test_returns_none_when_no_title(self):
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            result = generate_hook({"title": "", "summary": "something"}, "sports")
            assert result is None

    def test_returns_none_on_api_error(self):
        mock_cls = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("API down")
        mock_cls.return_value = mock_client
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                result = generate_hook(_make_story(), "sports")
        assert result is None

    def test_strips_quotes_from_response(self):
        mock_cls = _mock_anthropic_success('"Jokic owned Game 7"')
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                result = generate_hook(_make_story(), "sports")
        assert result == "Jokic owned Game 7"

    def test_uses_correct_model(self):
        mock_cls = _mock_anthropic_success("Jokic just owned Game 7")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                generate_hook(_make_story(), "sports")
        client = mock_cls.return_value
        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_falls_back_to_gaming_style_for_unknown_niche(self):
        # Note: hook must not match _BANNED_PATTERNS (the "X just <verb>"
        # lead-in template), otherwise it gets filtered out and the test
        # ends up asserting against the fallback (None).
        mock_cls = _mock_anthropic_success("Jokic owned Game 7 by himself")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                result = generate_hook(_make_story(), "unknown_niche")
        assert result == "Jokic owned Game 7 by himself"


class TestNicheStyle:
    """Tests for NICHE_STYLE config."""

    def test_all_four_niches_defined(self):
        for niche in ("sports", "movies", "anime", "gaming"):
            assert niche in NICHE_STYLE
            style = NICHE_STYLE[niche]
            assert "account" in style
            assert "voice" in style
            assert "audience" in style
            assert "example_good" in style
            assert "example_bad" in style

    def test_example_good_is_specific(self):
        """Good examples should contain proper nouns — no generic phrases."""
        for niche, style in NICHE_STYLE.items():
            words = style["example_good"].split()
            has_proper = any(
                w[0].isupper() and w not in ("The", "A", "An", "Is") for w in words if w
            )
            assert has_proper, f"{niche} example_good lacks proper nouns"


class TestBannedPhrases:
    """Tests for the banned phrases list."""

    def test_banned_phrases_are_lowercase(self):
        for phrase in _BANNED_PHRASES:
            assert phrase == phrase.lower(), f"Should be lowercase: {phrase}"

    def test_banned_phrases_not_empty(self):
        assert len(_BANNED_PHRASES) >= 10


class TestPickHookStyle:
    """Bandit-driven hook style selection (2026-05-17).

    pick_hook_style reads bandit_arms rows whose arm_id starts with
    style: and Thompson-samples among them. Returns None for cold-start
    (no arms, no client, etc.) so callers can degrade gracefully.
    """

    def test_returns_none_when_no_arms_exist(self):
        from genlab_core.writing.llm_hook_generator import pick_hook_style

        mock_client = MagicMock()
        mock_client.bandit_arms.all.return_value = []
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            assert pick_hook_style("gaming") is None

    def test_returns_none_when_proxy_missing(self):
        from genlab_core.writing.llm_hook_generator import pick_hook_style

        mock_client = MagicMock()
        mock_client.bandit_arms = None
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            assert pick_hook_style("gaming") is None

    def test_picks_style_when_arms_exist(self):
        from genlab_core.writing.llm_hook_generator import (
            _HOOK_STYLES,
            pick_hook_style,
        )

        mock_client = MagicMock()
        mock_client.bandit_arms.all.return_value = [
            {
                "id": "1",
                "fields": {
                    "arm_id": "style:gaming:question",
                    "niche_id": "gaming",
                    "alpha": 5.0,
                    "beta": 2.0,
                },
            },
            {
                "id": "2",
                "fields": {
                    "arm_id": "style:gaming:bold_claim",
                    "niche_id": "gaming",
                    "alpha": 1.0,
                    "beta": 5.0,
                },
            },
        ]
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            chosen = pick_hook_style("gaming")
        assert chosen in _HOOK_STYLES

    def test_ignores_unrecognized_arm_names(self):
        """If bandit_arms contains a style name not in _HOOK_STYLES
        (e.g. left over from a renamed style), don't return it — the
        LLM prompt template only has hints for known styles."""
        from genlab_core.writing.llm_hook_generator import pick_hook_style

        mock_client = MagicMock()
        # Only one arm, and it's an unknown style
        mock_client.bandit_arms.all.return_value = [
            {
                "id": "1",
                "fields": {
                    "arm_id": "style:gaming:mystery_genre",
                    "niche_id": "gaming",
                    "alpha": 99.0,
                    "beta": 1.0,
                },
            },
        ]
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            assert pick_hook_style("gaming") is None

    def test_only_considers_niche_scoped_arms(self):
        """Style arms from other niches must not influence this niche's
        sampling. arm_id is namespaced as style:{niche}:{name} so
        gaming's pick_hook_style only matches style:gaming:* entries.
        """
        from genlab_core.writing.llm_hook_generator import pick_hook_style

        mock_client = MagicMock()
        # gaming has a low-confidence arm; movies has a very-strong one
        mock_client.bandit_arms.all.return_value = [
            {
                "id": "1",
                "fields": {
                    "arm_id": "style:gaming:question",
                    "niche_id": "gaming",
                    "alpha": 1.0,
                    "beta": 5.0,
                },
            },
            {
                "id": "2",
                "fields": {
                    "arm_id": "style:movies:question",
                    "niche_id": "movies",
                    "alpha": 99.0,
                    "beta": 1.0,
                },
            },
        ]
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            chosen = pick_hook_style("gaming")
        assert chosen == "question"

    def test_legacy_unprefixed_style_format_still_works(self):
        """Backwards-compat: rows with arm_id "style:{name}" (no niche
        segment) should still parse correctly. Falls through the
        UNIQUE(arm_id) constraint for single-niche deployments."""
        from genlab_core.writing.llm_hook_generator import pick_hook_style

        mock_client = MagicMock()
        mock_client.bandit_arms.all.return_value = [
            {
                "id": "1",
                "fields": {
                    "arm_id": "style:question",
                    "niche_id": "gaming",
                    "alpha": 10.0,
                    "beta": 1.0,
                },
            },
        ]
        with patch(
            "genlab_core.http.backlog_client.BacklogClient",
            return_value=mock_client,
        ):
            chosen = pick_hook_style("gaming")
        assert chosen == "question"


class TestGenerateHookStyleHint:
    """Verify the LLM system prompt gets a style hint when bandit
    returns one, and verify backwards compatibility of the return
    signature."""

    def test_return_style_false_returns_plain_string(self):
        """Default behaviour must still return ``str | None``."""
        mock_cls = _mock_anthropic_success("Jokic owned Game 7")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                with patch(
                    "genlab_core.writing.llm_hook_generator.pick_hook_style",
                    return_value=None,
                ):
                    result = generate_hook(_make_story(), "sports")
        assert isinstance(result, str)
        assert result == "Jokic owned Game 7"

    def test_return_style_true_returns_tuple(self):
        mock_cls = _mock_anthropic_success("Jokic owned Game 7")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                with patch(
                    "genlab_core.writing.llm_hook_generator.pick_hook_style",
                    return_value="bold_claim",
                ):
                    result = generate_hook(
                        _make_story(),
                        "sports",
                        return_style=True,
                    )
        assert isinstance(result, tuple)
        assert result[0] == "Jokic owned Game 7"
        assert result[1] == "bold_claim"

    def test_style_hint_appears_in_system_prompt(self):
        mock_cls = _mock_anthropic_success("Jokic owned Game 7")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                with patch(
                    "genlab_core.writing.llm_hook_generator.pick_hook_style",
                    return_value="controversy",
                ):
                    generate_hook(_make_story(), "sports")

        client = mock_cls.return_value
        system_prompt = client.messages.create.call_args.kwargs["system"]
        assert "STYLE TARGET" in system_prompt
        # The body of the controversy hint should be in the prompt
        assert "tension" in system_prompt.lower() or "dispute" in system_prompt.lower()

    def test_no_style_hint_when_pick_returns_none(self):
        mock_cls = _mock_anthropic_success("Jokic owned Game 7")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                with patch(
                    "genlab_core.writing.llm_hook_generator.pick_hook_style",
                    return_value=None,
                ):
                    generate_hook(_make_story(), "sports")

        client = mock_cls.return_value
        system_prompt = client.messages.create.call_args.kwargs["system"]
        assert "STYLE TARGET" not in system_prompt

    def test_none_returned_with_style_on_api_error(self):
        """API failure with return_style=True returns (None, style)
        so the caller still knows which arm was 'attempted'."""
        mock_cls = MagicMock()
        mock_client_inst = MagicMock()
        mock_client_inst.messages.create.side_effect = RuntimeError("down")
        mock_cls.return_value = mock_client_inst
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                with patch(
                    "genlab_core.writing.llm_hook_generator.pick_hook_style",
                    return_value="revelation",
                ):
                    result = generate_hook(
                        _make_story(),
                        "sports",
                        return_style=True,
                    )
        assert result == (None, "revelation")


class TestAntiFabricationRulesInSystemPrompt:
    """PR (2026-06-20) — pin the anti-fabrication clause shipped after
    a SpliceReel blueprint generated "Does Pixar's Pressure actually fix
    what broke Toy Story 4?" for the 2025 submarine thriller *Pressure*.
    Pixar and Toy Story 4 were hallucinated; the LLM had no instruction
    forbidding it.

    These tests pin the EXACT rule strings into the system prompt so a
    future refactor can't silently remove them.
    """

    def _captured_system_prompt(self, niche_id: str = "movies") -> str:
        mock_cls = _mock_anthropic_success("a valid hook")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                with patch(
                    "genlab_core.writing.llm_hook_generator.pick_hook_style",
                    return_value=None,
                ):
                    generate_hook(_make_story(), niche_id)
        client = mock_cls.return_value
        return client.messages.create.call_args.kwargs["system"]

    def test_anti_fabrication_header_present(self):
        """The system prompt must contain the explicit anti-fabrication
        section header so the constraint is visually prominent to the LLM."""
        system = self._captured_system_prompt("movies")
        assert "STRICT ANTI-FABRICATION RULES" in system, (
            "Anti-fabrication clause missing from hook system prompt — "
            "regression risk for the SpliceReel Pixar/Toy-Story hallucination."
        )

    def test_proper_noun_grounding_rule_present(self):
        """Rule: use only proper nouns from the source story/summary."""
        system = self._captured_system_prompt("movies")
        assert "Use ONLY proper nouns that appear in the Story title or Summary" in system

    def test_sequel_invention_rule_present(self):
        """Rule: do not invent sequel numbers or franchise associations."""
        system = self._captured_system_prompt("movies")
        assert "Do NOT invent sequel numbers" in system
        # The exact "Toy Story 4" hallucination is called out as an example
        assert "Toy Story 4" in system or "Pixar" in system

    def test_anti_fabrication_present_for_all_5_niches(self):
        """The clause is niche-agnostic — must appear in every niche's prompt."""
        for niche_id in ("movies", "gaming", "sports", "anime", "ai_creators"):
            system = self._captured_system_prompt(niche_id)
            assert "STRICT ANTI-FABRICATION RULES" in system, (
                f"niche={niche_id!r} system prompt missing anti-fabrication clause"
            )
