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
        mock_cls = _mock_anthropic_success("Test hook")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                generate_hook(_make_story(), "sports")
        client = mock_cls.return_value
        call_kwargs = client.messages.create.call_args.kwargs
        assert call_kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_falls_back_to_gaming_style_for_unknown_niche(self):
        mock_cls = _mock_anthropic_success("Test hook")
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            with patch("anthropic.Anthropic", mock_cls):
                result = generate_hook(_make_story(), "unknown_niche")
        assert result == "Test hook"


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
                w[0].isupper() and w not in ("The", "A", "An", "Is")
                for w in words if w
            )
            assert has_proper, f"{niche} example_good lacks proper nouns"


class TestBannedPhrases:
    """Tests for the banned phrases list."""

    def test_banned_phrases_are_lowercase(self):
        for phrase in _BANNED_PHRASES:
            assert phrase == phrase.lower(), f"Should be lowercase: {phrase}"

    def test_banned_phrases_not_empty(self):
        assert len(_BANNED_PHRASES) >= 10
