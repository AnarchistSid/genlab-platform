"""Tests for BB strategy wrapper classes.

Verifies:
- Each strategy can be instantiated
- Each is a subclass of the correct genlab_core ABC
- execute() delegates to the underlying BB script functions
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

from genlab_core.strategies import (
    ContentResearchStrategy,
    HookStrategy,
    PlatformAdaptationStrategy,
    ScoringStrategy,
    VisualRenderStrategy,
    WritingStrategy,
)

from bb_strategies.content_research import BBContentResearchStrategy
from bb_strategies.hooks import BBHookStrategy
from bb_strategies.platform_adaptation import BBPlatformAdaptationStrategy
from bb_strategies.scoring import BBScoringStrategy
from bb_strategies.visual_render import BBVisualRenderStrategy
from bb_strategies.writing import BBWritingStrategy

# ── Instantiation + ABC subclass tests ─────────────────────────


class TestContentResearch:
    def test_instantiation(self):
        strategy = BBContentResearchStrategy()
        assert strategy is not None

    def test_is_subclass(self):
        assert issubclass(BBContentResearchStrategy, ContentResearchStrategy)

    def test_execute_delegates(self, tmp_path):
        strategy = BBContentResearchStrategy()
        mock_results = [
            {"entry_count": 3, "cache_hit": False},
            {"entry_count": 2, "cache_hit": True},
        ]
        mock_items = [
            {"title": "Story 1", "url": "https://example.com/1"},
            {"title": "Story 2", "url": "https://example.com/2"},
        ]

        with (
            patch("bb_strategies.content_research.BB_ROOT", tmp_path),
            patch(
                "execution.fetch_ai_creators.load_sources",
                return_value=[{"name": "test", "url": "https://example.com"}],
            ) as mock_load,
            patch(
                "execution.fetch_ai_creators.fetch_all_sources",
                return_value=mock_results,
            ) as mock_fetch,
            patch(
                "execution.parse_extract.parse_fetch_log",
                return_value=mock_items,
            ) as mock_parse,
            patch("genlab_core.cache.disk_cache.Cache"),
        ):
            context = {"run_id": "test_run_001"}
            result = strategy.execute(context)

            mock_load.assert_called_once()
            mock_fetch.assert_called_once()
            mock_parse.assert_called_once()

            assert result["run_stats"]["fetch"]["total_entries"] == 5
            assert result["run_stats"]["fetch"]["parsed_items"] == 2


class TestScoring:
    def test_instantiation(self):
        strategy = BBScoringStrategy()
        assert strategy is not None

    def test_is_subclass(self):
        assert issubclass(BBScoringStrategy, ScoringStrategy)

    def test_execute_delegates(self, tmp_path):
        strategy = BBScoringStrategy()

        # Create parsed_items.json fixture
        run_dir = tmp_path / ".tmp" / "runs" / "test_run"
        run_dir.mkdir(parents=True)
        import json

        parsed = {
            "item_count": 3,
            "items": [
                {"title": "A", "url": "https://a.com", "composite_score": 0.8},
                {"title": "B", "url": "https://b.com", "composite_score": 0.6},
                {"title": "C", "url": "https://c.com", "composite_score": 0.4},
            ],
        }
        (run_dir / "parsed_items.json").write_text(json.dumps(parsed))

        with (
            patch("bb_strategies.scoring.BB_ROOT", tmp_path),
            patch(
                "execution.dedupe_rank_items.load_weights",
                return_value={
                    "weights": {"virality_fit": 0.35, "recency": 0.25, "novelty": 0.20, "authority": 0.20},
                    "authority_map": {"default": 0.5},
                    "recency_decay_hours": 24,
                    "novelty_threshold": 0.85,
                },
            ),
            patch(
                "execution.dedupe_rank_items.dedup_pass_url",
                return_value=(parsed["items"], 0),
            ) as mock_url,
            patch(
                "execution.dedupe_rank_items.dedup_pass_title",
                return_value=(parsed["items"], 0),
            ) as mock_title,
            patch(
                "execution.dedupe_rank_items.filter_low_quality",
                return_value=(parsed["items"], 0),
            ) as mock_quality,
            patch(
                "execution.dedupe_rank_items.rank_items",
                return_value=parsed["items"],
            ) as mock_rank,
            patch(
                "execution.dedupe_rank_items.cluster_stories",
                return_value=(parsed["items"], {"clusters": 1}),
            ) as mock_cluster,
        ):
            context = {"run_id": "test_run"}
            result = strategy.execute(context)

            mock_url.assert_called_once()
            mock_title.assert_called_once()
            mock_quality.assert_called_once()
            mock_rank.assert_called_once()
            mock_cluster.assert_called_once()

            assert result["run_stats"]["scoring"]["input_count"] == 3
            assert result["run_stats"]["scoring"]["scored_count"] == 3


class TestWriting:
    def test_instantiation(self):
        strategy = BBWritingStrategy()
        assert strategy is not None

    def test_is_subclass(self):
        assert issubclass(BBWritingStrategy, WritingStrategy)

    def test_execute_delegates(self, tmp_path):
        """Writing strategy processes stories from context (template fallback path)."""
        strategy = BBWritingStrategy()

        context = {
            "run_id": "test_run",
            "stories": [
                {"story_id": "s1", "title": "OpenAI Releases GPT-5"},
                {"story_id": "s2", "title": "Anthropic Claude Update"},
            ],
        }
        # No ANTHROPIC_API_KEY → falls through to template-based writing
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = strategy.execute(context)

        assert result["run_stats"]["content_writing"]["written_count"] == 2
        # Verify stories got content attached
        for story in result["stories"]:
            assert story["content"]["written"] is True


class TestHooks:
    def test_instantiation(self):
        strategy = BBHookStrategy()
        assert strategy is not None

    def test_is_subclass(self):
        assert issubclass(BBHookStrategy, HookStrategy)

    def test_execute_delegates(self, tmp_path):
        strategy = BBHookStrategy()

        run_dir = tmp_path / ".tmp" / "runs" / "test_run"
        run_dir.mkdir(parents=True)
        import json

        ranked = {
            "stories": [
                {"title": "OpenAI Releases GPT-5", "summary": "Next-gen model"},
            ]
        }
        (run_dir / "ranked_stories.json").write_text(json.dumps(ranked))

        mock_variants = [
            {"hook": "GPT-5 just dropped", "engagement_score": 0.9, "formula_id": "f1"},
            {"hook": "OpenAI shocks everyone", "engagement_score": 0.7, "formula_id": "f2"},
        ]

        with (
            patch("bb_strategies.hooks.BB_ROOT", tmp_path),
            patch(
                "execution.generate_hooks.generate_hook_variants",
                return_value=mock_variants,
            ) as mock_gen,
            patch(
                "execution.generate_hooks.score_hooks",
                return_value=mock_variants,
            ) as mock_score,
        ):
            context = {"run_id": "test_run"}
            result = strategy.execute(context)

            mock_gen.assert_called_once()
            mock_score.assert_called_once()
            assert result["run_stats"]["hooks"]["hooked_count"] == 1


class TestVisualRender:
    def test_instantiation(self):
        strategy = BBVisualRenderStrategy()
        assert strategy is not None

    def test_is_subclass(self):
        assert issubclass(BBVisualRenderStrategy, VisualRenderStrategy)

    def test_execute_delegates(self, tmp_path):
        """VisualRender processes stories with clips via FrameCompositor."""
        strategy = BBVisualRenderStrategy()

        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"\x00" * 100)
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        context = {
            "run_id": "test_run_001",
            "run_dir": str(run_dir),
            "stories": [
                {"story_id": "s1", "title": "Test Story", "content": {"hook": "Test hook"}},
            ],
            "clip_index": {
                "clips": {
                    "s1": {"success": True, "clip_path": str(clip_path)},
                },
            },
        }

        with patch("bb_strategies.visual_render.FrameCompositor") as MockCompositor:
            mock_instance = MagicMock()
            mock_instance.compose.return_value = str(run_dir / "out.mp4")
            MockCompositor.from_visuals_yaml.return_value = mock_instance

            result = strategy.execute(context)
            mock_instance.compose.assert_called_once()
            assert result["run_stats"]["render"]["rendered"] == 1

    def test_execute_handles_no_stories(self):
        """VisualRender returns no_stories when stories list is empty."""
        strategy = BBVisualRenderStrategy()

        context = {"run_id": "test_run_001", "stories": []}
        result = strategy.execute(context)

        assert result["run_stats"]["render"]["status"] == "no_stories"
        assert result["run_stats"]["render"]["rendered"] == 0


class TestPlatformAdaptation:
    def test_instantiation(self):
        strategy = BBPlatformAdaptationStrategy()
        assert strategy is not None

    def test_is_subclass(self):
        assert issubclass(BBPlatformAdaptationStrategy, PlatformAdaptationStrategy)

    def test_execute_delegates(self):
        strategy = BBPlatformAdaptationStrategy()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            context = {"run_id": "test_run_001"}
            result = strategy.execute(context)

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            cmd = call_args[0][0]
            assert "--run-id" in cmd
            assert "test_run_001" in cmd
            assert result["run_stats"]["adapt"]["status"] == "completed"

    def test_execute_no_run_id(self):
        strategy = BBPlatformAdaptationStrategy()

        context = {}
        result = strategy.execute(context)
        # Should return context without crashing
        assert result is context
