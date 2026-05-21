"""Tests for SpliceReel visual render strategy."""

from unittest.mock import MagicMock, patch

import pytest
from sr_strategies.visual_render import MovieVisualRenderStrategy


@pytest.fixture
def strategy(tmp_path):
    """Create strategy with test sources config."""
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sources.yaml").write_text(
        yaml.dump(
            {
                "media": {
                    "pexels": {
                        "cinematic_queries": [
                            "cinema movie theater dark",
                            "film clapper board production",
                            "popcorn movie night dark",
                        ],
                    }
                }
            }
        )
    )
    (config_dir / "visuals.yaml").write_text(
        yaml.dump(
            {
                "niche_id": "movies",
                "animation": {
                    "word_by_word": {
                        "whisper_sync": {"enabled": False},
                    }
                },
            }
        )
    )
    with patch("sr_strategies.visual_render.NICHE_ROOT", tmp_path):
        s = MovieVisualRenderStrategy()
        s._ensure_config()
        yield s


class TestPexelsQueries:
    def test_queries_include_film_title(self, strategy):
        story = {"film_title": "Interstellar", "title": "Interstellar"}
        queries = strategy._build_pexels_queries(story)
        assert any("Interstellar" in q for q in queries)
        assert len(queries) <= 3

    def test_queries_fall_back_to_configured(self, strategy):
        story = {"title": "Some news"}
        queries = strategy._build_pexels_queries(story)
        assert len(queries) <= 3
        assert any("cinema" in q for q in queries)

    def test_queries_capped_at_3(self, strategy):
        story = {"film_title": "A Very Long Film Title", "title": "A Very Long Film Title"}
        queries = strategy._build_pexels_queries(story)
        assert len(queries) == 3


class TestVisualRenderExecute:
    def test_execute_sets_render_stats(self, strategy):
        context = {
            "stories": [
                {"title": "Film A", "film_title": "Film A"},
                {"title": "Film B", "film_title": "Film B"},
            ]
        }
        result = strategy.execute(context)
        stats = result["run_stats"]["render"]
        assert stats["rendered"] == 2
        assert stats["overlay_enabled"] is True

    def test_execute_sets_media_metadata(self, strategy):
        context = {"stories": [{"title": "Film A", "film_title": "Film A"}]}
        result = strategy.execute(context)
        media = result["stories"][0]["media"]
        assert "pexels_queries" in media
        assert media["overlay_enabled"] is True
        # Without clip_index, falls back to Pexels queries + no_video status
        assert media["render_status"] == "no_video"

    def test_execute_no_stories_returns_stats(self, strategy):
        context = {"stories": []}
        result = strategy.execute(context)
        assert result["run_stats"]["render"]["status"] == "no_stories"

    def test_execute_uses_clip_index_when_available(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_compositor = MagicMock()
        composed_path = str(tmp_path / "composed.mp4")
        mock_compositor.compose.return_value = composed_path

        context = {
            "stories": [{"story_id": "s1", "title": "Film A", "film_title": "Film A"}],
            "clip_index": {
                "clips": {
                    "s1": {"success": True, "clip_path": str(clip_file)},
                },
            },
            "run_dir": str(run_dir),
        }

        with patch("sr_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            result = strategy.execute(context)

        media = result["stories"][0]["media"]
        assert media["render_status"] == "video_ready"
        assert media["rendered_path"] == composed_path
        assert result["run_stats"]["render"]["videos_found"] == 1

    def test_execute_falls_back_when_clip_missing(self, strategy, tmp_path):
        context = {
            "stories": [{"story_id": "s1", "title": "Film A", "film_title": "Film A"}],
            "clip_index": {
                "clips": {
                    "s1": {"success": False},
                },
            },
        }
        result = strategy.execute(context)
        media = result["stories"][0]["media"]
        assert media["render_status"] == "no_video"
        assert "pexels_queries" in media
        assert result["run_stats"]["render"]["videos_found"] == 0


class TestFrameCompositorWiring:
    """Test FrameCompositor integration in movie visual render strategy."""

    def test_compositor_called_with_correct_args(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_compositor = MagicMock()
        mock_compositor.compose.return_value = str(tmp_path / "composed.mp4")

        context = {
            "stories": [
                {
                    "story_id": "s1",
                    "title": "Film A",
                    "film_title": "Film A",
                    "content": {"hook": "This changes cinema forever"},
                }
            ],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("sr_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            result = strategy.execute(context)

        MockFC.from_visuals_yaml.assert_called_once()
        mock_compositor.compose.assert_called_once()
        assert (
            mock_compositor.compose.call_args.kwargs["hook_text"] == "This changes cinema forever"
        )
        assert mock_compositor.compose.call_args.kwargs["duration_seconds"] == 55
        assert result["stories"][0]["media"]["rendered_path"] == str(tmp_path / "composed.mp4")

    def test_fallback_on_compositor_exception(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        context = {
            "stories": [{"story_id": "s1", "title": "Film A", "film_title": "Film A"}],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("sr_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value.compose.side_effect = RuntimeError(
                "ffmpeg crashed"
            )
            result = strategy.execute(context)

        media = result["stories"][0]["media"]
        assert "rendered_path" not in media
        assert media["render_status"] == "render_failed"

    def test_hook_text_fallback_to_title(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_compositor = MagicMock()
        mock_compositor.compose.return_value = "/composed.mp4"

        context = {
            "stories": [
                {"story_id": "s1", "title": "Top Gun 3 trailer drops", "film_title": "Top Gun 3"}
            ],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("sr_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            strategy.execute(context)

        assert mock_compositor.compose.call_args.kwargs["hook_text"] == "Top Gun 3 trailer drops"

    def test_no_run_dir_marks_render_failed(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")

        context = {
            "stories": [{"story_id": "s1", "title": "Film A", "film_title": "Film A"}],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
        }

        result = strategy.execute(context)
        media = result["stories"][0]["media"]
        assert "rendered_path" not in media
        assert media["render_status"] == "render_failed"
