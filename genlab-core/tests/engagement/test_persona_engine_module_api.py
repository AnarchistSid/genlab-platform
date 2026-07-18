"""Pin: module-level generate_reply + load_persona API for outbound engine.

Layer 4 outbound reply engine (2026-07-18). The runner
``scripts/run_outbound_reply_engine.py`` imports these as module-level
functions. Production 09:30 IST fire on 2026-07-18 hit
``cannot import name 'generate_reply' from 'persona_engine'`` and
posted 0 of 21 discovered targets.

These pins ensure:

1. **Module-level names exist and are importable** — the ImportError
   that broke 09:30 IST production fire cannot recur silently.
2. **load_persona signature matches caller** — ``(niche_id: str) -> NichePersona | None``
3. **generate_reply signature matches caller** — kwargs ``persona=``,
   ``comment_text=``, ``context=``
4. **Fail-open semantics** — None on persona-not-found (not raise)
5. **Context adaptation** — ``context["video_title"]`` maps to PersonaEngine's ``post_context``
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestModuleLevelAPIExists:
    """The exact ImportError that broke production must not recur."""

    def test_generate_reply_importable(self) -> None:
        from genlab_core.engagement.persona_engine import generate_reply

        assert callable(generate_reply)

    def test_load_persona_importable(self) -> None:
        from genlab_core.engagement.persona_engine import load_persona

        assert callable(load_persona)

    def test_both_from_same_import_line(self) -> None:
        """The runner does `from genlab_core.engagement.persona_engine import
        (generate_reply, load_persona)` — a single-line multi-import.
        Neither may be missing."""
        from genlab_core.engagement.persona_engine import (
            generate_reply,  # noqa: F401
            load_persona,  # noqa: F401
        )


class TestLoadPersonaSignature:
    def test_returns_none_when_persona_missing(self) -> None:
        """FileNotFoundError from the underlying resolver must become
        None — caller treats None as 'skip niche' per fail-open."""
        from genlab_core.engagement.persona_engine import load_persona

        with patch(
            "genlab_core.engagement.comment_processor._load_persona",
            side_effect=FileNotFoundError("no persona.yaml"),
        ):
            result = load_persona("gaming")
        assert result is None

    def test_returns_none_on_unexpected_exception(self) -> None:
        """Any other exception in resolution must also become None
        (fail-open)."""
        from genlab_core.engagement.persona_engine import load_persona

        with patch(
            "genlab_core.engagement.comment_processor._load_persona",
            side_effect=RuntimeError("YAML parse error"),
        ):
            result = load_persona("gaming")
        assert result is None

    def test_returns_persona_when_resolver_succeeds(self) -> None:
        from genlab_core.engagement.persona_engine import load_persona

        fake_persona = MagicMock()
        with patch(
            "genlab_core.engagement.comment_processor._load_persona",
            return_value=fake_persona,
        ):
            result = load_persona("gaming")
        assert result is fake_persona


class TestGenerateReplySignature:
    def test_accepts_kwargs_persona_comment_text_context(self) -> None:
        """The exact kwargs the runner uses at
        run_outbound_reply_engine.py:179 — persona=, comment_text=, context=.
        Positional would work in Python but the runner uses keyword-only
        so signature drift would silently break at prod runtime."""
        from genlab_core.engagement.persona_engine import generate_reply

        with patch("genlab_core.engagement.persona_engine.PersonaEngine") as mock_engine_cls:
            mock_engine_cls.return_value.generate_reply.return_value = "OK"
            fake_persona = MagicMock()
            result = generate_reply(
                persona=fake_persona,
                comment_text="This was insightful, thanks!",
                context={"video_title": "AI ethics roundup", "author_display_name": "Alice"},
            )
        assert result == "OK"

    def test_returns_none_when_persona_none(self) -> None:
        """Caller can pass a None persona (e.g. load_persona failed).
        Wrapper must not crash — return None."""
        from genlab_core.engagement.persona_engine import generate_reply

        result = generate_reply(
            persona=None,
            comment_text="hi",
            context={},
        )
        assert result is None

    def test_context_video_title_maps_to_post_context(self) -> None:
        """context['video_title'] must reach PersonaEngine.generate_reply's
        post_context= kwarg — that's what steers the LLM to reply about
        the actual video, not a generic response."""
        from genlab_core.engagement.persona_engine import generate_reply

        with patch("genlab_core.engagement.persona_engine.PersonaEngine") as mock_engine_cls:
            mock_engine = mock_engine_cls.return_value
            mock_engine.generate_reply.return_value = "reply"
            generate_reply(
                persona=MagicMock(),
                comment_text="c",
                context={"video_title": "The specific video title"},
            )

        # Assert post_context= carried the video_title
        call_kwargs = mock_engine.generate_reply.call_args.kwargs
        assert call_kwargs["post_context"] == "The specific video title"

    def test_platform_defaults_to_youtube_when_absent(self) -> None:
        """Outbound YT is the initial-ship platform. Context without
        explicit platform must default to 'youtube'."""
        from genlab_core.engagement.persona_engine import generate_reply

        with patch("genlab_core.engagement.persona_engine.PersonaEngine") as mock_engine_cls:
            mock_engine = mock_engine_cls.return_value
            mock_engine.generate_reply.return_value = "reply"
            generate_reply(
                persona=MagicMock(),
                comment_text="c",
                context={"video_title": "t"},
            )

        assert mock_engine.generate_reply.call_args.kwargs["platform"] == "youtube"

    def test_platform_overridable_via_context(self) -> None:
        """Future IG outbound will pass context['platform']='instagram'."""
        from genlab_core.engagement.persona_engine import generate_reply

        with patch("genlab_core.engagement.persona_engine.PersonaEngine") as mock_engine_cls:
            mock_engine = mock_engine_cls.return_value
            mock_engine.generate_reply.return_value = "reply"
            generate_reply(
                persona=MagicMock(),
                comment_text="c",
                context={"video_title": "t", "platform": "instagram"},
            )

        assert mock_engine.generate_reply.call_args.kwargs["platform"] == "instagram"

    def test_missing_context_treated_as_empty(self) -> None:
        """context=None is a valid input (some callers may not populate)."""
        from genlab_core.engagement.persona_engine import generate_reply

        with patch("genlab_core.engagement.persona_engine.PersonaEngine") as mock_engine_cls:
            mock_engine = mock_engine_cls.return_value
            mock_engine.generate_reply.return_value = "reply"
            result = generate_reply(
                persona=MagicMock(),
                comment_text="c",
                context=None,
            )
        assert result == "reply"
        # post_context should be empty string, not None
        assert mock_engine.generate_reply.call_args.kwargs["post_context"] == ""


class TestRunnerImportShape:
    """Structural pin — the exact import statement from the runner
    must succeed. If someone refactors persona_engine.py and removes
    either symbol, this test fires before deploy."""

    def test_runner_import_statement(self) -> None:
        """This is a copy of run_outbound_reply_engine.py:165-168 verbatim."""
        from genlab_core.engagement.persona_engine import (  # noqa: F401
            generate_reply,
            load_persona,
        )
