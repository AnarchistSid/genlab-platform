"""Tests for RenderGamingVideo stage — no real FFmpeg or video files."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from niches.gaming.stages.render_gaming_video import (
    RenderGamingVideo,
    _slugify,
    get_render_spec,
)


def _make_story(title="Elden Ring", has_clip=True, has_hook=True):
    story = {
        "title": title,
        "source": "steam_spike",
        "score": 0.8,
        "content": {"hook": "Players are losing it rn"} if has_hook else {},
        "media": {},
    }
    if has_clip:
        story["media"]["clip"] = {"file_path": "/tmp/fake_clip.mp4"}
    return story


class TestSlugify:
    def test_basic_slug(self):
        assert _slugify("Elden Ring DLC") == "elden_ring_dlc"

    def test_strips_special_chars(self):
        assert _slugify("What's Next?!") == "whats_next"

    def test_truncation(self):
        assert len(_slugify("a" * 100)) == 40


class TestSkippedStories:
    def test_skips_without_clip(self):
        stage = RenderGamingVideo()
        story = _make_story(has_clip=False)
        context = {"stories": [story], "feature_flags": {}, "niche_config": {}}
        with patch(
            "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test_run")
        ):
            result = stage.execute(context)

        assert result["stories"][0]["media"]["rendered_path"] is None
        assert result["run_stats"]["render"]["skipped"] == 1

    def test_skips_without_hook(self):
        stage = RenderGamingVideo()
        story = _make_story(has_hook=False)
        context = {"stories": [story], "feature_flags": {}, "niche_config": {}}
        with patch(
            "genlab_core.context.get_current_context", return_value=MagicMock(run_id="test_run")
        ):
            result = stage.execute(context)

        assert result["stories"][0]["media"]["rendered_path"] is None
        assert result["run_stats"]["render"]["skipped"] == 1


class TestCompositorRender:
    def test_compositor_success(self):
        """When FrameCompositor succeeds, rendered_path is set and method is 'frame_compositor'."""
        stage = RenderGamingVideo()
        mock_fc = MagicMock()
        mock_fc.compose.return_value = "/tmp/out.mp4"

        story = _make_story()

        with patch.object(stage, "_get_frame_compositor", return_value=mock_fc):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "genlab_core.context.get_current_context",
                    return_value=MagicMock(run_id="test_run"),
                ):
                    context = {
                        "stories": [story],
                        "feature_flags": {},
                        "niche_config": {"run_id": "test_run"},
                    }
                    result = stage.execute(context)

        assert result["stories"][0]["media"]["rendered_path"] is not None
        assert result["run_stats"]["render"]["rendered"] == 1
        assert result["run_stats"]["render"]["method_breakdown"]["frame_compositor"] == 1
        mock_fc.compose.assert_called_once()

    def test_compositor_failure_records_failed(self):
        """When FrameCompositor raises, story gets rendered_path=None and stats show failed.

        ARCH #45 (2026-06-13): VideoCompositor class was collapsed into a
        module-level `derive_landscape` function. We patch the function at
        its call site instead of the deleted `_get_compositor` method.
        """
        stage = RenderGamingVideo()
        mock_fc = MagicMock()
        mock_fc.compose.side_effect = RuntimeError("FFmpeg failed")

        story = _make_story()

        with (
            patch.object(stage, "_get_frame_compositor", return_value=mock_fc),
            patch(
                "niches.gaming.stages.render_gaming_video.derive_landscape",
                side_effect=RuntimeError("FFmpeg failed"),
            ),
        ):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "genlab_core.context.get_current_context",
                    return_value=MagicMock(run_id="test_run"),
                ):
                    context = {
                        "stories": [story],
                        "feature_flags": {},
                        "niche_config": {"run_id": "test_run"},
                    }
                    result = stage.execute(context)

        assert result["stories"][0]["media"]["rendered_path"] is None
        assert result["run_stats"]["render"]["failed"] == 1


class TestCompilationPathPropagation:
    def test_compilation_stories_get_rendered_path(self):
        """When 4 stories go into compilation, all 4 get media.rendered_path set."""
        stage = RenderGamingVideo()

        stories = [_make_story(title=f"Game {i}") for i in range(4)]

        comp_output = "/tmp/rendered/compilation_best_of_day.mp4"

        def fake_render_compilation(stories_arg, run_id, stats, flags):
            # Simulate what the real method does: mark stories and return result
            for s in stories_arg:
                s["_in_compilation"] = True
            stats["rendered"] += 4
            return {
                "compilation_id": "test_comp",
                "rendered_path": comp_output,
                "clip_count": 4,
                "theme": "mixed",
            }

        with patch.object(stage, "_render_compilation", side_effect=fake_render_compilation):
            with patch.object(stage, "_ensure_config"):
                with patch.object(stage, "_get_clip_path", return_value="/tmp/fake_clip.mp4"):
                    with patch(
                        "genlab_core.context.get_current_context",
                        return_value=MagicMock(run_id="test_run"),
                    ):
                        context = {
                            "stories": stories,
                            "feature_flags": {"use_compilation_mode": True},
                            "niche_config": {},
                        }
                        result = stage.execute(context)

        # Only the FIRST compilation story gets rendered_path (prevents duplicate publishes)
        primary = [s for s in result["stories"] if s.get("_compilation_primary")]
        assert len(primary) == 1, "Exactly one story should be compilation primary"
        assert primary[0]["media"]["rendered_path"] == comp_output

        non_primary = [
            s
            for s in result["stories"]
            if s.get("_in_compilation") and not s.get("_compilation_primary")
        ]
        for story in non_primary:
            assert story["media"].get("rendered_path") is None, (
                f"Non-primary story '{story['title']}' should NOT have rendered_path"
            )

        assert result["run_stats"]["render"]["compilation_built"] is True
        assert result["run_stats"]["render"]["rendered"] == 4


class TestCompositorVertical:
    """Verify that vertical (Instagram) rendering calls FrameCompositor.compose."""

    def test_instagram_calls_frame_compositor_compose(self):
        """Instagram render must call FrameCompositor.compose with clip + hook."""
        stage = RenderGamingVideo()
        mock_fc = MagicMock()
        mock_fc.compose.return_value = "/tmp/out.mp4"

        story = _make_story()

        with patch.object(stage, "_get_frame_compositor", return_value=mock_fc):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "genlab_core.context.get_current_context",
                    return_value=MagicMock(run_id="test_run"),
                ):
                    context = {
                        "stories": [story],
                        "feature_flags": {"platforms_enabled": ["instagram"]},
                        "niche_config": {},
                    }
                    stage.execute(context)

        mock_fc.compose.assert_called_once()
        call_kwargs = mock_fc.compose.call_args.kwargs
        assert call_kwargs["hook_text"] == "Players are losing it rn", "Must pass story hook text"


class TestAllPlatformsVertical:
    """All platforms unified to 9:16 vertical for Reels/Shorts growth."""

    def test_get_render_spec_facebook(self):
        """Facebook now gets 9:16 vertical, same as all other platforms."""
        spec = get_render_spec("facebook")
        assert spec.width == 1080
        assert spec.height == 1920
        assert spec.aspect == "9:16"
        assert spec.conversion == "pillarbox_blur"

    def test_get_render_spec_instagram(self):
        spec = get_render_spec("instagram")
        assert spec.width == 1080
        assert spec.height == 1920
        assert spec.aspect == "9:16"
        assert spec.conversion == "pillarbox_blur"

    def test_get_render_spec_x_twitter(self):
        """All platforms unified to 9:16 vertical — x_twitter included."""
        spec = get_render_spec("x_twitter")
        assert spec.width == 1080
        assert spec.height == 1920
        assert spec.conversion == "pillarbox_blur"

    def test_facebook_uses_vertical_not_landscape(self):
        """Facebook now uses 9:16 vertical (same as all platforms), not landscape."""
        stage = RenderGamingVideo()
        mock_fc = MagicMock()
        mock_fc.compose.return_value = "/tmp/vert.mp4"

        story = _make_story()

        with patch.object(stage, "_get_frame_compositor", return_value=mock_fc):
            with patch.object(Path, "exists", return_value=True):
                with patch(
                    "genlab_core.context.get_current_context",
                    return_value=MagicMock(run_id="test_run"),
                ):
                    context = {
                        "stories": [story],
                        "feature_flags": {"platforms_enabled": ["facebook"]},
                        "niche_config": {},
                    }
                    stage.execute(context)

        # Facebook uses vertical master via FrameCompositor
        mock_fc.compose.assert_called_once()


# ── task #615 (2026-07-09): tuple-unpack regression pin ──────────────
#
# `apply_post_render_transformations` returns tuple[str, dict] since
# task #581 (2026-07-08). Gaming's render stage was missed in that
# audit — kept `rendered_path = apply_post_render_transformations(...)`
# which stored the tuple as-is. That flowed into
# `rendered_variants["vertical"]`, then `Path(vpath)` inside
# `validate_platform_variant` raised TypeError caught by the
# defensive except → `valid = False` → hard VideoValidationError,
# blocking every gaming render.
#
# This pin locks the call site pattern so a future refactor can't
# silently un-fix it.


class TestPostRenderTransformCallSitePattern:
    """Source-inspection pins for the intelligent-transform wire in
    `RenderGamingVideo`. Runs as static analysis on the file text —
    no imports, no execution. Cheap and impossible to bypass with
    monkey-patching."""

    _SOURCE_PATH = Path(__file__).resolve().parents[2] / (
        "niches/gaming/stages/render_gaming_video.py"
    )

    def _read(self):
        return self._SOURCE_PATH.read_text()

    def test_unpacks_tuple_not_assigns_whole(self):
        """The bug pattern was:

            rendered_path = apply_post_render_transformations(...)

        which stores the (path, arm_ids) tuple in rendered_path.
        The correct pattern is:

            rendered_path, _arm_ids = apply_post_render_transformations(...)

        This pin FAILS if a future edit reverts to single-target
        assignment."""
        source = self._read()
        # Find the call site — must be preceded by a tuple-unpack LHS.
        bad_pattern = "rendered_path = apply_post_render_transformations("
        good_pattern = "rendered_path, _arm_ids = apply_post_render_transformations("
        assert bad_pattern not in source, (
            "Gaming render must NOT assign the whole tuple to rendered_path. "
            "Task #581 changed apply_post_render_transformations to return "
            "(path, arm_ids); assigning the tuple to rendered_path stores "
            "the tuple in rendered_variants and crashes Path(tuple) at "
            "validation time (task #615)."
        )
        assert good_pattern in source, (
            "Gaming render must unpack the (path, arm_ids) tuple. If the "
            "return type of apply_post_render_transformations changed, "
            "update both this pin and the call site."
        )

    def test_writes_arm_ids_to_story(self):
        """Sibling call sites (base_visual_render.py:218,
        BlackboxBrief.bb_strategies.visual_render.py) write
        arm_ids_by_dimension into the story dict so
        push_to_backlog._build_blueprint_fields can copy it into
        pending_feedback.arm_ids_by_dimension. Gaming must match this
        pattern — otherwise the reward wire ends at the render stage
        and the 255 transformation arms accumulate zero observations
        from gaming."""
        source = self._read()
        assert 'story["arm_ids_by_dimension"] = dict(_arm_ids)' in source, (
            "Gaming render must write _arm_ids into story['arm_ids_by_dimension'] "
            "when non-empty, matching the pattern in "
            "genlab_core.strategies.base_visual_render at line ~218. "
            "Without this, gaming's transformation arms never get "
            "observations even when the orchestrator picks them."
        )
