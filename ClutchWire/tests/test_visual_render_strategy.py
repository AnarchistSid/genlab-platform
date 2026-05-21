"""Tests for ClutchWire visual render strategy."""

from unittest.mock import MagicMock, patch

import pytest
from cw_strategies.visual_render import SportVisualRenderStrategy


@pytest.fixture
def strategy():
    return SportVisualRenderStrategy()


def _make_story(**kwargs):
    base = {
        "title": "Lakers win",
        "sport": "basketball",
        "teams": ["Lakers"],
    }
    base.update(kwargs)
    return base


class TestPexelsQueries:
    def test_basketball_queries(self, strategy):
        queries = strategy._build_pexels_queries(_make_story(sport="basketball"))
        assert len(queries) <= 3
        assert any("basketball" in q.lower() for q in queries)

    def test_unknown_sport_uses_defaults(self, strategy):
        queries = strategy._build_pexels_queries(_make_story(sport="curling"))
        assert len(queries) <= 3
        assert any("sports" in q.lower() for q in queries)

    def test_team_prepended(self, strategy):
        queries = strategy._build_pexels_queries(_make_story(sport="basketball", teams=["Lakers"]))
        assert any("Lakers" in q for q in queries)


class TestVisualExecute:
    def test_execute_renders_all(self, strategy):
        ctx = {"stories": [_make_story(), _make_story()]}
        result = strategy.execute(ctx)
        assert result["run_stats"]["render"]["rendered"] == 2
        assert result["run_stats"]["render"]["overlay_enabled"] is True

    def test_stories_get_media_metadata(self, strategy):
        ctx = {"stories": [_make_story()]}
        result = strategy.execute(ctx)
        media = result["stories"][0]["media"]
        assert "pexels_queries" in media
        assert media["overlay_enabled"] is True

    def test_execute_empty_stories(self, strategy):
        ctx = {"stories": []}
        result = strategy.execute(ctx)
        assert result["run_stats"]["render"]["status"] == "no_stories"

    def test_execute_uses_clip_index_when_available(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_compositor = MagicMock()
        composed_path = str(tmp_path / "composed.mp4")
        mock_compositor.compose.return_value = composed_path

        ctx = {
            "stories": [_make_story(story_id="s1")],
            "clip_index": {
                "clips": {
                    "s1": {"success": True, "clip_path": str(clip_file)},
                },
            },
            "run_dir": str(run_dir),
        }

        with patch("cw_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            result = strategy.execute(ctx)

        media = result["stories"][0]["media"]
        assert media["render_status"] == "video_ready"
        assert media["rendered_path"] == composed_path
        assert result["run_stats"]["render"]["videos_found"] == 1

    def test_execute_falls_back_when_clip_missing(self, strategy):
        ctx = {
            "stories": [_make_story(story_id="s1")],
            "clip_index": {
                "clips": {
                    "s1": {"success": False},
                },
            },
        }
        result = strategy.execute(ctx)
        media = result["stories"][0]["media"]
        assert media["render_status"] == "no_video"
        assert "pexels_queries" in media
        assert result["run_stats"]["render"]["videos_found"] == 0


class TestFrameCompositorWiring:
    """Test FrameCompositor integration in the render strategy."""

    def test_compositor_called_with_correct_args(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_compositor = MagicMock()
        mock_compositor.compose.return_value = str(tmp_path / "composed.mp4")

        ctx = {
            "stories": [
                _make_story(
                    story_id="s1",
                    content={"hook": "Lakers dominate in 4th quarter"},
                )
            ],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("cw_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            result = strategy.execute(ctx)

        MockFC.from_visuals_yaml.assert_called_once()
        mock_compositor.compose.assert_called_once()
        call_kwargs = mock_compositor.compose.call_args
        assert call_kwargs.kwargs["source_video_path"] == str(clip_file)
        assert call_kwargs.kwargs["hook_text"] == "Lakers dominate in 4th quarter"
        assert call_kwargs.kwargs["duration_seconds"] == 55
        assert result["stories"][0]["media"]["rendered_path"] == str(tmp_path / "composed.mp4")

    def test_fallback_on_compositor_exception(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        ctx = {
            "stories": [_make_story(story_id="s1")],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("cw_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value.compose.side_effect = RuntimeError(
                "ffmpeg crashed"
            )
            result = strategy.execute(ctx)

        media = result["stories"][0]["media"]
        assert "rendered_path" not in media
        assert media["render_status"] == "render_failed"

    def test_hook_text_from_content(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_compositor = MagicMock()
        mock_compositor.compose.return_value = "/composed.mp4"

        ctx = {
            "stories": [
                _make_story(
                    story_id="s1",
                    content={"hook": "This is the hook"},
                )
            ],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("cw_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            strategy.execute(ctx)

        assert mock_compositor.compose.call_args.kwargs["hook_text"] == "This is the hook"

    def test_hook_text_fallback_to_title(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        mock_compositor = MagicMock()
        mock_compositor.compose.return_value = "/composed.mp4"

        ctx = {
            "stories": [_make_story(story_id="s1", title="Lakers win big")],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
            "run_dir": str(run_dir),
        }

        with patch("cw_strategies.visual_render.FrameCompositor") as MockFC:
            MockFC.from_visuals_yaml.return_value = mock_compositor
            strategy.execute(ctx)

        assert mock_compositor.compose.call_args.kwargs["hook_text"] == "Lakers win big"

    def test_no_run_dir_marks_render_failed(self, strategy, tmp_path):
        clip_file = tmp_path / "clip.mp4"
        clip_file.write_text("fake video")

        ctx = {
            "stories": [_make_story(story_id="s1")],
            "clip_index": {"clips": {"s1": {"success": True, "clip_path": str(clip_file)}}},
        }

        result = strategy.execute(ctx)
        media = result["stories"][0]["media"]
        assert "rendered_path" not in media
        assert media["render_status"] == "render_failed"
