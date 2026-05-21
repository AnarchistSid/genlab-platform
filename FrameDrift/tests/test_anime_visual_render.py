"""Tests for FrameDrift visual render strategy — brand safety."""

from unittest.mock import MagicMock, patch

import pytest
from fd_strategies.visual_render import AnimeVisualRenderStrategy


@pytest.fixture
def strategy(tmp_path):
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sources.yaml").write_text(
        yaml.dump(
            {
                "media": {
                    "pexels": {
                        "anime_queries": [
                            "anime aesthetic lifestyle urban",
                            "streetwear lifestyle outfit",
                            "anime editorial aesthetic",
                        ],
                    }
                }
            }
        )
    )
    (config_dir / "visuals.yaml").write_text(yaml.dump({}))
    with patch("fd_strategies.visual_render.NICHE_ROOT", tmp_path):
        s = AnimeVisualRenderStrategy()
        s._ensure_config()
        yield s


class TestBrandSafety:
    def test_no_brand_names_in_pexels_queries(self, strategy):
        story = {"title": "Nike Air Max 90 Drop"}
        queries = strategy._build_pexels_queries(story)
        for q in queries:
            assert "nike" not in q.lower()
            assert "adidas" not in q.lower()
            assert "gucci" not in q.lower()

    def test_brand_name_in_config_replaced_with_fallback(self, tmp_path):
        import yaml

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "sources.yaml").write_text(
            yaml.dump(
                {
                    "media": {
                        "pexels": {
                            "anime_queries": [
                                "anime aesthetic sakura",
                                "anime aesthetic lifestyle urban",
                            ],
                        }
                    }
                }
            )
        )
        (config_dir / "visuals.yaml").write_text(yaml.dump({}))
        with patch("fd_strategies.visual_render.NICHE_ROOT", tmp_path):
            s = AnimeVisualRenderStrategy()
            s._sources_config = None
            s._ensure_config()
            queries = s._build_pexels_queries({})
            for q in queries:
                assert "nike" not in q.lower()


class TestVisualRenderExecute:
    def test_execute_sets_render_stats(self, strategy):
        context = {
            "stories": [
                {"title": "Story A"},
                {"title": "Story B"},
            ]
        }
        result = strategy.execute(context)
        stats = result["run_stats"]["render"]
        assert stats["rendered"] == 2
        assert stats["overlay_enabled"] is False

    def test_no_overlay_for_anime(self, strategy):
        context = {"stories": [{"title": "Story A"}]}
        result = strategy.execute(context)
        media = result["stories"][0]["media"]
        assert media["overlay_enabled"] is False

    def test_execute_uses_clip_index_when_available(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_compositor = MagicMock()
        composed_path = str(tmp_path / "composed.mp4")
        mock_compositor.compose.return_value = composed_path

        context = {
            "stories": [{"story_id": "s1", "title": "Story A"}],
            "clip_index": {
                "clips": {
                    "s1": {"success": True, "clip_path": str(clip_file)},
                },
            },
            "run_dir": str(run_dir),
        }

        with patch("fd_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            result = strategy.execute(context)

        media = result["stories"][0]["media"]
        assert media["render_status"] == "video_ready"
        assert media["rendered_path"] == composed_path
        assert result["run_stats"]["render"]["videos_found"] == 1

    def test_execute_falls_back_when_clip_missing(self, strategy):
        context = {
            "stories": [{"story_id": "s1", "title": "Story A"}],
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
    """Test FrameCompositor integration in anime visual render strategy."""

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
                    "title": "Story A",
                    "content": {"hook": "Gojo returns in new episode"},
                }
            ],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("fd_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            result = strategy.execute(context)

        MockFC.from_visuals_yaml.assert_called_once()
        mock_compositor.compose.assert_called_once()
        assert (
            mock_compositor.compose.call_args.kwargs["hook_text"] == "Gojo returns in new episode"
        )
        assert mock_compositor.compose.call_args.kwargs["duration_seconds"] == 55
        assert result["stories"][0]["media"]["rendered_path"] == str(tmp_path / "composed.mp4")

    def test_fallback_on_compositor_exception(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        context = {
            "stories": [{"story_id": "s1", "title": "Story A"}],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("fd_strategies.visual_render.FrameCompositor") as MockFC:
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
            "stories": [{"story_id": "s1", "title": "Attack on Titan finale"}],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("fd_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            strategy.execute(context)

        assert mock_compositor.compose.call_args.kwargs["hook_text"] == "Attack on Titan finale"

    def test_no_run_dir_marks_render_failed(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")

        context = {
            "stories": [{"story_id": "s1", "title": "Story A"}],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
        }

        result = strategy.execute(context)
        media = result["stories"][0]["media"]
        assert "rendered_path" not in media
        assert media["render_status"] == "render_failed"
