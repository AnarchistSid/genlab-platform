"""Pin the animated caption overlay (PR 11).

Covers:
  1. Empty segments / zero duration / unknown style → empty filter
  2. Segment time-range partitioning
  3. Emphasis word matching (case + punctuation tolerant)
  4. Emphasis color mapping per arm value
  5. Hormozi style always yellow regardless of emphasis_color arm
  6. Word-by-word: word stays until segment end
  7. Karaoke: word disappears when next word starts
  8. Minimal: whole segment appears at once (single drawtext)
  9. Meme: larger fontsize
 10. Text escaping (single quote, colon, backslash, comma)
 11. Fail-open subprocess wrapper (mocked)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.media.caption_animator import (
    CaptionAnimationSpec,
    _emphasis_color_for,
    _escape_drawtext,
    _fontsize_for,
    _segment_time_ranges,
    _word_is_emphasis,
    apply_captions,
    build_caption_filter_chain,
    build_ffmpeg_command,
)
from genlab_core.writing.caption_segments import CaptionSegment


def _fake_font() -> str:
    return "/tmp/fake_font.ttf"


class TestSegmentTimeRanges:
    def test_three_segments_20s(self) -> None:
        ranges = _segment_time_ranges(3, 20.0)
        assert len(ranges) == 3
        # First segment: 0 to 6.667
        assert ranges[0] == (0.0, pytest.approx(20.0 / 3))
        # Last segment ends at 20
        assert ranges[-1][1] == pytest.approx(20.0)

    def test_zero_duration_empty(self) -> None:
        assert _segment_time_ranges(3, 0) == []

    def test_zero_segments_empty(self) -> None:
        assert _segment_time_ranges(0, 20) == []


class TestEmphasisMatch:
    def test_case_insensitive(self) -> None:
        assert _word_is_emphasis("HELLO", ["hello"]) is True

    def test_punctuation_tolerated_on_word(self) -> None:
        """'FREE!' in word matches 'FREE' in emphasis list."""
        assert _word_is_emphasis("FREE!", ["FREE"]) is True

    def test_punctuation_tolerated_on_emphasis(self) -> None:
        assert _word_is_emphasis("hello", ["hello,"]) is True

    def test_no_match(self) -> None:
        assert _word_is_emphasis("world", ["hello"]) is False

    def test_empty_emphasis_list(self) -> None:
        assert _word_is_emphasis("hello", []) is False


class TestEmphasisColor:
    def test_yellow_arm_yellow(self) -> None:
        assert _emphasis_color_for("yellow", "word_by_word") == "#FFEB3B"

    def test_red_arm_red(self) -> None:
        assert _emphasis_color_for("red", "word_by_word") == "#FF5252"

    def test_none_arm_white(self) -> None:
        """emphasis_color=none means no color pop — emphasis renders white."""
        assert _emphasis_color_for("none", "word_by_word") == "white"

    def test_unknown_arm_defaults_yellow(self) -> None:
        """Unknown color arm falls back to a sensible default."""
        assert _emphasis_color_for("purple_polka_dots", "word_by_word") == "#FFEB3B"

    def test_hormozi_style_forces_yellow(self) -> None:
        """Hormozi is yellow-by-identity — overrides emphasis_color arm."""
        assert _emphasis_color_for("red", "hormozi_yellow") == "#FFEB3B"
        assert _emphasis_color_for("cyan", "hormozi_yellow") == "#FFEB3B"


class TestFontsize:
    def test_meme_larger(self) -> None:
        assert _fontsize_for("meme") == 72

    def test_others_default(self) -> None:
        for style in ("word_by_word", "hormozi_yellow", "karaoke", "minimal"):
            assert _fontsize_for(style) == 56


class TestEscapeDrawtext:
    def test_single_quote(self) -> None:
        assert _escape_drawtext("it's") == "it\\'s"

    def test_colon(self) -> None:
        assert _escape_drawtext("v:1") == "v\\:1"

    def test_comma(self) -> None:
        """Comma splits drawtext filter args → must escape."""
        assert _escape_drawtext("a,b") == "a\\,b"

    def test_backslash(self) -> None:
        assert _escape_drawtext("a\\b") == "a\\\\b"


class TestBuildCaptionFilterChain:
    def _segs(self, *args: tuple[str, list[str]]) -> list[CaptionSegment]:
        return [CaptionSegment(text=t, emphasis_words=e) for t, e in args]

    def test_empty_segments_empty_filter(self) -> None:
        assert build_caption_filter_chain([], "word_by_word", "yellow", 750, 20, _fake_font()) == ""

    def test_zero_duration_empty_filter(self) -> None:
        segs = self._segs(("hello world", []))
        assert (
            build_caption_filter_chain(segs, "word_by_word", "yellow", 750, 0, _fake_font()) == ""
        )

    def test_unknown_style_empty_filter(self) -> None:
        segs = self._segs(("hello world", []))
        assert (
            build_caption_filter_chain(segs, "bogus_style", "yellow", 750, 20, _fake_font()) == ""
        )

    def test_word_by_word_has_per_word_drawtext(self) -> None:
        segs = self._segs(("hello world foo", ["hello"]))
        f = build_caption_filter_chain(segs, "word_by_word", "yellow", 750, 20, _fake_font())
        # 3 drawtext filters, one per word
        assert f.count("drawtext=") == 3
        # 'hello' is emphasis → yellow color
        assert "text='hello'" in f
        assert "#FFEB3B" in f

    def test_word_by_word_word_stays_until_seg_end(self) -> None:
        """word_by_word: word 0's `enable` extends to segment end."""
        segs = self._segs(("hello world foo", []))
        f = build_caption_filter_chain(segs, "word_by_word", "none", 750, 6.0, _fake_font())
        # 1 segment at 6s → seg range (0, 6). Word 0 enable is
        # 'between(t,0.000,6.000)'.
        assert "between(t,0.000,6.000)" in f

    def test_karaoke_word_ends_when_next_starts(self) -> None:
        """karaoke: word 0's enable ends when word 1's enable begins."""
        segs = self._segs(("hello world foo", []))
        f = build_caption_filter_chain(segs, "karaoke", "none", 750, 6.0, _fake_font())
        # Word 0: 0.000 to 0.750
        assert "between(t,0.000,0.750)" in f
        # Word 1: 0.750 to 1.500
        assert "between(t,0.750,1.500)" in f

    def test_minimal_single_drawtext_per_segment(self) -> None:
        """minimal: whole segment as one drawtext, not per word."""
        segs = self._segs(("hello world foo bar", []))
        f = build_caption_filter_chain(segs, "minimal", "yellow", 750, 20, _fake_font())
        # Just 1 drawtext for the 1 segment
        assert f.count("drawtext=") == 1
        assert "text='hello world foo bar'" in f

    def test_meme_style_uses_bigger_fontsize(self) -> None:
        segs = self._segs(("hello world", []))
        f = build_caption_filter_chain(segs, "meme", "yellow", 750, 20, _fake_font())
        assert "fontsize=72" in f

    def test_hormozi_emphasis_yellow_regardless_of_arm(self) -> None:
        segs = self._segs(("hello WORLD foo", ["WORLD"]))
        # Ask for red emphasis, but hormozi_yellow style overrides
        f = build_caption_filter_chain(segs, "hormozi_yellow", "red", 750, 20, _fake_font())
        # Yellow (#FFEB3B) present, red (#FF5252) NOT present
        assert "#FFEB3B" in f
        assert "#FF5252" not in f

    def test_stops_when_pacing_overflows_segment(self) -> None:
        """A 6-word segment at 750ms pacing needs 4.5s. If the segment
        slot is only 2s, we should stop revealing after word 2."""
        segs = self._segs(("one two three four five six", []))
        # 1 segment × 2s duration; at 750ms per word, only words 0-2
        # fit (word 0 at 0s, word 1 at 0.75s, word 2 at 1.5s).
        # Word 3 would start at 2.25s > 2.0 seg_end.
        f = build_caption_filter_chain(segs, "word_by_word", "none", 750, 2.0, _fake_font())
        # 3 drawtexts (words 0, 1, 2)
        assert f.count("drawtext=") == 3


class TestBuildFFmpegCommand:
    def _spec(self, tmp_path: Path) -> CaptionAnimationSpec:
        return CaptionAnimationSpec(
            source_video_path=tmp_path / "source.mp4",
            output_path=tmp_path / "out.mp4",
            segments=[CaptionSegment(text="hi", emphasis_words=[])],
            font_path=_fake_font(),
        )

    def test_bt709_triple(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "myfilter")
        for flag in ("-colorspace", "-color_primaries", "-color_trc"):
            assert cmd[cmd.index(flag) + 1] == "bt709"

    def test_audio_copied_not_reencoded(self, tmp_path: Path) -> None:
        """Captions overlay video only; audio should copy through."""
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "myfilter")
        assert cmd[cmd.index("-c:a") + 1] == "copy"

    def test_libx264_present(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "myfilter")
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert cmd[cmd.index("-crf") + 1] == "20"
        assert cmd[cmd.index("-preset") + 1] == "fast"

    def test_filter_string_passed(self, tmp_path: Path) -> None:
        spec = self._spec(tmp_path)
        cmd = build_ffmpeg_command(spec, "/usr/bin/ffmpeg", "somefilter")
        assert cmd[cmd.index("-vf") + 1] == "somefilter"


class TestApplyCaptions:
    @pytest.fixture
    def paths(self, tmp_path: Path) -> tuple[Path, Path]:
        source = tmp_path / "source.mp4"
        out = tmp_path / "out.mp4"
        source.write_bytes(b"a" * 2000)
        return source, out

    def test_empty_segments_returns_false(self, tmp_path: Path, paths) -> None:
        source, out = paths
        spec = CaptionAnimationSpec(
            source_video_path=source,
            output_path=out,
            segments=[],
            font_path=_fake_font(),
        )
        assert apply_captions(spec) is False

    def test_unknown_style_returns_false(self, tmp_path: Path, paths) -> None:
        source, out = paths
        spec = CaptionAnimationSpec(
            source_video_path=source,
            output_path=out,
            segments=[CaptionSegment(text="hi", emphasis_words=[])],
            style="bogus",
            font_path=_fake_font(),
        )
        assert apply_captions(spec) is False

    def test_missing_source_returns_false(self, tmp_path: Path) -> None:
        spec = CaptionAnimationSpec(
            source_video_path=tmp_path / "no.mp4",
            output_path=tmp_path / "out.mp4",
            segments=[CaptionSegment(text="hi", emphasis_words=[])],
            font_path=_fake_font(),
        )
        assert apply_captions(spec) is False

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_success_path(self, mock_binary, mock_run, tmp_path: Path, paths) -> None:
        source, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"

        def _fake_run(*args, **kwargs):
            out.write_bytes(b"o" * 2048)
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        mock_run.side_effect = _fake_run
        spec = CaptionAnimationSpec(
            source_video_path=source,
            output_path=out,
            segments=[CaptionSegment(text="hello world", emphasis_words=["hello"])],
            font_path=_fake_font(),
        )
        assert apply_captions(spec) is True

    @patch("subprocess.run")
    @patch("genlab_core.media.ffmpeg.get_ffmpeg_binary")
    def test_timeout_returns_false(self, mock_binary, mock_run, tmp_path: Path, paths) -> None:
        source, out = paths
        mock_binary.return_value = "/usr/bin/ffmpeg"
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=300)
        spec = CaptionAnimationSpec(
            source_video_path=source,
            output_path=out,
            segments=[CaptionSegment(text="hi", emphasis_words=[])],
            font_path=_fake_font(),
        )
        assert apply_captions(spec, timeout_seconds=1) is False


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
