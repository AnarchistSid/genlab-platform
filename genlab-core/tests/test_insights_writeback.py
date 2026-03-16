"""Tests for _write_back_to_blueprint in run_fetch_insights."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock


class TestWriteBackToBlueprint(unittest.TestCase):

    def _call(self, client, bp_id, platform, insights, window=6):
        from genlab_core.scripts.run_fetch_insights import _write_back_to_blueprint
        _write_back_to_blueprint(client, bp_id, platform, insights, window)

    def test_instagram_fields_written(self):
        client = MagicMock()
        insights = {"reach": 847, "likes": 23, "comments": 5, "saved": 12}
        self._call(client, "1518", "instagram", insights)

        client.blueprints.update.assert_called_once()
        fields = client.blueprints.update.call_args[0][1]
        self.assertEqual(fields["ig_reach"], 847)
        self.assertEqual(fields["ig_likes"], 23)
        self.assertEqual(fields["ig_comments"], 5)
        self.assertIn("insights_collected_at", fields)

    def test_youtube_fields_written(self):
        client = MagicMock()
        insights = {"views": 1204, "likes": 45, "comments": 8}
        self._call(client, "1518", "youtube", insights)

        fields = client.blueprints.update.call_args[0][1]
        self.assertEqual(fields["yt_views"], 1204)
        self.assertEqual(fields["yt_likes"], 45)
        self.assertEqual(fields["yt_comments"], 8)

    def test_engagement_rate_from_ig(self):
        client = MagicMock()
        insights = {"reach": 1000, "likes": 20, "comments": 10}
        self._call(client, "bp1", "instagram", insights)

        fields = client.blueprints.update.call_args[0][1]
        self.assertAlmostEqual(fields["engagement_rate"], 0.03)

    def test_engagement_rate_from_yt_when_no_ig(self):
        client = MagicMock()
        insights = {"views": 2000, "likes": 50, "comments": 10}
        self._call(client, "bp1", "youtube", insights)

        fields = client.blueprints.update.call_args[0][1]
        self.assertAlmostEqual(fields["engagement_rate"], 0.03)

    def test_6h_window_sets_insights_6h_at(self):
        client = MagicMock()
        insights = {"views": 100, "likes": 5, "comments": 1}
        self._call(client, "bp1", "youtube", insights, window=6)

        fields = client.blueprints.update.call_args[0][1]
        self.assertIn("insights_6h_at", fields)
        self.assertNotIn("insights_24h_at", fields)

    def test_24h_window_sets_insights_24h_at(self):
        client = MagicMock()
        insights = {"views": 500, "likes": 20, "comments": 3}
        self._call(client, "bp1", "youtube", insights, window=24)

        fields = client.blueprints.update.call_args[0][1]
        self.assertIn("insights_24h_at", fields)
        self.assertNotIn("insights_6h_at", fields)

    def test_nonfatal_caller_catches_exception(self):
        """Verify the call site in fetch_insights_for_window wraps in try/except."""
        import inspect
        from genlab_core.scripts import run_fetch_insights as mod
        source = inspect.getsource(mod.fetch_insights_for_window)
        self.assertIn("_write_back_to_blueprint", source)
        self.assertIn("except Exception as wb_exc", source)
