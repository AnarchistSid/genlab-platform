"""Tests for engagement Dramatiq task definitions."""

import importlib
import sys

from genlab_core.engagement.tasks import (
    like_comment,
    reply_to_comment_high,
    reply_to_comment_low,
    reply_to_comment_normal,
)


def test_high_priority_actor_queue():
    """High priority actor must use engagement_high queue."""
    assert reply_to_comment_high.queue_name == "engagement_high"


def test_normal_priority_actor_queue():
    assert reply_to_comment_normal.queue_name == "engagement_normal"


def test_low_priority_actor_queue():
    assert reply_to_comment_low.queue_name == "engagement_low"


def test_like_comment_uses_normal_queue():
    """Like actions are normal priority — not high."""
    assert like_comment.queue_name == "engagement_normal"


def test_separate_actor_per_priority():
    """All three priority actors must be distinct objects."""
    assert reply_to_comment_high is not reply_to_comment_normal
    assert reply_to_comment_normal is not reply_to_comment_low


def test_tasks_import_without_redis():
    """Importing tasks.py with DRAMATIQ_TEST=1 must not require Redis."""
    for key in list(sys.modules.keys()):
        if "genlab_core.engagement.tasks" in key:
            del sys.modules[key]
    importlib.import_module("genlab_core.engagement.tasks")
