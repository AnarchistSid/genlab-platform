"""Tests for genlab_core.publishing.scheduling."""

import unittest
from datetime import UTC, datetime, timedelta

from genlab_core.publishing.scheduling import build_caption, is_due


class TestIsDue(unittest.TestCase):
    def test_past_time_is_due(self):
        past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        assert is_due(past) is True

    def test_future_time_not_due(self):
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        assert is_due(future) is False

    def test_z_suffix_parsed(self):
        past = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert is_due(past) is True


class TestBuildCaption(unittest.TestCase):
    def test_basic_caption(self):
        result = build_caption("Hello world", "#test #ai")
        assert "Hello world" in result
        assert "#test" in result

    def test_truncation(self):
        long_text = "x" * 3000
        result = build_caption(long_text, "#tag", max_length=200)
        assert len(result) <= 200

    def test_source_credit(self):
        result = build_caption("Caption", "#tag", source_credit="ESPN")
        assert "Source: ESPN" in result

    def test_empty_hashtags(self):
        result = build_caption("Just text", "")
        assert result == "Just text"


if __name__ == "__main__":
    unittest.main()
