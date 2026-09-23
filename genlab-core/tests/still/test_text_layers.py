"""Pins for the two text layers (ANIME-14 §1, §3)."""
from __future__ import annotations

import subprocess

import pytest

from genlab_core.still import text_layers as TL


class TestHookBand:
    def test_a_sentence_is_not_a_hook(self):
        with pytest.raises(TL.HookRejected):
            TL.Hook("Firefly Wedding premieres in October this year on Friday")

    def test_one_word_is_not_a_hook_either(self):
        with pytest.raises(TL.HookRejected):
            TL.Hook("Assassin")

    def test_a_tension_line_passes(self):
        assert TL.Hook("She married her assassin").text

    def test_onset_past_the_gate_is_refused(self):
        with pytest.raises(TL.HookRejected):
            TL.Hook("She married her assassin", onset_s=2.0)


class TestFitting:
    def test_the_hook_that_clipped_off_both_edges(self):
        """At 106px "SHE MARRIED HER ASSASSIN" is 1474px on a 1080px frame, so
        x=(w-text_w)/2 is NEGATIVE and it clips at both ends. The caption path
        learned this in ANIME-12; the hook path was written without it."""
        lines, size = TL.fit_lines("SHE MARRIED HER ASSASSIN", max_size=118)
        widest = max(len(ln) for ln in lines) * TL.CHAR_W_RATIO * size
        assert widest <= TL.FRAME_W * TL.MAX_TEXT_WIDTH_FRAC
        assert len(lines) == 2

    def test_it_takes_the_largest_size_not_the_first_that_fits(self):
        """One line at 69px fits. Two lines at 118px also fit and are better:
        the hook is the biggest text in the reel."""
        _, size = TL.fit_lines("SHE MARRIED HER ASSASSIN", max_size=118)
        assert size == 118

    def test_a_short_hook_stays_on_one_line(self):
        lines, _ = TL.fit_lines("HE LIED", max_size=118)
        assert lines == ["HE LIED"]


class TestRendersWithoutCrashing:
    """drawtext accepts an expression for fontsize and this build SEGFAULTS on
    it — rc=-11 with empty stderr, which reads as a rejected command rather
    than a crash. The punch is stepped constant sizes for that reason."""

    def _render(self, vf, tmp_path):
        src = tmp_path / "src.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", "testsrc=s=1080x1920:d=3:r=30", "-c:v", "libx264",
                        "-crf", "28", "-pix_fmt", "yuv420p", str(src)],
                       check=True, timeout=180)
        return subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf,
                               "-frames:v", "90", "-c:v", "libx264", "-crf", "28",
                               "-pix_fmt", "yuv420p", str(tmp_path / "out.mp4")],
                              capture_output=True, text=True, timeout=600)

    def test_hook_renders(self, tmp_path):
        vf = TL.hook_filters(TL.Hook("She married her assassin"), tmp_path / "t")
        r = self._render(vf, tmp_path)
        assert r.returncode == 0, f"rc={r.returncode} {r.stderr[-200:]}"

    def test_no_expression_is_used_for_fontsize(self):
        src = TL.hook_filters(TL.Hook("She married her assassin"), TL.Path("/tmp/_t"))
        assert "fontsize='" not in src, "a fontsize EXPRESSION segfaults this build"

    def test_slams_render(self, tmp_path):
        vf = TL.slam_filters([TL.Slam("9 OCTOBER 2026", 1.0)], tmp_path / "t")
        assert self._render(vf, tmp_path).returncode == 0


class TestSlams:
    def test_a_slam_outside_the_band_is_refused(self):
        with pytest.raises(ValueError):
            TL.Slam("9 OCTOBER 2026", 1.0, duration_s=2.5)
        with pytest.raises(ValueError):
            TL.Slam("9 OCTOBER 2026", 1.0, duration_s=0.2)

    def test_facts_land_on_downbeats(self):
        beats = [i * 0.4 for i in range(60)]
        slams = TL.facts_to_slams(
            {"premiere": "9 October 2026", "studio": "david production", "episodes": 24},
            beats, start_after_s=3.0)
        assert len(slams) == 3
        for s in slams:
            assert min(abs(s.at_s - b) for b in beats) < 1e-6

    def test_an_absent_fact_is_skipped_not_slammed_empty(self):
        beats = [i * 0.4 for i in range(60)]
        slams = TL.facts_to_slams({"premiere": "9 October 2026", "studio": "", "episodes": None},
                                  beats, start_after_s=3.0)
        assert [s.text for s in slams] == ["9 October 2026"]

    def test_the_hook_gate_catches_a_texty_frame(self):
        hook = TL.Hook("She married her assassin")
        assert TL.check_hook_gate(hook, frame_text_fraction=0.01) == []
        assert TL.check_hook_gate(hook, frame_text_fraction=0.14)
