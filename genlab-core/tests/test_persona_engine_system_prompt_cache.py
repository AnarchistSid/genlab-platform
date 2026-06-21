"""Pin: ``PersonaEngine`` builds its system prompt EXACTLY ONCE per instance.

Perf fix (2026-06-21): ``_build_system_prompt`` was called on every
``generate_reply`` invocation even though the result is a pure function
of ``self._persona``. At the comment_processor cache layer (one
PersonaEngine per niche, see ``test_comment_processor_persona_engine_cache.py``)
the system prompt now gets built exactly once per niche per worker
lifetime instead of once per reply.

These pins lock in:

  1. Constructing a ``PersonaEngine`` builds the system prompt once
     (eagerly in ``__init__``).
  2. Calling ``generate_reply`` does NOT rebuild the prompt — it reuses
     the cached ``self._system_prompt``.
  3. The cached prompt has the SAME content as the dynamic build (so
     this is a pure perf win — no behavioral change).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.engagement.persona_schema import (
    NichePersona,
    ReplyConstraints,
    VoiceConfig,
)


def _make_persona() -> NichePersona:
    return NichePersona(
        name="TestChannel",
        niche_id="gaming",
        voice=VoiceConfig(
            formality=0.2,
            enthusiasm=0.8,
            emoji_density="moderate",
            vocabulary="casual gamer slang",
        ),
        style_examples=["fire take!", "nah that's cap"],
        topics_to_avoid=["politics", "religion"],
        reply_constraints=ReplyConstraints(max_length_chars=150),
    )


class TestSystemPromptCachedOnce:
    def test_init_builds_prompt_once(self):
        """Constructing the engine builds the prompt eagerly; the cached
        attribute matches the dynamic build (no behavioral drift)."""
        from genlab_core.engagement.persona_engine import PersonaEngine

        persona = _make_persona()
        engine = PersonaEngine(persona=persona)

        # The cached prompt exists and is the same as a fresh build.
        # If the cache is ever removed or computed from the wrong
        # source, this fails immediately.
        assert hasattr(engine, "_system_prompt")
        assert engine._system_prompt == engine._build_system_prompt()
        # Sanity: the cached prompt carries the persona's name + constraints.
        assert "TestChannel" in engine._system_prompt
        assert "150" in engine._system_prompt

    @patch("genlab_core.engagement.persona_engine.ANTHROPIC_CB")
    def test_generate_reply_does_not_rebuild_prompt(self, mock_cb):
        """N calls to ``generate_reply`` must NOT trigger N calls to
        ``_build_system_prompt``. This is the perf regression pin —
        if someone reverts the change at line 88 back to
        ``system = self._build_system_prompt()``, this test fails."""
        from genlab_core.engagement.persona_engine import PersonaEngine

        # Stub the LLM round-trip so the test stays hermetic.
        fake_resp = MagicMock()
        fake_resp.content = [MagicMock(text="cool reply")]
        mock_cb.call.return_value = fake_resp

        engine = PersonaEngine(persona=_make_persona())

        # Eager init counts as 1 build call. Wrap from this point on
        # so we measure only what ``generate_reply`` does.
        # Patching the bound method post-construction works because
        # ``self._build_system_prompt`` is looked up on each call.
        with patch.object(engine, "_build_system_prompt") as spy_build:
            for _ in range(10):
                reply = engine.generate_reply("nice clip", "youtube")
                assert reply == "cool reply"

            # The pin: ZERO rebuilds during ``generate_reply`` calls.
            # The eager build at __init__ time happened BEFORE we
            # installed the spy, so spy_build.call_count tracks only
            # the per-reply path.
            assert spy_build.call_count == 0, (
                f"Expected _build_system_prompt NOT to be called per "
                f"reply (cached in __init__), got "
                f"{spy_build.call_count} calls across 10 replies."
            )

    @patch("genlab_core.engagement.persona_engine.ANTHROPIC_CB")
    def test_cached_prompt_actually_passed_to_llm(self, mock_cb):
        """The cached prompt — not some fresh build — is the value
        actually passed as ``system`` to ``client.messages.create``.
        Guards against a bug where someone caches the prompt but
        accidentally still passes a freshly-built one."""
        from genlab_core.engagement.persona_engine import PersonaEngine

        captured: dict = {}

        def _capture(fn):
            # The wrapped function builds the messages.create kwargs;
            # call it (so the test mirrors prod execution shape).
            fn()
            return MagicMock(content=[MagicMock(text="ok")])

        mock_cb.call.side_effect = _capture

        engine = PersonaEngine(persona=_make_persona())

        # Capture the actual kwargs passed to messages.create.
        with patch.object(engine, "_client", create=True) as fake_client:

            def _record_call(**kwargs):
                captured.update(kwargs)
                return MagicMock(content=[MagicMock(text="ok")])

            fake_client.messages.create.side_effect = _record_call
            engine.generate_reply("hello", "youtube")

        # The system passed to the LLM must be the cached prompt
        # (object identity check — not just equality — would be too
        # strict if the impl ever copies the string; equality suffices).
        assert captured.get("system") == engine._system_prompt
