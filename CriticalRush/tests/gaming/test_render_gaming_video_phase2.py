"""Phase 2 tests for compilation assembly, FFmpeg utilities, and render stage.

Tests cover:
  - ffmpeg_filters: scale_crop, position_to_xy, build_drawtext, build_xfade_transition, XFADE_MAP
  - ffmpeg_utils: concat_with_transitions offset calculation (the bug fix)
  - compilation_planner: filter_eligible, select_clips, pacing, transitions, trim, diversity, commentary
  - video_hasher: hamming_distance, HashStore
  - render_gaming_video: dual-path decision logic
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# ffmpeg_filters tests
# ---------------------------------------------------------------------------


class TestFFmpegFilters:
    def test_xfade_map_has_expected_keys(self):
        from niches.gaming.media.ffmpeg_gaming import XFADE_MAP

        assert "hard_cut" in XFADE_MAP
        assert "zoom_impact" in XFADE_MAP
        assert "glitch" in XFADE_MAP
        assert "white_flash" in XFADE_MAP
        assert "chromatic_aberration" in XFADE_MAP
        assert XFADE_MAP["hard_cut"] is None

    def test_build_scale_crop_near_target_ratio(self):
        from genlab_core.media.ffmpeg_utils import build_scale_crop

        # 9:16 source → should just scale
        result = build_scale_crop(1080, 1920, 1080, 1920)
        assert "scale=1080:1920" in result

    def test_build_scale_crop_landscape_to_portrait(self):
        from genlab_core.media.ffmpeg_utils import build_scale_crop

        # 16:9 → 9:16 crop
        result = build_scale_crop(1920, 1080, 1080, 1920)
        assert "crop=" in result

    def test_build_scale_crop_pillarbox_mode(self):
        from genlab_core.media.ffmpeg_utils import build_scale_crop

        result = build_scale_crop(1920, 1080, 1080, 1920, mode="pillarbox")
        assert "boxblur" in result
        assert "overlay" in result

    def test_position_to_xy_known_positions(self):
        from genlab_core.media.ffmpeg_utils import position_to_xy

        x, y = position_to_xy("center")
        assert "w-text_w" in x
        assert "h-text_h" in y

        x, y = position_to_xy("bottom_left", margin_x=40, margin_y=120)
        assert x == "40"
        assert "120" in y

    def test_position_to_xy_unknown_defaults_to_zero(self):
        from genlab_core.media.ffmpeg_utils import position_to_xy

        x, y = position_to_xy("nonexistent")
        assert x == "0"
        assert y == "0"

    def test_build_drawtext_basic(self):
        from genlab_core.media.ffmpeg_utils import build_drawtext

        result = build_drawtext(
            text="Hello",
            font_file="/font.ttf",
            size=48,
            color="white",
            x="0",
            y="0",
            start=0.0,
            end=3.0,
        )
        assert "drawtext=" in result
        assert "fontfile=/font.ttf" in result
        assert "fontsize=48" in result
        assert "between(t,0.0,3.0)" in result

    def test_build_drawtext_with_stroke(self):
        from genlab_core.media.ffmpeg_utils import build_drawtext

        result = build_drawtext(
            text="Test",
            font_file="/f.ttf",
            size=36,
            color="white",
            x="0",
            y="0",
            start=0,
            end=3,
            stroke_color="black",
            stroke_width=2,
        )
        assert "bordercolor=black" in result
        assert "borderw=2" in result

    def test_build_drawtext_escapes_special_chars(self):
        from genlab_core.media.ffmpeg_utils import build_drawtext

        result = build_drawtext(
            text="it's a: [test]",
            font_file="/f.ttf",
            size=36,
            color="white",
            x="0",
            y="0",
            start=0,
            end=3,
        )
        assert "\\:" in result
        assert "\\[" in result
        assert "\\]" in result

    def test_build_xfade_hard_cut_returns_none(self):
        from niches.gaming.media.ffmpeg_gaming import build_xfade_transition

        assert build_xfade_transition("hard_cut") is None

    def test_build_xfade_zoom_impact(self):
        from niches.gaming.media.ffmpeg_gaming import build_xfade_transition

        result = build_xfade_transition("zoom_impact", duration=0.5, offset=10.0)
        assert "smoothup" in result
        assert "duration=0.5" in result
        assert "offset=10.0" in result

    def test_build_xfade_unknown_falls_back_to_fade(self):
        from niches.gaming.media.ffmpeg_gaming import build_xfade_transition

        result = build_xfade_transition("unknown_transition", duration=0.3, offset=5.0)
        assert "fade" in result

    def test_build_loudnorm_filter(self):
        from genlab_core.media.ffmpeg_utils import build_loudnorm_filter

        result = build_loudnorm_filter(target_i=-14.0, target_tp=-1.5, target_lra=11.0)
        assert "loudnorm" in result
        assert "I=-14.0" in result


# ---------------------------------------------------------------------------
# concat_with_transitions offset bug fix tests
# ---------------------------------------------------------------------------


class TestConcatOffsetFix:
    """Verify the offset calculation fix in concat_with_transitions.

    We mock subprocess.run and get_duration to test the filter graph
    construction without needing real video files.
    """

    def _extract_offsets(self, filter_complex: str) -> list[float]:
        """Extract offset values from a filter_complex string."""
        import re

        return [float(m) for m in re.findall(r"offset=([\d.]+)", filter_complex)]

    @patch("niches.gaming.media.ffmpeg_gaming.probe_media")
    @patch("niches.gaming.media.ffmpeg_gaming.get_duration")
    @patch("niches.gaming.media.ffmpeg_gaming.subprocess.run")
    def test_all_real_transitions_offsets_correct(self, mock_run, mock_dur, mock_probe):
        """3 clips with real transitions — offsets should subtract overlap."""
        from niches.gaming.media.ffmpeg_gaming import concat_with_transitions

        mock_dur.side_effect = [10.0, 10.0, 10.0]
        mock_probe.return_value = {"audio": {"channels": 2, "codec": "aac", "sample_rate": 44100}}
        mock_run.return_value = MagicMock(returncode=0)

        transitions = [
            {"type": "zoom_impact", "duration": 0.5},
            {"type": "white_flash", "duration": 0.5},
        ]

        concat_with_transitions(
            ["/a.mp4", "/b.mp4", "/c.mp4"],
            "/out.mp4",
            transitions,
        )

        # Extract the filter_complex argument
        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args.kwargs.get("cmd", [])
        fc_idx = cmd.index("-filter_complex") + 1
        filter_complex = cmd[fc_idx]

        offsets = self._extract_offsets(filter_complex)
        # Clip 1 (10s) → transition at offset=10-0.5=9.5
        # After transition, effective offset = 10-0.5 = 9.5
        # Clip 2 (10s) → offset = 9.5+10-0.5 = 19.0
        assert len(offsets) == 2
        assert abs(offsets[0] - 9.5) < 0.01
        assert abs(offsets[1] - 19.0) < 0.01

    @patch("niches.gaming.media.ffmpeg_gaming.probe_media")
    @patch("niches.gaming.media.ffmpeg_gaming.get_duration")
    @patch("niches.gaming.media.ffmpeg_gaming.subprocess.run")
    def test_hard_cut_in_mixed_chain_subtracts_fake_overlap(self, mock_run, mock_dur, mock_probe):
        """The bug fix: hard_cut in mixed chain must subtract 0.001."""
        from niches.gaming.media.ffmpeg_gaming import concat_with_transitions

        mock_dur.side_effect = [10.0, 10.0, 10.0]
        mock_probe.return_value = {"audio": {"channels": 2, "codec": "aac", "sample_rate": 44100}}
        mock_run.return_value = MagicMock(returncode=0)

        transitions = [
            {"type": "hard_cut", "duration": 0.0},
            {"type": "zoom_impact", "duration": 0.5},
        ]

        concat_with_transitions(
            ["/a.mp4", "/b.mp4", "/c.mp4"],
            "/out.mp4",
            transitions,
        )

        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args.kwargs.get("cmd", [])
        fc_idx = cmd.index("-filter_complex") + 1
        filter_complex = cmd[fc_idx]

        offsets = self._extract_offsets(filter_complex)
        # Clip 1 (10s) hard_cut: offset = 10 - 0.001 = 9.999
        # After: current_offset = 10 - 0.001 = 9.999
        # Clip 2 (10s) zoom_impact: offset = 9.999 + 10 - 0.5 = 19.499
        assert len(offsets) == 2
        assert abs(offsets[0] - 9.999) < 0.01
        assert abs(offsets[1] - 19.499) < 0.01

    @patch("niches.gaming.media.ffmpeg_gaming.concat")
    def test_all_hard_cut_uses_simple_concat(self, mock_concat):
        """When all transitions are hard_cut, should use simple concat."""
        from niches.gaming.media.ffmpeg_gaming import concat_with_transitions

        mock_concat.return_value = True
        transitions = [
            {"type": "hard_cut", "duration": 0.0},
            {"type": "hard_cut", "duration": 0.0},
        ]

        result = concat_with_transitions(
            ["/a.mp4", "/b.mp4", "/c.mp4"],
            "/out.mp4",
            transitions,
        )
        assert result is True
        mock_concat.assert_called_once()

    def test_single_clip_copies_file(self):
        """Single clip should just be copied."""
        from niches.gaming.media.ffmpeg_gaming import concat_with_transitions

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as src:
            src.write(b"fake video data")
            src_path = src.name

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as dst:
            dst_path = dst.name

        try:
            result = concat_with_transitions([src_path], dst_path, [])
            assert result is True
            assert Path(dst_path).read_bytes() == b"fake video data"
        finally:
            Path(src_path).unlink(missing_ok=True)
            Path(dst_path).unlink(missing_ok=True)

    def test_empty_clips_returns_false(self):
        from niches.gaming.media.ffmpeg_gaming import concat_with_transitions

        assert concat_with_transitions([], "/out.mp4", []) is False


# ---------------------------------------------------------------------------
# compilation_planner tests
# ---------------------------------------------------------------------------


def _make_clip(clip_id, score, game, duration=10, tier="GREEN", local_path="/clip.mp4"):
    return {
        "clip_id": clip_id,
        "final_score": score,
        "game_title": game,
        "duration_seconds": duration,
        "publisher_tier": tier,
        "local_path": local_path,
        "title": f"Clip {clip_id}",
        "description": "",
    }


class TestCompilationPlanner:
    def test_filter_eligible_excludes_red_tier(self):
        from niches.gaming.tools.compilation_planner import filter_eligible_clips

        clips = [
            _make_clip("a", 0.8, "Valorant", tier="RED"),
            _make_clip("b", 0.7, "CS2", tier="GREEN"),
        ]
        result = filter_eligible_clips(clips)
        assert len(result) == 1
        assert result[0]["clip_id"] == "b"

    def test_filter_eligible_excludes_no_local_path(self):
        from niches.gaming.tools.compilation_planner import filter_eligible_clips

        clips = [
            _make_clip("a", 0.8, "Valorant", local_path=None),
            _make_clip("b", 0.7, "CS2"),
        ]
        result = filter_eligible_clips(clips)
        assert len(result) == 1

    def test_filter_eligible_excludes_zero_duration(self):
        from niches.gaming.tools.compilation_planner import filter_eligible_clips

        clips = [
            _make_clip("a", 0.8, "Valorant", duration=0),
        ]
        assert filter_eligible_clips(clips) == []

    def test_select_clips_diversity_first(self):
        from niches.gaming.tools.compilation_planner import select_clips_for_compilation

        clips = [
            _make_clip("a", 0.9, "Valorant"),
            _make_clip("b", 0.85, "CS2"),
            _make_clip("c", 0.8, "Fortnite"),
            _make_clip("d", 0.7, "Valorant"),
            _make_clip("e", 0.6, "Apex"),
        ]
        rules = {
            "compilation_types": {
                "short_compilation": {
                    "min_clips": 3,
                    "max_clips": 5,
                    "target_duration_seconds": 45,
                }
            }
        }
        selected = select_clips_for_compilation(clips, "short_compilation", rules)
        games = {c["game_title"] for c in selected}
        assert len(games) >= 3

    def test_select_clips_returns_empty_when_too_few(self):
        from niches.gaming.tools.compilation_planner import select_clips_for_compilation

        clips = [_make_clip("a", 0.9, "Valorant")]
        rules = {
            "compilation_types": {
                "short_compilation": {"min_clips": 4, "max_clips": 8, "target_duration_seconds": 45}
            }
        }
        assert select_clips_for_compilation(clips, "short_compilation", rules) == []

    def test_order_by_pacing_escalating(self):
        from niches.gaming.tools.compilation_planner import order_by_pacing

        clips = [
            _make_clip("a", 0.9, "V"),
            _make_clip("b", 0.5, "V"),
            _make_clip("c", 0.7, "V"),
        ]
        result = order_by_pacing(clips, "escalating")
        scores = [c["final_score"] for c in result]
        assert scores == sorted(scores)

    def test_order_by_pacing_front_loaded(self):
        from niches.gaming.tools.compilation_planner import order_by_pacing

        clips = [
            _make_clip("a", 0.5, "V"),
            _make_clip("b", 0.9, "V"),
            _make_clip("c", 0.7, "V"),
        ]
        result = order_by_pacing(clips, "front_loaded")
        assert result[0]["final_score"] == 0.9  # best first

    def test_assign_transitions_first_clip_is_hard_cut(self):
        from niches.gaming.tools.compilation_planner import assign_transitions

        clips = [_make_clip("a", 0.9, "V"), _make_clip("b", 0.8, "V")]
        weights = {"hard_cut": 0.6, "zoom_impact": 0.4}
        result = assign_transitions(clips, weights)
        assert result[0]["transition_in"] == "hard_cut"

    def test_calculate_trim_points_caps_long_clips(self):
        from niches.gaming.tools.compilation_planner import calculate_trim_points

        clips = [_make_clip("a", 0.9, "V", duration=60)]
        result = calculate_trim_points(clips, target_duration=45, max_single_ratio=0.35)
        assert result[0]["effective_duration_seconds"] <= 45 * 0.35

    def test_generate_commentary_slots_minimum_lines(self):
        from niches.gaming.tools.compilation_planner import generate_commentary_slots

        clips = [
            {**_make_clip("a", 0.9, "V"), "effective_duration_seconds": 10},
            {**_make_clip("b", 0.8, "V"), "effective_duration_seconds": 10},
            {**_make_clip("c", 0.7, "V"), "effective_duration_seconds": 10},
        ]
        slots = generate_commentary_slots(clips, min_lines=3)
        assert len(slots) >= 3
        assert slots[0]["style"] == "intro"
        assert slots[-1]["style"] == "outro"

    def test_compute_diversity_score(self):
        from niches.gaming.tools.compilation_planner import compute_diversity_score

        clips_3_games = [
            _make_clip("a", 0.9, "Valorant"),
            _make_clip("b", 0.8, "CS2"),
            _make_clip("c", 0.7, "Fortnite"),
        ]
        assert compute_diversity_score(clips_3_games) == 1.15

        clips_1_game = [_make_clip("a", 0.9, "Valorant")]
        assert compute_diversity_score(clips_1_game) == 1.0

    def test_compute_pacing_score_perfect_escalation(self):
        from niches.gaming.tools.compilation_planner import compute_pacing_score

        clips = [
            _make_clip("a", 0.5, "V"),
            _make_clip("b", 0.7, "V"),
            _make_clip("c", 0.9, "V"),
        ]
        assert compute_pacing_score(clips) == 1.0

    def test_build_compilation_plan_returns_dict(self):
        from niches.gaming.tools.compilation_planner import build_compilation_plan

        clips = [
            _make_clip("a", 0.9, "Valorant", duration=10),
            _make_clip("b", 0.85, "CS2", duration=10),
            _make_clip("c", 0.8, "Fortnite", duration=10),
            _make_clip("d", 0.7, "Apex", duration=10),
        ]
        rules = {
            "compilation_types": {
                "short_compilation": {
                    "min_clips": 3,
                    "max_clips": 8,
                    "target_duration_seconds": 45,
                    "max_single_clip_ratio": 0.35,
                    "pacing": "escalating",
                    "min_commentary_lines": 3,
                }
            },
            "transitions": {"hard_cut": 0.6, "zoom_impact": 0.4},
        }
        plan = build_compilation_plan(clips, "short_compilation", rules, {})
        assert plan is not None
        assert "compilation_id" in plan
        assert "clips" in plan
        assert len(plan["clips"]) >= 3
        assert plan["diversity_score"] == 1.15

    def test_compilation_id_is_deterministic(self):
        from niches.gaming.tools.compilation_planner import _compilation_id

        id1 = _compilation_id("fails", "2025-01-01", 1)
        id2 = _compilation_id("fails", "2025-01-01", 1)
        assert id1 == id2
        id3 = _compilation_id("fails", "2025-01-01", 2)
        assert id1 != id3

    def test_detect_clip_theme(self):
        from niches.gaming.tools.compilation_planner import detect_clip_theme

        theme_config = {
            "fails": {"keywords": ["fail", "died"]},
            "clutch": {"keywords": ["clutch", "ace"]},
            "funny": {"keywords": ["funny", "lol"]},
        }
        clip_fail = {"title": "Epic fail compilation", "description": ""}
        assert detect_clip_theme(clip_fail, theme_config) == "fails"

        clip_mixed = {"title": "Normal gameplay", "description": ""}
        assert detect_clip_theme(clip_mixed, theme_config) == "mixed"


# ---------------------------------------------------------------------------
# video_hasher tests
# ---------------------------------------------------------------------------


class TestVideoHasher:
    def test_hamming_distance_identical(self):
        from niches.gaming.tools.video_hasher import hamming_distance

        assert hamming_distance("abcdef1234567890", "abcdef1234567890") == 0

    def test_hamming_distance_single_bit(self):
        from niches.gaming.tools.video_hasher import hamming_distance

        # 0x0 vs 0x1 = 1 bit difference
        assert hamming_distance("0", "1") == 1

    def test_hamming_distance_all_different(self):
        from niches.gaming.tools.video_hasher import hamming_distance

        # 0x0 vs 0xf = 4 bits
        assert hamming_distance("0", "f") == 4

    def test_hash_store_add_and_find(self):
        from niches.gaming.tools.video_hasher import HashStore

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store_path = f.name
        # Remove the empty file so HashStore.load() starts fresh
        Path(store_path).unlink()

        try:
            store = HashStore(store_path)
            store.load()
            assert len(store.entries) == 0

            store.add("clip_1", "abcdef1234567890", "https://example.com/1")
            store.save()

            # Reload
            store2 = HashStore(store_path)
            store2.load()
            assert len(store2.entries) == 1

            # Find near-duplicate (same hash)
            dups = store2.find_near_duplicates("abcdef1234567890", threshold=10)
            assert len(dups) == 1
            assert dups[0]["clip_id"] == "clip_1"
            assert dups[0]["hamming_distance"] == 0
        finally:
            Path(store_path).unlink(missing_ok=True)

    def test_hash_store_no_false_positive(self):
        from niches.gaming.tools.video_hasher import HashStore

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            store_path = f.name

        try:
            store = HashStore(store_path)
            store.add("clip_1", "0000000000000000", "url")
            # Very different hash
            dups = store.find_near_duplicates("ffffffffffffffff", threshold=10)
            assert len(dups) == 0
        finally:
            Path(store_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# render_gaming_video dual-path tests
# ---------------------------------------------------------------------------


class TestRenderDualPath:
    def test_compilation_mode_requires_3_clips(self):
        """With <3 clips with paths, should fall back to single-clip mode."""
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        stage = RenderGamingVideo()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            clip_path = f.name

        try:
            stories = [
                {
                    "title": "Test Story",
                    "media": {"clip": {"file_path": clip_path}},
                    "content": {"hook": "Test hook"},
                },
            ]
            context = {
                "stories": stories,
                "feature_flags": {"use_compilation_mode": True},
            }

            with patch.object(stage, "_render_with_ffmpeg", return_value=clip_path):
                with patch("genlab_core.context.get_current_context") as mock_ctx:
                    mock_ctx.return_value = MagicMock(run_id="test_run")
                    result = stage.execute(context)

            # Should NOT have built a compilation
            assert result["run_stats"]["render"]["compilation_built"] is False
        finally:
            Path(clip_path).unlink(missing_ok=True)

    def test_compilation_mode_disabled_by_flag(self):
        """When use_compilation_mode=False, always use single-clip path."""
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        stage = RenderGamingVideo()

        mock_fc = MagicMock()
        mock_fc.compose.return_value = "/tmp/out.mp4"

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            clip_path = f.name

        try:
            stories = [
                {
                    "title": f"Story {i}",
                    "media": {"clip": {"file_path": clip_path}},
                    "content": {"hook": f"Hook {i}"},
                }
                for i in range(5)
            ]
            context = {
                "stories": stories,
                "feature_flags": {"use_compilation_mode": False},
            }

            with patch.object(stage, "_get_frame_compositor", return_value=mock_fc):
                with patch.object(Path, "exists", return_value=True):
                    with patch("genlab_core.context.get_current_context") as mock_ctx:
                        mock_ctx.return_value = MagicMock(run_id="test_run")
                        result = stage.execute(context)

            assert result["run_stats"]["render"]["compilation_built"] is False
            assert result["run_stats"]["render"]["rendered"] == 5
        finally:
            Path(clip_path).unlink(missing_ok=True)

    def test_get_clip_path_returns_none_for_missing_clip(self):
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        stage = RenderGamingVideo()
        assert stage._get_clip_path({}) is None
        assert stage._get_clip_path({"media": {}}) is None
        assert stage._get_clip_path({"media": {"clip": None}}) is None

    def test_get_clip_path_returns_path_for_existing_file(self):
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        stage = RenderGamingVideo()

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"fake")
            clip_path = f.name

        try:
            story = {"media": {"clip": {"file_path": clip_path}}}
            assert stage._get_clip_path(story) == clip_path
        finally:
            Path(clip_path).unlink(missing_ok=True)

    def test_skipped_when_no_clip_and_no_hook(self):
        from niches.gaming.stages.render_gaming_video import RenderGamingVideo

        stage = RenderGamingVideo()

        stories = [{"title": "No media", "media": {}, "content": {}}]
        context = {
            "stories": stories,
            "feature_flags": {},
        }
        with patch("genlab_core.context.get_current_context") as mock_ctx:
            mock_ctx.return_value = MagicMock(run_id="test_run")
            result = stage.execute(context)

        assert result["run_stats"]["render"]["skipped"] == 1
