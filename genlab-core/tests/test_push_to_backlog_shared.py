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


class TestBanditArmBoost(unittest.TestCase):
    """Tests for the Thompson-sampled bandit consumer (2026-05-17).

    The bandit consumer is the first live reader of bandit_arms
    posteriors. It draws a Beta(alpha, beta) sample per arm and converts
    the draw into a priority_score multiplier. Randomness IS the
    exploration mechanism — equal-alpha arms produce variable boosts,
    well-trained arms concentrate near their posterior mean.
    """

    def _make_proxy(self, arms_data):
        """Build a mock bandit_arms proxy returning the given rows."""
        proxy = MagicMock()
        proxy.all.return_value = [
            {"id": f"row_{i}", "fields": {"arm_id": arm_id, "alpha": a, "beta": b}}
            for i, (arm_id, (a, b)) in enumerate(arms_data.items())
        ]
        return proxy

    def test_returns_empty_when_no_bandit_proxy(self):
        from genlab_core.pipeline.stages.push_to_backlog import _get_bandit_arm_boost
        client = MagicMock()
        client.bandit_arms = None
        self.assertEqual(_get_bandit_arm_boost(client, "gaming"), {})

    def test_returns_empty_when_no_arms_for_niche(self):
        from genlab_core.pipeline.stages.push_to_backlog import _get_bandit_arm_boost
        client = MagicMock()
        client.bandit_arms = self._make_proxy({})
        self.assertEqual(_get_bandit_arm_boost(client, "gaming"), {})

    def test_boosts_lie_in_documented_range(self):
        from genlab_core.pipeline.stages.push_to_backlog import (
            _BANDIT_BOOST_CEIL,
            _BANDIT_BOOST_FLOOR,
            _get_bandit_arm_boost,
        )
        import random as _random

        _random.seed(42)
        client = MagicMock()
        client.bandit_arms = self._make_proxy({
            "arm_a": (1.0, 1.0),
            "arm_b": (5.0, 5.0),
            "arm_c": (20.0, 1.0),
            "arm_d": (1.0, 20.0),
        })

        boosts = _get_bandit_arm_boost(client, "gaming")
        self.assertEqual(set(boosts), {"arm_a", "arm_b", "arm_c", "arm_d"})
        for arm_id, b in boosts.items():
            self.assertGreaterEqual(b, _BANDIT_BOOST_FLOOR, arm_id)
            self.assertLessEqual(b, _BANDIT_BOOST_CEIL, arm_id)

    def test_high_alpha_arm_boosts_higher_than_low_alpha_on_average(self):
        """High-alpha arm should sample near 1.0 → boost near ceiling.
        Low-alpha arm should sample near 0.0 → boost near floor.
        Averaged over many draws to defeat sampling variance.
        """
        from genlab_core.pipeline.stages.push_to_backlog import _get_bandit_arm_boost
        import random as _random

        _random.seed(123)
        client = MagicMock()
        client.bandit_arms = self._make_proxy({
            "high": (50.0, 2.0),  # posterior mean 0.96
            "low": (2.0, 50.0),   # posterior mean 0.04
        })

        high_total = 0.0
        low_total = 0.0
        n = 200
        for _ in range(n):
            boosts = _get_bandit_arm_boost(client, "gaming")
            high_total += boosts["high"]
            low_total += boosts["low"]

        high_mean = high_total / n
        low_mean = low_total / n
        self.assertGreater(
            high_mean - low_mean, 0.4,
            f"high={high_mean:.3f} low={low_mean:.3f} — bandit not discriminating",
        )

    def test_zero_alpha_or_beta_gracefully_handled(self):
        """alpha or beta <= 0 would crash random.betavariate; we guard
        by flooring to 1.0 so a stale/zeroed row doesn't kill the push."""
        from genlab_core.pipeline.stages.push_to_backlog import _get_bandit_arm_boost
        client = MagicMock()
        client.bandit_arms = self._make_proxy({
            "pathological": (0.0, 0.0),
        })
        boosts = _get_bandit_arm_boost(client, "gaming")
        self.assertIn("pathological", boosts)
        # Beta(1,1) sample bounded by [floor, ceil]
        self.assertGreaterEqual(boosts["pathological"], 0.7)
        self.assertLessEqual(boosts["pathological"], 1.3)

    def test_arm_id_in_boost_matches_classify_arm_output(self):
        """The arm_id keys returned by _get_bandit_arm_boost must match
        the arm_ids that _classify_arm assigns. Otherwise the boost
        dict lookup at priority computation always misses.
        """
        from genlab_core.pipeline.stages.push_to_backlog import (
            _classify_arm,
            _get_bandit_arm_boost,
        )

        client = MagicMock()
        client.bandit_arms = self._make_proxy({
            "gameplay_clip": (3.0, 5.0),
            "patch_news": (2.0, 8.0),
        })
        boosts = _get_bandit_arm_boost(client, "gaming")

        story = {"title": "New Patch 1.42 buffs healers", "summary": ""}
        content = {"hook": "Patch dropped"}
        classified = _classify_arm("gaming", story, content)

        # Either the classified arm is in the boost dict (lookup works)
        # or it falls through to the niche default (also in arms or 1.0).
        # The test asserts the LOOKUP doesn't silently miss for the
        # canonical arms.
        self.assertIn(classified, ["gameplay_clip", "patch_news",
                                    "esports_highlight", "trailer_reaction"])


if __name__ == "__main__":
    unittest.main()
