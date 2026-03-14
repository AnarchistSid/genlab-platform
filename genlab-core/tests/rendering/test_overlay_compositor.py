"""Tests for shared FFmpeg overlay compositor."""

from unittest.mock import patch, MagicMock


from genlab_core.rendering.overlay_compositor import (
    OverlaySpec,
    composite_overlay,
    _escape_drawtext,
)


class TestOverlaySpec:
    def test_sports_score_creates_single_overlay(self):
        spec = OverlaySpec.sports_score("Lakers", "Celtics", 112, 108)
        assert len(spec.overlays) == 1
        assert "Lakers" in spec.overlays[0].text
        assert "Celtics" in spec.overlays[0].text
        assert "112" in spec.overlays[0].text

    def test_film_rating_creates_title_and_score_overlays(self):
        spec = OverlaySpec.film_rating("Sinners", rt_score=99, imdb_score=8.1)
        assert len(spec.overlays) == 2
        assert "Sinners" in spec.overlays[0].text
        assert "99%" in spec.overlays[1].text
        assert "8.1" in spec.overlays[1].text

    def test_film_rating_with_missing_rt_score(self):
        spec = OverlaySpec.film_rating("Test Film", rt_score=None, imdb_score=7.5)
        assert len(spec.overlays) == 2
        assert "7.5" in spec.overlays[1].text
        assert "%" not in spec.overlays[1].text

    def test_film_rating_no_scores_omits_score_overlay(self):
        spec = OverlaySpec.film_rating("Test Film", rt_score=None, imdb_score=None)
        assert len(spec.overlays) == 1  # title only


class TestCompositeOverlay:
    def test_returns_input_when_ffmpeg_not_found(self, tmp_path):
        input_vid = tmp_path / "input.mp4"
        input_vid.write_bytes(b"fake video")
        output_vid = tmp_path / "output.mp4"
        spec = OverlaySpec.sports_score("Team A", "Team B", 3, 1)

        with patch("genlab_core.rendering.overlay_compositor.shutil.which", return_value=None):
            result = composite_overlay(input_vid, output_vid, spec)

        assert result == input_vid

    def test_returns_input_when_source_missing(self, tmp_path):
        missing = tmp_path / "nonexistent.mp4"
        output = tmp_path / "output.mp4"
        spec = OverlaySpec.sports_score("A", "B", 1, 0)
        result = composite_overlay(missing, output, spec)
        assert result == missing

    def test_returns_input_on_ffmpeg_nonzero_exit(self, tmp_path):
        input_vid = tmp_path / "input.mp4"
        input_vid.write_bytes(b"fake video")
        output_vid = tmp_path / "output.mp4"
        spec = OverlaySpec.sports_score("Team A", "Team B", 2, 0)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "ffmpeg error"

        with patch("genlab_core.rendering.overlay_compositor.shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("genlab_core.rendering.overlay_compositor.subprocess.run", return_value=mock_result):
            result = composite_overlay(input_vid, output_vid, spec)

        assert result == input_vid

    def test_returns_output_on_success(self, tmp_path):
        input_vid = tmp_path / "input.mp4"
        output_vid = tmp_path / "output.mp4"
        input_vid.write_bytes(b"fake video")
        output_vid.write_bytes(b"overlaid video")
        spec = OverlaySpec.sports_score("Team A", "Team B", 2, 1)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("genlab_core.rendering.overlay_compositor.shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("genlab_core.rendering.overlay_compositor.subprocess.run", return_value=mock_result):
            result = composite_overlay(input_vid, output_vid, spec)

        assert result == output_vid

    def test_special_chars_in_text_are_escaped(self, tmp_path):
        input_vid = tmp_path / "input.mp4"
        input_vid.write_bytes(b"fake video")
        output_vid = tmp_path / "output.mp4"
        spec = OverlaySpec.sports_score("St. Louis: Blues", "O'Brien's FC", 3, 2)

        captured_cmd: list[str] = []

        def capture_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            r = MagicMock()
            r.returncode = 0
            return r

        with patch("genlab_core.rendering.overlay_compositor.shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("genlab_core.rendering.overlay_compositor.subprocess.run", side_effect=capture_run):
            composite_overlay(input_vid, output_vid, spec)

        assert "ffmpeg" in captured_cmd[0]

    def test_no_overlays_returns_input_without_ffmpeg_call(self, tmp_path):
        input_vid = tmp_path / "input.mp4"
        input_vid.write_bytes(b"fake")
        output_vid = tmp_path / "output.mp4"
        spec = OverlaySpec()

        with patch("genlab_core.rendering.overlay_compositor.subprocess.run") as mock_run:
            result = composite_overlay(input_vid, output_vid, spec)

        mock_run.assert_not_called()
        assert result == input_vid


class TestEscapeDrawtext:
    def test_escapes_colons(self):
        assert "\\:" in _escape_drawtext("Team: Name")

    def test_escapes_apostrophes(self):
        assert "\\'" in _escape_drawtext("O'Brien")
