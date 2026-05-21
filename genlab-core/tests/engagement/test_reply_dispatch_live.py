"""Integration tests for the full dispatch → reply → mark pipeline.

Exercises the path from _dispatch_to_dramatiq through process_reply_event
to platform client posting, with all external calls mocked.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Load the runner script (not a package)
_script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_engagement_poller.py"
_spec = importlib.util.spec_from_file_location("run_engagement_poller", str(_script_path))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_dispatch_to_dramatiq = _mod._dispatch_to_dramatiq


@pytest.fixture
def agent_root(tmp_path, monkeypatch):
    """Set AGENT_ROOT to tmp_path with a minimal persona."""
    monkeypatch.setenv("AGENT_ROOT", str(tmp_path))
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "persona.yaml").write_text(
        'name: "TestBrand"\n'
        "voice:\n"
        "  formality: 0.5\n"
        "  enthusiasm: 0.5\n"
        "  emoji_density: low\n"
        "  vocabulary: casual\n"
        "style_examples: []\n"
        "topics_to_avoid: []\n"
        "reply_constraints:\n"
        "  max_length_chars: 280\n"
        "  language: en\n"
    )
    return tmp_path


def _make_comment(**overrides):
    defaults = {
        "comment_id": "c100",
        "text": "Great video!",
        "platform": "youtube",
        "post_id": "v1",
        "author_name": "TestUser",
    }
    defaults.update(overrides)
    return defaults


class TestFullReplyPipeline:
    """End-to-end: process_reply_event generates a reply and posts it."""

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0.0)
    @patch("genlab_core.engagement.comment_processor.time")
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    def test_reply_posted_and_marked_idempotent(
        self,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_time,
        mock_delay,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import (
            _has_replied,
            _rate_limiter,
            process_reply_event,
        )
        from genlab_core.platforms.protocols import Engageable

        # Toxicity gate: clean inbound
        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_gate_cls.return_value.check_inbound.return_value = mock_result

        # Persona engine: return a reply
        mock_engine_cls.return_value.generate_reply.return_value = "Thanks for watching!"

        # Mock the unified client
        mock_client = MagicMock(spec=Engageable)
        mock_client.post_reply.return_value = True

        with (
            patch("genlab_core.platforms.get_client", return_value=mock_client),
            patch.object(_rate_limiter, "acquire", return_value=True),
        ):
            event = {
                "comment_id": "c200",
                "comment_text": "Love the content",
                "platform": "youtube",
                "niche_id": "gaming",
                "post_id": "v1",
                "post_context": "",
            }
            process_reply_event(event)

        # Reply posted via unified client
        mock_client.post_reply.assert_called_once_with(
            parent_id="c200",
            text="Thanks for watching!",
            context_id="v1",
        )

        # Idempotency: second call should be a no-op
        mock_client.post_reply.reset_mock()
        with (
            patch("genlab_core.platforms.get_client", return_value=mock_client),
            patch.object(_rate_limiter, "acquire", return_value=True),
        ):
            process_reply_event(event)
        mock_client.post_reply.assert_not_called()

        # Confirm idempotency record exists
        assert _has_replied("c200", "youtube") is True

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0.0)
    @patch("genlab_core.engagement.comment_processor.time")
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    def test_x_twitter_reply_uses_correct_client(
        self,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_time,
        mock_delay,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import (
            _rate_limiter,
            process_reply_event,
        )
        from genlab_core.platforms.protocols import Engageable

        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_gate_cls.return_value.check_inbound.return_value = mock_result
        mock_engine_cls.return_value.generate_reply.return_value = "Nice take!"

        mock_client = MagicMock(spec=Engageable)
        mock_client.post_reply.return_value = True

        with (
            patch("genlab_core.platforms.get_client", return_value=mock_client),
            patch.object(_rate_limiter, "acquire", return_value=True),
        ):
            process_reply_event(
                {
                    "comment_id": "tw123",
                    "comment_text": "Interesting thread",
                    "platform": "x_twitter",
                    "niche_id": "ai_creators",
                    "post_id": "t1",
                    "post_context": "",
                }
            )

        mock_client.post_reply.assert_called_once_with(
            parent_id="tw123",
            text="Nice take!",
            context_id="t1",
        )


class TestDispatchToProcessorIntegration:
    """_dispatch_to_dramatiq → Dramatiq actor → process_reply_event chain."""

    def test_high_priority_event_has_correct_keys(self, monkeypatch):
        """Events dispatched via high-priority actor contain all required keys."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "true")
        captured = []

        mock_high = MagicMock()
        mock_high.send.side_effect = lambda ev: captured.append(ev)
        mock_normal = MagicMock()

        with (
            patch("genlab_core.engagement.tasks.reply_to_comment_high", mock_high),
            patch("genlab_core.engagement.tasks.reply_to_comment_normal", mock_normal),
        ):
            comments = [_make_comment(text="What settings do you use?")]
            _dispatch_to_dramatiq(comments, "gaming")

        assert len(captured) == 1
        event = captured[0]
        required = {"comment_id", "comment_text", "platform", "niche_id", "post_id", "post_context"}
        assert set(event.keys()) == required
        assert event["comment_text"] == "What settings do you use?"
        assert event["niche_id"] == "gaming"

    def test_normal_priority_event_has_correct_keys(self, monkeypatch):
        """Non-question events dispatched via normal-priority actor."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "true")
        captured = []

        mock_high = MagicMock()
        mock_normal = MagicMock()
        mock_normal.send.side_effect = lambda ev: captured.append(ev)

        with (
            patch("genlab_core.engagement.tasks.reply_to_comment_high", mock_high),
            patch("genlab_core.engagement.tasks.reply_to_comment_normal", mock_normal),
        ):
            comments = [_make_comment(text="Amazing clip", comment_id="c300")]
            _dispatch_to_dramatiq(comments, "gaming")

        assert len(captured) == 1
        assert captured[0]["comment_id"] == "c300"
        mock_high.send.assert_not_called()


class TestPipelineShortCircuits:
    """Verify the pipeline stops at the correct gate for filtered comments."""

    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=True)
    def test_spam_never_reaches_toxicity(self, mock_spam, mock_gate_cls, agent_root):
        from genlab_core.engagement.comment_processor import process_reply_event

        process_reply_event(
            {
                "comment_id": "spam1",
                "comment_text": "Free followers at http://scam.xyz",
                "platform": "youtube",
                "niche_id": "gaming",
                "post_id": "v1",
                "post_context": "",
            }
        )
        mock_gate_cls.assert_not_called()

    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    def test_toxic_comment_never_reaches_persona(
        self,
        mock_spam,
        mock_engine_cls,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_gate = MagicMock()
        toxic_result = MagicMock()
        toxic_result.is_toxic = True
        toxic_result.max_dimension = "toxicity"
        toxic_result.max_score = 0.95
        mock_gate.return_value.check_inbound.return_value = toxic_result

        with patch("genlab_core.engagement.comment_processor.ToxicityGate", mock_gate):
            process_reply_event(
                {
                    "comment_id": "toxic1",
                    "comment_text": "some toxic text",
                    "platform": "youtube",
                    "niche_id": "gaming",
                    "post_id": "v1",
                    "post_context": "",
                }
            )

        mock_engine_cls.assert_not_called()
