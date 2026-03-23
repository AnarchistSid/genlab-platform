"""Tests for the shared PushToBacklog pipeline stage.

Validates that PushToBacklog reads niche_id from context, rejects
missing niche_id, and correctly forwards niche_id when creating
stories and blueprints on the BacklogClient.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


class TestPushToBacklogShared(unittest.TestCase):
    """Tests for genlab_core.pipeline.stages.push_to_backlog.PushToBacklog."""

    def _make_stage(self):
        from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog
        return PushToBacklog()

    def _make_story(self, title="Test Story", source_url="https://example.com/story1"):
        # Use a recent date to stay within the 7-day freshness gate
        recent = (datetime.now(UTC) - timedelta(hours=6)).isoformat()
        return {
            "title": title,
            "source_url": source_url,
            "published_at": recent,
            "source": "test_source",
            "summary": "A test story summary",
            "score": 0.8,
            "content": {
                "hook": "Test hook text",
                "instagram": {"caption": "IG caption", "hashtags": ["#test"]},
                "youtube": {"title": "YT Title", "description": "YT desc"},
                "x_twitter": {"tweet": "Tweet text"},
                "facebook": {"caption": "FB caption"},
            },
        }

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_raises_without_niche_id(self, mock_settings):
        """execute() raises ValueError when context has no niche_id."""
        mock_settings.azure_tenant_id = "t"
        mock_settings.azure_client_id = "c"
        mock_settings.azure_client_secret = "s"
        mock_settings.sharepoint_site_id = "sp"

        stage = self._make_stage()
        context = {"stories": [self._make_story()]}

        with self.assertRaises(ValueError) as cm:
            stage.execute(context)

        self.assertIn("niche_id", str(cm.exception))

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_raises_with_empty_niche_id(self, mock_settings):
        """execute() raises ValueError when niche_id is empty string."""
        mock_settings.azure_tenant_id = "t"
        mock_settings.azure_client_id = "c"
        mock_settings.azure_client_secret = "s"
        mock_settings.sharepoint_site_id = "sp"

        stage = self._make_stage()
        context = {"niche_id": "", "stories": [self._make_story()]}

        with self.assertRaises(ValueError):
            stage.execute(context)

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_reads_niche_id_from_context(self, mock_settings):
        """execute() reads niche_id from context and passes it to BacklogClient calls."""
        mock_settings.azure_tenant_id = "t"
        mock_settings.azure_client_id = "c"
        mock_settings.azure_client_secret = "s"
        mock_settings.sharepoint_site_id = "sp"

        stage = self._make_stage()

        mock_client = MagicMock()
        mock_client.find_story_by_story_id.return_value = None
        mock_client.stories.create.return_value = {"id": "rec123"}
        mock_client.blueprints.all.return_value = []
        mock_client.blueprints.create.return_value = {"id": "bp456"}
        stage._client = mock_client

        context = {
            "niche_id": "sports",
            "stories": [self._make_story()],
        }

        result = stage.execute(context)

        # Verify story was created with niche_id="sports"
        story_create_call = mock_client.stories.create.call_args[0][0]
        self.assertEqual(story_create_call["niche_id"], "sports")

        # Verify blueprint was created with niche_id="sports"
        bp_create_call = mock_client.blueprints.create.call_args[0][0]
        self.assertEqual(bp_create_call["niche_id"], "sports")

        # Verify stats recorded
        self.assertEqual(result["run_stats"]["backlog_push"]["stories_pushed"], 1)
        self.assertEqual(result["run_stats"]["backlog_push"]["blueprints_pushed"], 1)

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_creates_story_with_correct_niche_id(self, mock_settings):
        """Story creation includes the niche_id from context for each niche."""
        mock_settings.azure_tenant_id = "t"
        mock_settings.azure_client_id = "c"
        mock_settings.azure_client_secret = "s"
        mock_settings.sharepoint_site_id = "sp"

        for niche in ("sports", "movies", "anime"):
            stage = self._make_stage()
            mock_client = MagicMock()
            mock_client.find_story_by_story_id.return_value = None
            mock_client.stories.create.return_value = {"id": "rec_x"}
            mock_client.blueprints.all.return_value = []
            mock_client.blueprints.create.return_value = {"id": "bp_x"}
            stage._client = mock_client

            context = {
                "niche_id": niche,
                "stories": [self._make_story()],
            }
            stage.execute(context)

            story_fields = mock_client.stories.create.call_args[0][0]
            self.assertEqual(story_fields["niche_id"], niche, f"Failed for niche={niche}")
            self.assertEqual(story_fields["status"], "INTAKE")

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_creates_blueprint_with_correct_niche_id(self, mock_settings):
        """Blueprint creation includes niche_id and uses {niche_id}_default candidate template."""
        mock_settings.azure_tenant_id = "t"
        mock_settings.azure_client_id = "c"
        mock_settings.azure_client_secret = "s"
        mock_settings.sharepoint_site_id = "sp"

        # Run with two different niches to verify candidate_id differs
        candidate_ids = {}
        for niche in ("anime", "sports"):
            stage = self._make_stage()
            mock_client = MagicMock()
            mock_client.find_story_by_story_id.return_value = None
            mock_client.stories.create.return_value = {"id": "rec_y"}
            mock_client.blueprints.all.return_value = []
            mock_client.blueprints.create.return_value = {"id": "bp_y"}
            stage._client = mock_client

            context = {
                "niche_id": niche,
                "stories": [self._make_story()],
            }
            stage.execute(context)

            bp_fields = mock_client.blueprints.create.call_args[0][0]
            self.assertEqual(bp_fields["niche_id"], niche)
            self.assertEqual(bp_fields["format"], "reel")
            candidate_ids[niche] = bp_fields["candidate_id"]

        # Different niches should produce different candidate_ids
        # (because template_id is "{niche_id}_default")
        self.assertNotEqual(candidate_ids["anime"], candidate_ids["sports"])

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_skips_when_no_stories(self, mock_settings):
        """execute() returns early with no side effects when stories list is empty."""
        mock_settings.azure_tenant_id = "t"
        mock_settings.azure_client_id = "c"
        mock_settings.azure_client_secret = "s"
        mock_settings.sharepoint_site_id = "sp"

        stage = self._make_stage()
        context = {"niche_id": "sports", "stories": []}

        result = stage.execute(context)

        # No run_stats should be set — early return before any client work
        self.assertNotIn("run_stats", result)

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_skips_without_credentials(self, mock_settings):
        """execute() logs a skip when Azure credentials are missing."""
        mock_settings.azure_tenant_id = ""
        mock_settings.azure_client_id = ""
        mock_settings.azure_client_secret = ""
        mock_settings.sharepoint_site_id = ""

        stage = self._make_stage()
        context = {
            "niche_id": "movies",
            "stories": [self._make_story()],
        }

        result = stage.execute(context)

        stats = result["run_stats"]["backlog_push"]
        self.assertEqual(stats["status"], "skipped_no_credentials")
        self.assertEqual(stats["stories_pushed"], 0)

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_uses_source_as_default_topic(self, mock_settings):
        """When story has a source field, it is used as topic; otherwise niche_id is the fallback."""
        mock_settings.azure_tenant_id = "t"
        mock_settings.azure_client_id = "c"
        mock_settings.azure_client_secret = "s"
        mock_settings.sharepoint_site_id = "sp"

        stage = self._make_stage()
        mock_client = MagicMock()
        mock_client.find_story_by_story_id.return_value = None
        mock_client.stories.create.return_value = {"id": "rec_z"}
        mock_client.blueprints.all.return_value = []
        mock_client.blueprints.create.return_value = {"id": "bp_z"}
        stage._client = mock_client

        story = self._make_story()
        story["source"] = "espn_api"
        context = {"niche_id": "sports", "stories": [story]}
        stage.execute(context)

        bp_fields = mock_client.blueprints.create.call_args[0][0]
        self.assertEqual(bp_fields["topic"], "espn_api")

    @patch("genlab_core.pipeline.stages.push_to_backlog.settings")
    def test_push_to_backlog_visual_ready_when_rendered(self, mock_settings):
        """Blueprint status is VISUAL_READY when rendered_path is present."""
        mock_settings.azure_tenant_id = "t"
        mock_settings.azure_client_id = "c"
        mock_settings.azure_client_secret = "s"
        mock_settings.sharepoint_site_id = "sp"

        stage = self._make_stage()
        mock_client = MagicMock()
        mock_client.find_story_by_story_id.return_value = None
        mock_client.stories.create.return_value = {"id": "rec_r"}
        mock_client.blueprints.all.return_value = []
        mock_client.blueprints.create.return_value = {"id": "bp_r"}
        stage._client = mock_client

        story = self._make_story()
        story["media"] = {"rendered_path": "/tmp/video.mp4"}
        context = {"niche_id": "movies", "stories": [story]}
        stage.execute(context)

        bp_fields = mock_client.blueprints.create.call_args[0][0]
        self.assertEqual(bp_fields["status"], "VISUAL_READY")


if __name__ == "__main__":
    unittest.main()
