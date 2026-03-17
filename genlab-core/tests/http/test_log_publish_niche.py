"""Sprint 42 — Test log_publish_result niche_id field handling (Fix 1B)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from genlab_core.http.backlog_client import BacklogClient
from genlab_core.storage.factory import reset_backends


def _make_client_with_mock_analytics() -> tuple[BacklogClient, MagicMock]:
    """Create a BacklogClient without hitting Azure/SharePoint.

    We bypass __init__ entirely and wire up only publishing_analytics.
    The mock_analytics proxy is registered in _sp_proxies so the
    _backend() routing works correctly.
    """
    reset_backends()
    client = object.__new__(BacklogClient)
    mock_analytics = MagicMock()
    # .all() returns [] so log_publish_result takes the .create() path
    mock_analytics.all.return_value = []
    mock_analytics.create.return_value = {"id": "rec_new"}
    client.publishing_analytics = mock_analytics
    client._sp_proxies = {"Publishing_Analytics": mock_analytics}
    return client, mock_analytics


class TestLogPublishResultNicheId:
    """Verify niche_id is conditionally included in the fields dict."""

    def test_log_publish_result_includes_niche_id_when_provided(self):
        client, mock_analytics = _make_client_with_mock_analytics()

        client.log_publish_result(
            candidate_id="cand-100",
            platform="instagram",
            status="SUCCESS",
            niche_id="gaming",
        )

        # The backend.create is called with (table, fields, typecast=True)
        create_calls = mock_analytics.create.call_args_list
        assert len(create_calls) == 1
        fields = create_calls[0][0][0]
        assert fields["niche_id"] == "gaming"

    def test_log_publish_result_omits_niche_id_field_when_empty(self):
        client, mock_analytics = _make_client_with_mock_analytics()

        client.log_publish_result(
            candidate_id="cand-200",
            platform="youtube",
            status="SUCCESS",
        )

        create_calls = mock_analytics.create.call_args_list
        assert len(create_calls) == 1
        fields = create_calls[0][0][0]
        assert "niche_id" not in fields
