"""Tests for comment_processor — idempotency, spam, routing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def agent_root(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_ROOT", str(tmp_path))
    # Create a minimal persona.yaml
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


def _make_event(**overrides):
    defaults = {
        "comment_id": "c123",
        "comment_text": "This is a great clip!",
        "platform": "youtube",
        "niche_id": "gaming",
        "post_id": "p456",
        "post_context": "",
    }
    defaults.update(overrides)
    return defaults


class TestIdempotency:
    def test_has_replied_returns_false_when_no_file(self, agent_root):
        from genlab_core.engagement.comment_processor import _has_replied

        assert _has_replied("c123", "youtube") is False

    def test_mark_and_check_replied(self, agent_root):
        from genlab_core.engagement.comment_processor import (
            _has_replied,
            _mark_replied,
        )

        _mark_replied("c123", "youtube")
        assert _has_replied("c123", "youtube") is True
        assert _has_replied("c123", "instagram") is False
        assert _has_replied("c999", "youtube") is False

    def test_mark_replied_uses_file_locking(self, agent_root):
        from genlab_core.engagement.comment_processor import _mark_replied, _replied_set_path

        _mark_replied("c1", "yt")
        _mark_replied("c2", "yt")
        path = _replied_set_path()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2


class TestSpamSkipping:
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    def test_spam_comments_are_skipped(self, mock_gate_cls, agent_root):
        from genlab_core.engagement.comment_processor import process_reply_event

        event = _make_event(comment_text="Check my bio for free money! http://scam.com")
        process_reply_event(event)
        # Should have been filtered by spam — ToxicityGate never instantiated
        mock_gate_cls.assert_not_called()


class TestAgentRootValidation:
    def test_raises_when_agent_root_not_set(self, monkeypatch):
        monkeypatch.delenv("AGENT_ROOT", raising=False)
        from genlab_core.engagement.comment_processor import _get_agent_root

        with pytest.raises(RuntimeError, match="AGENT_ROOT"):
            _get_agent_root()


class TestLikeIdempotency:
    def test_like_key_distinct_from_reply_key(self, agent_root):
        from genlab_core.engagement.comment_processor import _has_replied, _mark_replied

        _mark_replied("c456", "instagram")
        assert not _has_replied("like:c456", "instagram")

    def test_mark_like_and_check(self, agent_root):
        from genlab_core.engagement.comment_processor import _has_replied, _mark_replied

        assert not _has_replied("like:c123", "youtube")
        _mark_replied("like:c123", "youtube")
        assert _has_replied("like:c123", "youtube")


class TestRateLimitRetry:
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    def test_rate_limit_raises_for_dramatiq_retry(self, mock_replied, mock_spam, agent_root):
        import dramatiq
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_gate = MagicMock()
        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_gate.return_value.check_inbound.return_value = mock_result

        with (
            patch("genlab_core.engagement.comment_processor.ToxicityGate", mock_gate),
            patch("genlab_core.engagement.comment_processor._rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = False
            # The processor raises dramatiq.Retry (not RuntimeError) so the
            # broker re-queues with a calibrated delay instead of letting
            # Dramatiq's default exponential backoff chase a token bucket
            # that refills on a slower cadence. See wave 2 fix in
            # comment_processor.py line ~466.
            with pytest.raises(dramatiq.Retry, match="rate_limited:youtube"):
                process_reply_event(_make_event())


class TestIdempotencyOnFailure:
    """Verify that failed API calls do NOT create false-positive idempotency records."""

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    @patch("genlab_core.engagement.comment_processor._rate_limiter")
    def test_failed_reply_does_not_mark_replied(
        self,
        mock_rl,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_delay,
        agent_root,
    ):
        """When platform client returns False (failure), _mark_replied must NOT run."""
        from genlab_core.engagement.comment_processor import _has_replied, process_reply_event

        mock_rl.acquire.return_value = True

        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_gate_cls.return_value.check_inbound.return_value = mock_result

        mock_engine_cls.return_value.generate_reply.return_value = "Test reply"

        with patch("genlab_core.engagement.comment_processor._post_reply", return_value=False):
            process_reply_event(_make_event(comment_id="fail_c1"))

        assert _has_replied("fail_c1", "youtube") is False

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    @patch("genlab_core.engagement.comment_processor._rate_limiter")
    def test_successful_reply_marks_replied(
        self,
        mock_rl,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_delay,
        agent_root,
    ):
        """When platform client returns True (success), _mark_replied MUST run."""
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_rl.acquire.return_value = True

        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_result.max_score = 0.02  # low toxicity score
        mock_gate_cls.return_value.check_inbound.return_value = mock_result

        mock_engine_cls.return_value.generate_reply.return_value = "Thanks! Glad you liked it"

        with (
            patch("genlab_core.engagement.comment_processor._post_reply", return_value=True),
            patch("genlab_core.engagement.comment_processor._mark_replied") as mock_mark,
        ):
            process_reply_event(_make_event(comment_id="ok_c1"))

        mock_mark.assert_called_once_with("ok_c1", "youtube")


class TestBacklogClientWiring:
    """Verify SharePoint integration in the reply pipeline."""

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    @patch("genlab_core.engagement.comment_processor._rate_limiter")
    def test_successful_reply_updates_sharepoint(
        self,
        mock_rl,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_delay,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_rl.acquire.return_value = True

        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_result.max_score = 0.02
        mock_gate_cls.return_value.check_inbound.return_value = mock_result
        mock_engine_cls.return_value.generate_reply.return_value = "Thanks! Glad you enjoyed it"

        mock_bl = MagicMock()
        mock_bl.write_pending_engagement.return_value = "sp-42"

        with (
            patch("genlab_core.engagement.comment_processor._post_reply", return_value=True),
            patch("genlab_core.engagement.comment_processor._mark_replied"),
            patch(
                "genlab_core.engagement.comment_processor.classify_reply_action",
                return_value="auto",
            ),
            patch(
                "genlab_core.engagement.comment_processor._get_backlog_client", return_value=mock_bl
            ),
        ):
            process_reply_event(_make_event(comment_id="bl_c1"))

        mock_bl.write_pending_engagement.assert_called_once()
        mock_bl.update_engagement_status.assert_called_once_with(
            "sp-42", "replied", reply_text="Thanks! Glad you enjoyed it [automated reply]"
        )

    @patch("genlab_core.engagement.comment_processor.human_delay", return_value=0)
    @patch("genlab_core.engagement.comment_processor.PersonaEngine")
    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=False)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    @patch("genlab_core.engagement.comment_processor._rate_limiter")
    def test_failed_reply_updates_sharepoint_as_failed(
        self,
        mock_rl,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        mock_engine_cls,
        mock_delay,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_rl.acquire.return_value = True

        mock_result = MagicMock()
        mock_result.is_toxic = False
        mock_result.max_score = 0.02
        mock_gate_cls.return_value.check_inbound.return_value = mock_result
        mock_engine_cls.return_value.generate_reply.return_value = "Thanks for the feedback!"

        mock_bl = MagicMock()
        mock_bl.write_pending_engagement.return_value = "sp-99"

        with (
            patch("genlab_core.engagement.comment_processor._post_reply", return_value=False),
            patch(
                "genlab_core.engagement.comment_processor.classify_reply_action",
                return_value="auto",
            ),
            patch(
                "genlab_core.engagement.comment_processor._get_backlog_client", return_value=mock_bl
            ),
        ):
            process_reply_event(_make_event(comment_id="fail_bl"))

        mock_bl.update_engagement_status.assert_called_once_with(
            "sp-99",
            "failed",
            error_msg="Platform API call failed",
        )

    @patch("genlab_core.engagement.comment_processor.ToxicityGate")
    @patch("genlab_core.engagement.comment_processor.is_spam", return_value=True)
    @patch("genlab_core.engagement.comment_processor._has_replied", return_value=False)
    def test_spam_updates_sharepoint_as_skipped(
        self,
        mock_replied,
        mock_spam,
        mock_gate_cls,
        agent_root,
    ):
        from genlab_core.engagement.comment_processor import process_reply_event

        mock_bl = MagicMock()
        mock_bl.write_pending_engagement.return_value = "sp-spam"

        with patch(
            "genlab_core.engagement.comment_processor._get_backlog_client", return_value=mock_bl
        ):
            process_reply_event(_make_event(comment_id="spam_c1", comment_text="FREE MONEY"))

        mock_bl.update_engagement_status.assert_called_once_with("sp-spam", "skipped")

    def test_pipeline_works_without_backlog_client(self, agent_root):
        """Pipeline runs normally when BacklogClient is not configured."""
        from genlab_core.engagement.comment_processor import process_reply_event

        with patch(
            "genlab_core.engagement.comment_processor._get_backlog_client", return_value=None
        ):
            # Spam event — should complete without errors even without BacklogClient
            process_reply_event(
                _make_event(comment_id="no_bl", comment_text="FREE MONEY http://scam.com"),
            )
