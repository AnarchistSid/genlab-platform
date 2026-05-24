"""R-76: engagement replies must carry the original post's content as context
(it was hardcoded ""), resolved post_id -> publishing_analytics -> blueprint.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.engagement import comment_processor as cp


def test_resolves_title_and_hook():
    bl = MagicMock()
    bl.publishing_analytics.all.return_value = [{"fields": {"blueprint_id": "bp123"}}]
    bl.blueprints.all.return_value = [
        {"fields": {"title": "Dune Part Three trailer", "hook": "Wild new desert footage"}}
    ]
    with patch.object(cp, "_get_backlog_client", return_value=bl):
        ctx = cp._resolve_post_context("ytAbc123", "youtube")
    assert ctx == "Dune Part Three trailer — Wild new desert footage"


def test_invalid_post_id_rejected():
    # Quotes/spaces rejected before any query (injection-safe, R-60-adjacent).
    assert cp._resolve_post_context("abc' OR '1'='1", "youtube") == ""
    assert cp._resolve_post_context("", "youtube") == ""
    assert cp._resolve_post_context("has spaces", "instagram") == ""


def test_no_backlog_client_returns_empty():
    with patch.object(cp, "_get_backlog_client", return_value=None):
        assert cp._resolve_post_context("abc123", "youtube") == ""


def test_no_matching_analytics_returns_empty():
    bl = MagicMock()
    bl.publishing_analytics.all.return_value = []
    with patch.object(cp, "_get_backlog_client", return_value=bl):
        assert cp._resolve_post_context("abc123", "youtube") == ""


def test_fail_open_on_error():
    bl = MagicMock()
    bl.publishing_analytics.all.side_effect = RuntimeError("db down")
    with patch.object(cp, "_get_backlog_client", return_value=bl):
        assert cp._resolve_post_context("abc123", "youtube") == ""


def test_title_only_when_no_hook():
    bl = MagicMock()
    bl.publishing_analytics.all.return_value = [{"fields": {"blueprint_id": "bp1"}}]
    bl.blueprints.all.return_value = [{"fields": {"title": "Just a title", "hook": ""}}]
    with patch.object(cp, "_get_backlog_client", return_value=bl):
        assert cp._resolve_post_context("abc123", "youtube") == "Just a title"
