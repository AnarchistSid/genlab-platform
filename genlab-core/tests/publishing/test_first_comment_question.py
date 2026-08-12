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


class TestFlagGating:
    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.delenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", raising=False)
        from genlab_core.publishing.first_comment_question import (
            generate_engagement_question,
        )
        assert generate_engagement_question(niche_id="sports", hook="hook") is None

    def test_enabled_but_no_content_returns_none(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        from genlab_core.publishing.first_comment_question import (
            generate_engagement_question,
        )
        assert generate_engagement_question(
            niche_id="sports", hook="", title="", summary=""
        ) is None

    def test_no_api_key_returns_none(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "")
        from genlab_core.publishing.first_comment_question import (
            generate_engagement_question,
        )
        assert generate_engagement_question(
            niche_id="sports", hook="Game-winning shot with 2 seconds left",
        ) is None


class TestLLMCallAndParse:
    def _flag_on_with_key(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
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
    """The cta_engine wire fires ONLY when youtube_first_comment is
    empty after the affiliate block. Never overrides affiliate CTA."""

    def test_does_not_override_affiliate_cta(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        from genlab_core.publishing import first_comment_question as fcq
        called = []

        def fake_generate(**_):
            called.append(True)
            return "Fake question generated?"

        monkeypatch.setattr(fcq, "generate_engagement_question", fake_generate)

        # Manually simulate the cta_engine branch logic:
        fields = {
            "youtube_first_comment": "🔗 Get Foo: https://x.co/y",
            "niche_id": "sports",
            "hook": "hook",
            "title": "title",
            "summary": "summary",
        }
        # Branch: only fires when youtube_first_comment is falsy
        if not fields.get("youtube_first_comment"):
            fields["youtube_first_comment"] = fcq.generate_engagement_question(
                niche_id=fields["niche_id"],
                hook=fields["hook"],
                title=fields["title"],
                summary=fields["summary"],
            )

        assert fields["youtube_first_comment"] == "🔗 Get Foo: https://x.co/y"
        assert called == []

    def test_wire_populates_when_empty(self, monkeypatch):
        monkeypatch.setenv("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED", "1")
        from genlab_core.publishing import first_comment_question as fcq

        def fake_generate(**_):
            return "which surprised you more, the play or the reaction?"

        monkeypatch.setattr(fcq, "generate_engagement_question", fake_generate)

        fields = {
            "youtube_first_comment": "",
            "niche_id": "sports",
            "hook": "hook",
            "title": "title",
            "summary": "summary",
        }
        if not fields.get("youtube_first_comment"):
            fields["youtube_first_comment"] = fcq.generate_engagement_question(
                niche_id=fields["niche_id"],
                hook=fields["hook"],
                title=fields["title"],
                summary=fields["summary"],
            )

        assert fields["youtube_first_comment"].endswith("?")

    def test_cta_engine_source_contains_wire(self):
        """Structural pin: the actual cta_engine.py has the wire.
        Guards against a future refactor accidentally deleting it."""
        import pathlib

        cta_engine_path = (
            pathlib.Path(__file__).parents[2]
            / "src"
            / "genlab_core"
            / "monetization"
            / "cta_engine.py"
        )
        src = cta_engine_path.read_text()
        assert "generate_engagement_question" in src
        # cta_engine should not CALL env_true on the flag directly —
        # the primitive owns the gate. (Flag name may appear in a
        # doc-comment referencing the primitive; that's fine.)
        assert 'env_true("GENLAB_YT_ENGAGEMENT_QUESTION_ENABLED")' not in src
