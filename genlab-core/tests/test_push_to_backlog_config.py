"""Tests for PushToBacklog config path resolution (Bug 3 — Sprint 65).

Validates that PushToBacklog._get_client() correctly resolves the
lists_config.yaml path from multiple sources:
  1. Explicit context["backlog_config_path"]
  2. context["niche_root"] / config / lists_config.yaml
  3. Fallback to BacklogClient() defaults (env var + CWD walk)

Also verifies that the pipeline runner injects niche_root into
the context dict so non-BB channels can resolve their config.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestPushToBacklogConfigResolution(unittest.TestCase):
    """Tests for _get_client() config path resolution."""

    def _make_stage(self):
        from genlab_core.pipeline.stages.push_to_backlog import PushToBacklog
        return PushToBacklog()

    @patch("genlab_core.pipeline.stages.push_to_backlog.BacklogClient")
    def test_get_client_with_explicit_config_path(self, MockClient):
        """When context has backlog_config_path, it is passed directly."""
        MockClient.return_value = MagicMock()
        stage = self._make_stage()

        context = {
            "niche_id": "sports",
            "backlog_config_path": "/some/explicit/lists_config.yaml",
        }

        client = stage._get_client(context)

        MockClient.assert_called_once_with(
            config_path="/some/explicit/lists_config.yaml",
        )
        self.assertIs(client, MockClient.return_value)

    @patch("genlab_core.pipeline.stages.push_to_backlog.BacklogClient")
    def test_get_client_resolves_from_niche_root(self, MockClient, tmp_path=None):
        """When context has niche_root with config/lists_config.yaml, that path is used."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            niche_root = Path(tmpdir)
            config_dir = niche_root / "config"
            config_dir.mkdir()
            config_file = config_dir / "lists_config.yaml"
            config_file.write_text("Blueprints:\n  list_id: test-id\n")

            MockClient.return_value = MagicMock()
            stage = self._make_stage()

            context = {
                "niche_id": "sports",
                "niche_root": str(niche_root),
            }

            client = stage._get_client(context)

            MockClient.assert_called_once_with(
                config_path=str(config_file),
            )
            self.assertIs(client, MockClient.return_value)

    @patch("genlab_core.pipeline.stages.push_to_backlog.BacklogClient")
    def test_get_client_without_config_path_uses_default(self, MockClient):
        """When neither backlog_config_path nor niche_root is set, falls through to BacklogClient defaults."""
        MockClient.return_value = MagicMock()
        stage = self._make_stage()

        context = {"niche_id": "movies"}

        client = stage._get_client(context)

        # Called with no arguments — BacklogClient resolves via env var / CWD walk
        MockClient.assert_called_once_with()
        self.assertIs(client, MockClient.return_value)

    @patch("genlab_core.pipeline.stages.push_to_backlog.BacklogClient")
    def test_get_client_niche_root_without_config_file_falls_through(self, MockClient):
        """When niche_root is set but config/lists_config.yaml doesn't exist, falls through."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            # niche_root exists but has no config/lists_config.yaml
            MockClient.return_value = MagicMock()
            stage = self._make_stage()

            context = {
                "niche_id": "anime",
                "niche_root": tmpdir,
            }

            stage._get_client(context)

            # Should fall through to no-arg default
            MockClient.assert_called_once_with()

    @patch("genlab_core.pipeline.stages.push_to_backlog.BacklogClient")
    def test_get_client_caches_after_first_call(self, MockClient):
        """_get_client() is lazy — second call returns cached client."""
        MockClient.return_value = MagicMock()
        stage = self._make_stage()

        context = {"niche_id": "gaming"}

        client1 = stage._get_client(context)
        client2 = stage._get_client(context)

        self.assertIs(client1, client2)
        # Only one BacklogClient instantiation
        MockClient.assert_called_once()

    @patch("genlab_core.pipeline.stages.push_to_backlog.BacklogClient")
    def test_explicit_path_takes_priority_over_niche_root(self, MockClient):
        """backlog_config_path wins even when niche_root also has a config."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            niche_root = Path(tmpdir)
            config_dir = niche_root / "config"
            config_dir.mkdir()
            (config_dir / "lists_config.yaml").write_text("Blueprints:\n  list_id: x\n")

            MockClient.return_value = MagicMock()
            stage = self._make_stage()

            explicit_path = "/override/lists_config.yaml"
            context = {
                "niche_id": "sports",
                "backlog_config_path": explicit_path,
                "niche_root": str(niche_root),
            }

            stage._get_client(context)

            MockClient.assert_called_once_with(config_path=explicit_path)


class TestPipelineRunnerNicheRoot(unittest.TestCase):
    """Verify that GenericPipelineRunner injects niche_root into context_dict."""

    @patch("genlab_core.pipeline.pipeline_runner.StageRunnerFactory")
    @patch("genlab_core.pipeline.pipeline_runner.install_log_handler")
    @patch("genlab_core.pipeline.pipeline_runner.load_niche_config")
    @patch("genlab_core.pipeline.pipeline_runner.get_feature_flags")
    def test_niche_root_in_context_dict_via_stage(
        self, mock_flags, mock_config, mock_log, mock_factory,
    ):
        """A stage receives niche_root in its context dict."""
        import tempfile

        from genlab_core.pipeline.pipeline_runner import GenericPipelineRunner

        # Create a fake stage class path
        captured_context = {}

        class FakeStage:
            def execute(self, context):
                captured_context.update(context)
                return context

        mock_config.return_value = {
            "pipeline": {
                "stages": [
                    {"class": "test_module.FakeStage", "enabled": True},
                ],
            },
        }
        mock_flags.return_value = {}
        mock_log.return_value = (MagicMock(), MagicMock(), "/tmp/log")

        # Make factory.run() actually call stage.execute() with the context_dict
        def run_side_effect(declaration, stage, context_dict, pipeline_ctx):
            from genlab_core.pipeline.stage_runner import StageResult
            captured_context.update(context_dict)
            return StageResult(stage_name="FakeStage", success=True, elapsed_seconds=0.1)

        mock_runner_instance = MagicMock()
        mock_runner_instance.run.side_effect = run_side_effect
        mock_factory.return_value = mock_runner_instance

        with tempfile.TemporaryDirectory() as tmpdir:
            niche_root = Path(tmpdir) / "ClutchWire"
            niche_root.mkdir()
            genlab_root = Path(tmpdir) / "genlab"
            genlab_root.mkdir()
            (genlab_root / ".tmp").mkdir()

            runner = GenericPipelineRunner(
                niche_roots={"sports": niche_root},
                genlab_root=genlab_root,
            )

            # Patch _load_stages to return our fake stage
            with patch.object(
                GenericPipelineRunner,
                "_load_stages",
                return_value=([FakeStage()], [{"class": "fake.FakeStage"}]),
            ):
                runner.run("sports")

            self.assertIn("niche_root", captured_context)
            self.assertEqual(captured_context["niche_root"], str(niche_root))


if __name__ == "__main__":
    unittest.main()
