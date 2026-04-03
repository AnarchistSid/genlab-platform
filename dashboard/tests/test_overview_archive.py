"""Tests for overview auto-archive stats."""
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch


class TestOverviewArchiveStats(unittest.TestCase):

    @patch("server.api.overview._get_client")
    @patch("server.api.overview._load_registry")
    @patch("server.api.overview._platform_health_from_reports")
    @patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x)
    @patch("server.api.pipeline._prefect_healthy", return_value=False)
    def test_overview_includes_auto_archive_today(
        self, _ph, _mp, _phr, mock_registry, mock_client_fn
    ):
        from server.api.overview import _build_overview

        mock_registry.return_value = [{"id": "sports", "status": "active", "display_name": "CW"}]
        _phr.return_value = {}

        now_iso = datetime.now(UTC).isoformat()
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            {"id": "1", "fields": {"status": "ARCHIVED", "action_taken": "auto_archived_no_video",
                                   "reviewed_at": now_iso, "niche_id": "sports"}},
            {"id": "2", "fields": {"status": "ARCHIVED", "action_taken": "auto_archived_template_hook",
                                   "reviewed_at": now_iso, "niche_id": "sports"}},
            {"id": "3", "fields": {"status": "PUBLISHED", "published_at": now_iso,
                                   "niche_id": "sports", "priority_score": "0.8", "hook_text": "Test"}},
        ]
        mock_client_fn.return_value = mock_client

        result = _build_overview()

        archive = result["global"]["auto_archive_today"]
        assert archive["total"] == 2
        assert archive["by_reason"]["auto_archived_no_video"] == 1
        assert archive["by_reason"]["auto_archived_template_hook"] == 1
        assert archive["pass_rate"] is not None
        assert 0.3 <= archive["pass_rate"] <= 0.34

    @patch("server.api.overview._get_client")
    @patch("server.api.overview._load_registry")
    @patch("server.api.overview._platform_health_from_reports")
    @patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x)
    @patch("server.api.pipeline._prefect_healthy", return_value=False)
    def test_overview_pass_rate_none_when_zero(
        self, _ph, _mp, _phr, mock_registry, mock_client_fn
    ):
        from server.api.overview import _build_overview

        mock_registry.return_value = [{"id": "gaming", "status": "active", "display_name": "CR"}]
        _phr.return_value = {}

        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = []
        mock_client_fn.return_value = mock_client

        result = _build_overview()
        archive = result["global"]["auto_archive_today"]
        assert archive["total"] == 0
        assert archive["pass_rate"] is None

    @patch("server.api.overview._get_client")
    @patch("server.api.overview._load_registry")
    @patch("server.api.overview._platform_health_from_reports")
    @patch("server.api.pipeline._merge_prefect_status", side_effect=lambda x: x)
    @patch("server.api.pipeline._prefect_healthy", return_value=False)
    def test_overview_per_niche_archived_today(
        self, _ph, _mp, _phr, mock_registry, mock_client_fn
    ):
        from server.api.overview import _build_overview

        mock_registry.return_value = [
            {"id": "sports", "status": "active", "display_name": "CW"},
            {"id": "gaming", "status": "active", "display_name": "CR"},
        ]
        _phr.return_value = {}

        now_iso = datetime.now(UTC).isoformat()
        mock_client = MagicMock()
        mock_client.blueprints.all.return_value = [
            {"id": "1", "fields": {"status": "ARCHIVED", "action_taken": "auto_archived_no_video",
                                   "reviewed_at": now_iso, "niche_id": "sports"}},
            {"id": "2", "fields": {"status": "ARCHIVED", "action_taken": "auto_archived_stale",
                                   "reviewed_at": now_iso, "niche_id": "gaming"}},
        ]
        mock_client_fn.return_value = mock_client

        result = _build_overview()
        niches = {n["id"]: n for n in result["niches"]}
        assert niches["sports"]["archived_today"] == 1
        assert niches["gaming"]["archived_today"] == 1
