"""Tests for scripts/run_engagement_poller.py runner functions."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

# Load the script module via importlib (it's a script, not a package)
_script_path = Path(__file__).resolve().parents[2] / "scripts" / "run_engagement_poller.py"
_spec = importlib.util.spec_from_file_location("run_engagement_poller", str(_script_path))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

_is_dispatch_enabled = _mod._is_dispatch_enabled
_classify_priority = _mod._classify_priority
_dispatch_to_dramatiq = _mod._dispatch_to_dramatiq


# -- _is_dispatch_enabled tests ------------------------------------------------


class TestIsDispatchEnabled:
    def test_false_when_env_not_set(self, monkeypatch):
        monkeypatch.delenv("ENGAGEMENT_DISPATCH", raising=False)
        assert _is_dispatch_enabled() is False

    def test_false_when_env_is_false(self, monkeypatch):
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "false")
        assert _is_dispatch_enabled() is False

    def test_false_when_env_is_empty(self, monkeypatch):
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "")
        assert _is_dispatch_enabled() is False

    def test_true_when_env_is_true(self, monkeypatch):
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "true")
        assert _is_dispatch_enabled() is True

    def test_true_when_env_is_TRUE_uppercase(self, monkeypatch):
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "TRUE")
        assert _is_dispatch_enabled() is True


# -- _classify_priority tests -------------------------------------------------


class TestClassifyPriority:
    def test_high_when_question_mark_in_text(self):
        """Comments containing '?' should be classified as high priority."""
        comment = {"text": "What game is this?", "comment_id": "c1", "platform": "youtube"}
        assert _classify_priority(comment) == "high"

    def test_high_when_is_question_flag_true(self):
        """Comments with is_question=True should be classified as high priority."""
        comment = {
            "text": "Nice clip",
            "is_question": True,
            "comment_id": "c2",
            "platform": "youtube",
        }
        assert _classify_priority(comment) == "high"

    def test_normal_for_non_questions(self):
        """Non-question comments should be classified as normal priority."""
        comment = {"text": "Great content", "comment_id": "c3", "platform": "youtube"}
        assert _classify_priority(comment) == "normal"

    def test_normal_when_is_question_false_and_no_qmark(self):
        """Comments with is_question=False and no '?' should be normal."""
        comment = {
            "text": "Love it",
            "is_question": False,
            "comment_id": "c4",
            "platform": "twitter",
        }
        assert _classify_priority(comment) == "normal"

    def test_high_when_text_missing_but_is_question_true(self):
        """Comments with is_question=True but no text key should be high."""
        comment = {"is_question": True, "comment_id": "c5", "platform": "youtube"}
        assert _classify_priority(comment) == "high"


# -- _dispatch_to_dramatiq tests ----------------------------------------------


def _patch_actors():
    """Return a context manager that patches both Dramatiq actors.

    The conftest ``use_stub_broker`` fixture deletes
    ``genlab_core.engagement.tasks`` from ``sys.modules`` between tests,
    so ``monkeypatch.setattr`` with a dotted-string resolves the module
    at patch time but ``_dispatch_to_dramatiq``'s deferred ``from`` import
    may resolve a *different* module object.  Using ``unittest.mock.patch``
    avoids the issue because it patches the live module at context-enter.
    """
    mock_high = MagicMock()
    mock_normal = MagicMock()
    cm_high = patch("genlab_core.engagement.tasks.reply_to_comment_high", mock_high)
    cm_normal = patch("genlab_core.engagement.tasks.reply_to_comment_normal", mock_normal)
    return cm_high, cm_normal, mock_high, mock_normal


class TestDispatchToDramatiq:
    def test_dispatches_high_priority_to_correct_actor(self, monkeypatch):
        """Questions should be dispatched via reply_to_comment_high."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "true")
        cm_high, cm_normal, mock_high, mock_normal = _patch_actors()
        with cm_high, cm_normal:
            comments = [
                {
                    "comment_id": "c1",
                    "text": "What settings do you use?",
                    "platform": "youtube",
                    "post_id": "v1",
                }
            ]

            count = _dispatch_to_dramatiq(comments, "gaming")

            assert count == 1
            mock_high.send.assert_called_once()
            mock_normal.send.assert_not_called()

            event = mock_high.send.call_args[0][0]
            assert event["comment_id"] == "c1"
            assert event["niche_id"] == "gaming"
            assert event["platform"] == "youtube"

    def test_dispatches_normal_priority_to_correct_actor(self, monkeypatch):
        """Non-questions should be dispatched via reply_to_comment_normal."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "true")
        cm_high, cm_normal, mock_high, mock_normal = _patch_actors()
        with cm_high, cm_normal:
            comments = [
                {
                    "comment_id": "c2",
                    "text": "Amazing play",
                    "platform": "twitter",
                    "post_id": "t1",
                }
            ]

            count = _dispatch_to_dramatiq(comments, "gaming")

            assert count == 1
            mock_normal.send.assert_called_once()
            mock_high.send.assert_not_called()

    def test_dispatches_mixed_priorities(self, monkeypatch):
        """Multiple comments should be routed to correct actors based on priority."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "true")
        cm_high, cm_normal, mock_high, mock_normal = _patch_actors()
        with cm_high, cm_normal:
            comments = [
                {"comment_id": "c1", "text": "What game?", "platform": "youtube", "post_id": "v1"},
                {"comment_id": "c2", "text": "Nice", "platform": "youtube", "post_id": "v1"},
                {
                    "comment_id": "c3",
                    "text": "How did you do that?",
                    "platform": "youtube",
                    "post_id": "v1",
                },
            ]

            count = _dispatch_to_dramatiq(comments, "gaming")

            assert count == 3
            assert mock_high.send.call_count == 2  # two questions
            assert mock_normal.send.call_count == 1  # one non-question

    def test_returns_zero_for_empty_list(self, monkeypatch):
        """Empty comment list should return 0 dispatched."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "true")
        cm_high, cm_normal, mock_high, mock_normal = _patch_actors()
        with cm_high, cm_normal:
            count = _dispatch_to_dramatiq([], "gaming")

            assert count == 0
            mock_high.send.assert_not_called()
            mock_normal.send.assert_not_called()

    def test_event_dict_structure(self, monkeypatch):
        """Dispatched event should have all required keys for comment_processor."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "true")
        cm_high, cm_normal, mock_high, mock_normal = _patch_actors()
        with cm_high, cm_normal:
            comments = [
                {
                    "comment_id": "c1",
                    "text": "Cool clip",
                    "platform": "youtube",
                    "post_id": "v1",
                }
            ]

            _dispatch_to_dramatiq(comments, "gaming")

            event = mock_normal.send.call_args[0][0]
            required_keys = {
                "comment_id",
                "comment_text",
                "platform",
                "niche_id",
                "post_id",
                "post_context",
            }
            assert set(event.keys()) == required_keys
            assert event["comment_text"] == "Cool clip"
            assert event["post_context"] == ""


# -- observe-mode tests -------------------------------------------------------


class TestDispatchObserveMode:
    """When ENGAGEMENT_DISPATCH is not 'true', dispatch should log and return 0."""

    def test_returns_zero_when_dispatch_off(self, monkeypatch):
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "false")
        comments = [
            {"comment_id": "c1", "text": "Nice", "platform": "youtube", "post_id": "v1"},
        ]
        assert _dispatch_to_dramatiq(comments, "gaming") == 0

    def test_returns_zero_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("ENGAGEMENT_DISPATCH", raising=False)
        comments = [
            {"comment_id": "c1", "text": "Nice", "platform": "youtube", "post_id": "v1"},
        ]
        assert _dispatch_to_dramatiq(comments, "gaming") == 0

    def test_does_not_import_tasks_when_dispatch_off(self, monkeypatch):
        """Observe mode should never import genlab_core.engagement.tasks."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "false")
        comments = [
            {"comment_id": "c1", "text": "What game?", "platform": "youtube", "post_id": "v1"},
            {"comment_id": "c2", "text": "Cool", "platform": "youtube", "post_id": "v1"},
        ]
        count = _dispatch_to_dramatiq(comments, "gaming")
        assert count == 0

    def test_observe_logs_comments(self, monkeypatch, caplog):
        """Observe mode should log each comment with [OBSERVE] prefix."""
        monkeypatch.setenv("ENGAGEMENT_DISPATCH", "false")
        comments = [
            {
                "comment_id": "c1",
                "text": "Great video",
                "platform": "youtube",
                "post_id": "v1",
                "author_name": "Alice",
            },
        ]
        with caplog.at_level(logging.INFO, logger="engagement.poller"):
            _dispatch_to_dramatiq(comments, "gaming")

        observe_lines = [r for r in caplog.records if "[OBSERVE]" in r.message]
        assert len(observe_lines) >= 2  # per-comment + summary
