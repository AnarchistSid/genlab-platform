"""Pin the YouTube engagement question generator primitive + wire.

Contract:

  * Flag off (default) -> returns None; no LLM call.
  * No content context -> returns None; no LLM call.
  * No API key -> returns None; no LLM call.
  * LLM returns valid JSON with a question that:
      - has 20-200 chars
      - ends with '?'
      - doesn't match bait-pattern list
    -> returns the question
  * LLM returns anything failing those checks -> None.
  * API exception / unparseable output -> None (never raises).

Wire (cta_engine): fires ONLY when youtube_first_comment is empty
after affiliate CTA logic. Never overrides affiliate CTA.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class _MockAnthropicResponse:
    def __init__(self, text: str):
        block = MagicMock()
        block.text = text
        self.content = [block]


class TestPrimitiveIsFlagAgnostic:
    """The primitive itself has no flag check — each wire site owns
    its own per-platform flag. This lets operator graduated-rollout
    per platform without a flag-thrash on the primitive."""

    def test_no_content_returns_none(self):
        from genlab_core.publishing.first_comment_question import (
            generate_engagement_question,
        )
        assert generate_engagement_question(
            niche_id="sports", hook="", title="", summary=""
        ) is None

    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        from genlab_core.publishing.first_comment_question import (
            generate_engagement_question,
        )
        assert generate_engagement_question(
            niche_id="sports", hook="Game-winning shot with 2 seconds left",
        ) is None


class TestLLMCallAndParse:
    def _flag_on_with_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def test_valid_question_returned(self, monkeypatch):
        self._flag_on_with_key(monkeypatch)
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _MockAnthropicResponse(
            '{"question": "which one was more surprising — the shot or the reaction from the bench?"}'
        )
        mock_anthropic.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from genlab_core.publishing.first_comment_question import (
                generate_engagement_question,
            )
            result = generate_engagement_question(
                niche_id="sports",
                hook="Game-winning shot with 2 seconds left",
                title="Buzzer-beater from half-court",
                summary="Team down by 2, shot from beyond half-court to win it.",
            )
        assert result is not None
        assert result.endswith("?")
        assert 20 < len(result) < 200

    def test_short_question_rejected(self, monkeypatch):
        self._flag_on_with_key(monkeypatch)
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _MockAnthropicResponse(
            '{"question": "Cool?"}'
        )
        mock_anthropic.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from genlab_core.publishing.first_comment_question import (
                generate_engagement_question,
            )
            assert generate_engagement_question(
                niche_id="sports", hook="Buzzer beater",
            ) is None

    def test_no_question_mark_rejected(self, monkeypatch):
        self._flag_on_with_key(monkeypatch)
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _MockAnthropicResponse(
            '{"question": "This is a statement not a question that is long enough"}'
        )
        mock_anthropic.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from genlab_core.publishing.first_comment_question import (
                generate_engagement_question,
            )
            assert generate_engagement_question(
                niche_id="sports", hook="Buzzer beater",
            ) is None

    def test_bait_pattern_rejected(self, monkeypatch):
        """Explicit rejection of generic engagement-bait phrases."""
        self._flag_on_with_key(monkeypatch)
        for bad in [
            "What do you think about this shot?",
            "Do you agree with the ref's call here?",
            "Comment your thoughts below?",
        ]:
            mock_anthropic = MagicMock()
            mock_client = MagicMock()
            mock_client.messages.create.return_value = _MockAnthropicResponse(
                f'{{"question": "{bad}"}}'
            )
            mock_anthropic.Anthropic.return_value = mock_client
            with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
                from genlab_core.publishing.first_comment_question import (
                    generate_engagement_question,
                )
                assert generate_engagement_question(
                    niche_id="sports", hook="Buzzer beater",
                ) is None, f"bait pattern not rejected: {bad!r}"

    def test_unparseable_returns_none(self, monkeypatch):
        self._flag_on_with_key(monkeypatch)
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _MockAnthropicResponse(
            "definitely not JSON"
        )
        mock_anthropic.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from genlab_core.publishing.first_comment_question import (
                generate_engagement_question,
            )
            assert generate_engagement_question(
                niche_id="sports", hook="Buzzer beater",
            ) is None

    def test_markdown_fenced_json_still_parses(self, monkeypatch):
        self._flag_on_with_key(monkeypatch)
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _MockAnthropicResponse(
            '```json\n{"question": "which team surprised you more during this play?"}\n```'
        )
        mock_anthropic.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from genlab_core.publishing.first_comment_question import (
                generate_engagement_question,
            )
            result = generate_engagement_question(
                niche_id="sports", hook="Buzzer beater",
            )
        assert result is not None
        assert "?" in result

    def test_api_exception_returns_none(self, monkeypatch):
        self._flag_on_with_key(monkeypatch)
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = RuntimeError("boom")
        mock_anthropic.Anthropic.return_value = mock_client
        with patch.dict("sys.modules", {"anthropic": mock_anthropic}):
            from genlab_core.publishing.first_comment_question import (
                generate_engagement_question,
            )
            assert generate_engagement_question(
                niche_id="sports", hook="Buzzer beater",
            ) is None


class TestCTAEngineWire:
    """The cta_engine helper `_apply_engagement_question_fallback`
    fires per-platform when (a) that platform's slot is empty AND
    (b) that platform's flag is on. Never overrides affiliate CTAs.

    Caches the LLM call across the 3 platforms in a single invocation
    so we don't pay 3× the cost when all 3 flags are on."""

    _FIELDS_TEMPLATE = {
        "niche_id": "sports",
        "hook": "Buzzer beater to win Game 7",
        "title": "Game-winning shot from half-court",
        "summary": "Down by 2, shot from beyond half-court to win series.",
    }

    def _apply(self, fields, monkeypatch, mock_return="which surprised you more, the play or the reaction?"):
        from genlab_core.monetization import cta_engine
        from genlab_core.publishing import first_comment_question as fcq
        call_log = []

        def fake_generate(**kwargs):
            call_log.append(kwargs)
            return mock_return

        monkeypatch.setattr(fcq, "generate_engagement_question", fake_generate)
        cta_engine._apply_engagement_question_fallback(fields)
        return call_log

    def test_no_flags_no_llm_call(self, monkeypatch):
        # All flags off (default)
        monkeypatch.delenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        fields = dict(self._FIELDS_TEMPLATE)
        calls = self._apply(fields, monkeypatch)
        assert calls == []
        assert not fields.get("youtube_first_comment")
        assert not fields.get("instagram_first_comment")
        assert not fields.get("threads_first_comment")

    def test_yt_flag_only_populates_yt(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.delenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        fields = dict(self._FIELDS_TEMPLATE)
        self._apply(fields, monkeypatch)
        assert fields.get("youtube_first_comment", "").endswith("?")
        assert not fields.get("instagram_first_comment")
        assert not fields.get("threads_first_comment")

    def test_ig_flag_only_populates_ig(self, monkeypatch):
        monkeypatch.delenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.setenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.delenv("GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        fields = dict(self._FIELDS_TEMPLATE)
        self._apply(fields, monkeypatch)
        assert not fields.get("youtube_first_comment")
        assert fields.get("instagram_first_comment", "").endswith("?")
        assert not fields.get("threads_first_comment")

    def test_threads_flag_only_populates_threads(self, monkeypatch):
        monkeypatch.delenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.delenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        monkeypatch.setenv("GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED", "1")
        fields = dict(self._FIELDS_TEMPLATE)
        self._apply(fields, monkeypatch)
        assert not fields.get("youtube_first_comment")
        assert not fields.get("instagram_first_comment")
        assert fields.get("threads_first_comment", "").endswith("?")

    def test_all_flags_on_shares_llm_call(self, monkeypatch):
        """Cost optimization: same LLM call reused across all 3
        platforms in one invocation. LLM called ONCE for all 3."""
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED", "1")
        fields = dict(self._FIELDS_TEMPLATE)
        calls = self._apply(fields, monkeypatch)
        assert len(calls) == 1  # ← shared call
        assert fields.get("youtube_first_comment", "").endswith("?")
        assert fields.get("instagram_first_comment", "").endswith("?")
        assert fields.get("threads_first_comment", "").endswith("?")

    def test_affiliate_cta_not_overridden(self, monkeypatch):
        """When YT slot already has affiliate CTA, wire skips YT but
        still fires for empty IG + Threads (if their flags are on)."""
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED", "1")
        fields = dict(self._FIELDS_TEMPLATE)
        fields["youtube_first_comment"] = "🔗 Get Foo: https://x.co/y"
        self._apply(fields, monkeypatch)
        assert fields["youtube_first_comment"] == "🔗 Get Foo: https://x.co/y"
        assert fields.get("instagram_first_comment", "").endswith("?")

    def test_llm_returns_none_no_write(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        fields = dict(self._FIELDS_TEMPLATE)
        self._apply(fields, monkeypatch, mock_return=None)
        assert not fields.get("youtube_first_comment")

    def test_no_content_context_skips_llm(self, monkeypatch):
        """Save the LLM call when hook + title + summary are all
        empty — the primitive would return None anyway."""
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        fields = {"niche_id": "sports", "hook": "", "title": "", "summary": ""}
        calls = self._apply(fields, monkeypatch)
        assert calls == []

    def test_cta_engine_source_contains_wire(self):
        """Structural pin: the actual cta_engine.py has the wire and
        the helper function it delegates to."""
        import pathlib

        cta_engine_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "monetization"
            / "cta_engine.py"
        )
        src = cta_engine_path.read_text()
        assert "_apply_engagement_question_fallback" in src
        assert "GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED" in src
        assert "GENLAB_IG_ENGAGEMENT_QUESTION_ENABLED" in src
        assert "GENLAB_THREADS_ENGAGEMENT_QUESTION_ENABLED" in src
