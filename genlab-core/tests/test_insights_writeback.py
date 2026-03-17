"""Tests for _write_back_to_blueprint in run_fetch_insights."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


class TestWriteBackToBlueprint(unittest.TestCase):

    def _call(self, client, bp_id, platform, insights, window=6):
        from genlab_core.scripts.run_fetch_insights import _write_back_to_blueprint
        _write_back_to_blueprint(client, bp_id, platform, insights, window)

    def test_instagram_fields_written(self):
        """IG insights should write ig_reach, ig_likes, ig_comments per-field."""
        client = MagicMock()
        insights = {"reach": 847, "likes": 23, "comments": 5, "saved": 12}
        self._call(client, "1518", "instagram", insights)

        # Per-field writes — each field is a separate update call
        assert client.blueprints.update.call_count >= 3
        all_fields = {}
        for call_args in client.blueprints.update.call_args_list:
            all_fields.update(call_args[0][1])
        self.assertEqual(all_fields["ig_reach"], 847)
        self.assertEqual(all_fields["ig_likes"], 23)
        self.assertEqual(all_fields["ig_comments"], 5)

    def test_youtube_fields_written(self):
        client = MagicMock()
        insights = {"views": 1204, "likes": 45, "comments": 8}
        self._call(client, "1518", "youtube", insights)

        all_fields = {}
        for call_args in client.blueprints.update.call_args_list:
            all_fields.update(call_args[0][1])
        self.assertEqual(all_fields["yt_views"], 1204)
        self.assertEqual(all_fields["yt_likes"], 45)
        self.assertEqual(all_fields["yt_comments"], 8)

    def test_engagement_rate_from_ig(self):
        client = MagicMock()
        insights = {"reach": 1000, "likes": 20, "comments": 10}
        self._call(client, "bp1", "instagram", insights)

        all_fields = {}
        for call_args in client.blueprints.update.call_args_list:
            all_fields.update(call_args[0][1])
        self.assertAlmostEqual(all_fields["engagement_rate"], 0.03)

    def test_missing_field_skipped_gracefully(self):
        """If a SharePoint column doesn't exist, the per-field write
        catches the exception and continues with other fields."""
        client = MagicMock()
        # First call succeeds, second raises (missing column)
        client.blueprints.update.side_effect = [None, Exception("Field not recognized"), None, None]
        insights = {"reach": 100, "likes": 5, "comments": 1}
        # Should not raise
        self._call(client, "bp1", "instagram", insights)

    def test_nonfatal_caller_catches_exception(self):
        """Verify the call site in fetch_insights_for_window wraps in try/except."""
        import inspect

        from genlab_core.scripts import run_fetch_insights as mod
        source = inspect.getsource(mod.fetch_insights_for_window)
        self.assertIn("_write_back_to_blueprint", source)
        self.assertIn("except Exception as wb_exc", source)
