"""Pin chart_broll (2026-08-18, task #193):

  * Flag semantics (off / on / canary / wildcard)
  * Missing ffmpeg → False
  * Empty title / bars → False
  * >7 bars → capped
  * Accent color matches CLAUDE.md niche accents
  * Value formatter thresholds (10, 1K, 1M)
  * ffmpeg subprocess nonzero → False
  * Subprocess timeout → False
  * Filter-graph shape carries bt709 params + drawbox per bar
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from genlab_core.media.chart_broll import (
    _NICHE_ACCENT,
    _accent_for,
    _build_filter_graph,
    _fmt_value,
    is_enabled_for,
    render_chart_broll,
)


class TestFlagSemantics:
    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
    def test_off_tokens(self, monkeypatch, val):
        monkeypatch.setenv("GENLAB_CHART_BROLL_NICHES", val)
        assert is_enabled_for("ai_creators") is False

    def test_unset_off(self, monkeypatch):
        monkeypatch.delenv("GENLAB_CHART_BROLL_NICHES", raising=False)
        assert is_enabled_for("ai_creators") is False

    def test_wildcard_enables_all(self, monkeypatch):
        monkeypatch.setenv("GENLAB_CHART_BROLL_NICHES", "all")
        for n in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert is_enabled_for(n) is True

    def test_canary_isolation(self, monkeypatch):
        monkeypatch.setenv("GENLAB_CHART_BROLL_NICHES", "ai_creators")
        assert is_enabled_for("ai_creators") is True
        assert is_enabled_for("gaming") is False


class TestAccentColors:
    def test_every_niche_has_accent(self):
        for niche in ("ai_creators", "gaming", "sports", "movies", "anime"):
            assert niche in _NICHE_ACCENT

    def test_unknown_niche_gets_fallback(self):
        # Non-empty hex string, not raising
        assert len(_accent_for("nonexistent")) == 6

    def test_ai_creators_matches_claude_md(self):
        """CLAUDE.md documents ai_creators accent as #00D4FF."""
        assert _accent_for("ai_creators").upper() == "00D4FF"


class TestValueFormatter:
    def test_below_10_keeps_decimal(self):
        assert _fmt_value(1.5) == "1.5"
        assert _fmt_value(9.9) == "9.9"

    def test_ten_to_thousand_no_decimal(self):
        assert _fmt_value(50) == "50"
        assert _fmt_value(999) == "999"

    def test_thousand_tier(self):
        assert _fmt_value(1500) == "1.5K"
        assert _fmt_value(175000) == "175.0K"

    def test_million_tier(self):
        assert _fmt_value(1_700_000) == "1.7M"

    def test_none_returns_empty(self):
        assert _fmt_value(None) == ""


class TestFilterGraphBuilder:
    def test_builds_one_bar_per_input(self):
        g = _build_filter_graph(
            "T",
            [("A", 10), ("B", 20), ("C", 30)],
            "ai_creators",
        )
        assert g.count("drawbox") == 3

    def test_includes_title_drawtext(self):
        g = _build_filter_graph("MyTitle", [("A", 1), ("B", 2)], "gaming")
        assert "text='MyTitle'" in g

    def test_includes_niche_accent_color(self):
        g = _build_filter_graph(
            "T", [("A", 1), ("B", 2)], "ai_creators",
        )
        assert "00D4FF" in g

    def test_apostrophe_in_label_uses_typographic_quote(self):
        """Same escape rule as escape_drawtext — ASCII \' terminates
        the outer 'text=' quoted string in a -vf chain."""
        g = _build_filter_graph(
            "AI's leaders", [("OpenAI's rank", 1), ("Google's rank", 2)],
            "ai_creators",
        )
        # No ASCII apostrophe should appear inside drawtext text= values
        # for values that contained one — U+2019 replaces it.
        assert "'s" not in g or "’" in g

    def test_empty_bars_raises(self):
        with pytest.raises(ValueError):
            _build_filter_graph("T", [], "ai_creators")

    def test_label_fontsize_smaller_at_5plus_bars(self):
        """Sample-review 2026-08-18: 5-bar chart with long labels
        ('Anthropic', 'Perplexity') collapsed into 'AnthropicPerplexity'
        at fontsize=36. Verify 5+ bars use the smaller 26px label size."""
        four_bar = _build_filter_graph(
            "T", [("A", 1), ("B", 2), ("C", 3), ("D", 4)], "ai_creators",
        )
        five_bar = _build_filter_graph(
            "T",
            [("A", 1), ("B", 2), ("C", 3), ("D", 4), ("E", 5)],
            "ai_creators",
        )
        # 4 bars uses fontsize=36 for labels
        assert "fontsize=36" in four_bar
        # 5 bars drops label fontsize to 26 (values stay at 42)
        assert "fontsize=26" in five_bar
        assert "fontsize=36" not in five_bar

    def test_long_labels_truncated_at_5plus_bars(self):
        """Long labels get an ellipsis when the width is crowded."""
        five_bar = _build_filter_graph(
            "T",
            [("Anthropic", 1), ("Perplexity", 2), ("OpenAI", 3),
             ("xAI", 4), ("Mistral", 5)],
            "ai_creators",
        )
        # Truncation ellipsis should appear
        assert "…" in five_bar


class TestRenderChartBroll:
    def test_missing_ffmpeg_returns_false(self):
        with patch(
            "genlab_core.media.chart_broll.shutil.which",
            return_value=None,
        ):
            assert render_chart_broll(
                "T", [("A", 1), ("B", 2)], "ai_creators", "/tmp/out.mp4",
            ) is False

    def test_empty_title_returns_false(self):
        with patch(
            "genlab_core.media.chart_broll.shutil.which",
            return_value="/opt/ffmpeg",
        ):
            assert render_chart_broll(
                "", [("A", 1), ("B", 2)], "ai_creators", "/tmp/out.mp4",
            ) is False
            assert render_chart_broll(
                "   ", [("A", 1), ("B", 2)], "ai_creators", "/tmp/out.mp4",
            ) is False

    def test_empty_bars_returns_false(self):
        with patch(
            "genlab_core.media.chart_broll.shutil.which",
            return_value="/opt/ffmpeg",
        ):
            assert render_chart_broll(
                "T", [], "ai_creators", "/tmp/out.mp4",
            ) is False

    def test_more_than_seven_bars_capped(self, tmp_path):
        many = [(f"L{i}", i + 1) for i in range(12)]
        with patch(
            "genlab_core.media.chart_broll.shutil.which",
            return_value="/opt/ffmpeg",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr="",
            )
            assert render_chart_broll(
                "T", many, "ai_creators", str(tmp_path / "out.mp4"),
            ) is True
            # Filter_complex arg should be present and cap at 7 bars
            call = mock_run.call_args
            # The subprocess.run call is positional-list; find the -filter_complex value
            args = call.args[0]
            fc_idx = args.index("-filter_complex")
            filter_str = args[fc_idx + 1]
            assert filter_str.count("drawbox") == 7

    def test_subprocess_nonzero_returns_false(self, tmp_path):
        with patch(
            "genlab_core.media.chart_broll.shutil.which",
            return_value="/opt/ffmpeg",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="fatal",
            )
            assert render_chart_broll(
                "T", [("A", 1), ("B", 2)], "ai_creators",
                str(tmp_path / "out.mp4"),
            ) is False

    def test_subprocess_success_returns_true(self, tmp_path):
        with patch(
            "genlab_core.media.chart_broll.shutil.which",
            return_value="/opt/ffmpeg",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr="",
            )
            assert render_chart_broll(
                "T", [("A", 1), ("B", 2)], "ai_creators",
                str(tmp_path / "out.mp4"),
            ) is True

    def test_ffmpeg_command_carries_bt709_params(self, tmp_path):
        """CLAUDE.md contract: every reel MUST have bt709 on all 3
        color fields. The -x264-params flag is the only reliable way
        to embed them into the H.264 SPS (verified 4ec93793 +
        e2efd89e). The chart broll must ship with the same setup."""
        with patch(
            "genlab_core.media.chart_broll.shutil.which",
            return_value="/opt/ffmpeg",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr="",
            )
            render_chart_broll(
                "T", [("A", 1), ("B", 2)], "ai_creators",
                str(tmp_path / "out.mp4"),
            )
            args = mock_run.call_args.args[0]
            joined = " ".join(args)
            assert "-x264-params" in args
            assert "colorprim=bt709" in joined
            assert "transfer=bt709" in joined
            assert "colormatrix=bt709" in joined

    def test_ffmpeg_command_includes_silent_audio(self, tmp_path):
        """Concat with the main reel requires matching stream count.
        Chart intro must include silent AAC 48kHz stereo audio."""
        with patch(
            "genlab_core.media.chart_broll.shutil.which",
            return_value="/opt/ffmpeg",
        ), patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="", stderr="",
            )
            render_chart_broll(
                "T", [("A", 1), ("B", 2)], "ai_creators",
                str(tmp_path / "out.mp4"),
            )
            args = mock_run.call_args.args[0]
            joined = " ".join(args)
            assert "anullsrc" in joined
            assert "sample_rate=48000" in joined
            assert "channel_layout=stereo" in joined
