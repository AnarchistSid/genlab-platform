"""Tests for ScoredDownloadGate — the 4-stage progressive download filter.

All subprocess.run calls are mocked; no real yt-dlp invocations.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from niches.gaming.tools.clip_sourcer import (
    ClipSourcerConfig,
    ScoredDownloadGate,
    StageScore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> ClipSourcerConfig:
    defaults = dict(
        min_duration_seconds=5,
        max_duration_seconds=60,
        banned_fragments=["reaction", "review", "explained", "ranking"],
        min_views=1000,
    )
    defaults.update(overrides)
    return ClipSourcerConfig(**defaults)


def _make_gate(**config_overrides) -> ScoredDownloadGate:
    return ScoredDownloadGate(_make_config(**config_overrides))


def _subprocess_ok(stdout: str) -> MagicMock:
    """Return a mock CompletedProcess with returncode=0."""
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = 0
    m.stdout = stdout
    m.stderr = ""
    return m


def _subprocess_fail() -> MagicMock:
    m = MagicMock(spec=subprocess.CompletedProcess)
    m.returncode = 1
    m.stdout = ""
    m.stderr = "error"
    return m


# ---------------------------------------------------------------------------
# StageScore dataclass
# ---------------------------------------------------------------------------


class TestStageScore:
    def test_stage_score_dataclass(self):
        s = StageScore(stage=1, score=0.5, passed=True, reason="ok", bytes_downloaded=42)
        assert s.stage == 1
        assert s.score == 0.5
        assert s.passed is True
        assert s.reason == "ok"
        assert s.bytes_downloaded == 42

    def test_stage_score_defaults(self):
        s = StageScore(stage=2, score=0.0, passed=False)
        assert s.reason == ""
        assert s.bytes_downloaded == 0


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_thresholds(self):
        assert ScoredDownloadGate.THRESHOLDS == (0.30, 0.50, 0.70, 0.0)

    def test_threshold_stage_count(self):
        assert len(ScoredDownloadGate.THRESHOLDS) == 4


# ---------------------------------------------------------------------------
# Stage 1 — Metadata
# ---------------------------------------------------------------------------


class TestStage1Metadata:
    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_metadata_rejects_short_duration(self, mock_run):
        mock_run.return_value = _subprocess_ok("Cool Game Trailer|||3.0|||50000|||2500")
        gate = _make_gate(min_duration_seconds=5, max_duration_seconds=60)

        result = gate.stage1_metadata("https://youtube.com/watch?v=abc")

        assert result.stage == 1
        assert result.passed is False
        assert "duration" in result.reason
        assert result.score == 0.0

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_metadata_rejects_long_duration(self, mock_run):
        mock_run.return_value = _subprocess_ok("Cool Game Trailer|||120.0|||50000|||2500")
        gate = _make_gate(min_duration_seconds=5, max_duration_seconds=60)

        result = gate.stage1_metadata("https://youtube.com/watch?v=abc")

        assert result.passed is False
        assert "duration" in result.reason

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_metadata_rejects_low_views(self, mock_run):
        mock_run.return_value = _subprocess_ok("Cool Game Trailer|||30.0|||500|||25")
        gate = _make_gate(min_views=1000)

        result = gate.stage1_metadata("https://youtube.com/watch?v=abc")

        assert result.stage == 1
        assert result.passed is False
        assert "500 views" in result.reason
        assert result.score == 0.1

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_metadata_rejects_banned_fragment(self, mock_run):
        mock_run.return_value = _subprocess_ok("Elden Ring REACTION|||30.0|||50000|||2500")
        gate = _make_gate()

        result = gate.stage1_metadata("https://youtube.com/watch?v=abc")

        assert result.passed is False
        assert "banned fragment" in result.reason

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_metadata_passes_good_video(self, mock_run):
        mock_run.return_value = _subprocess_ok(
            "Elden Ring Official Trailer|||45.0|||200000|||10000"
        )
        gate = _make_gate()

        result = gate.stage1_metadata("https://youtube.com/watch?v=abc")

        assert result.stage == 1
        assert result.passed is True
        assert result.score >= 0.30
        assert "views=200000" in result.reason

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_metadata_handles_na_values(self, mock_run):
        mock_run.return_value = _subprocess_ok("Some Clip|||NA|||NA|||NA")
        gate = _make_gate()

        result = gate.stage1_metadata("https://youtube.com/watch?v=abc")

        # NA values: duration=0 (skip filter), views=0 (skip low-view filter),
        # likes=0 => view_score=0.3, like_ratio=0.03 => score should pass 0.30
        assert result.stage == 1
        assert result.passed is True
        assert result.score >= 0.30

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_metadata_fetch_failed(self, mock_run):
        mock_run.return_value = _subprocess_fail()
        gate = _make_gate()

        result = gate.stage1_metadata("https://youtube.com/watch?v=abc")

        assert result.passed is False
        assert result.reason == "metadata fetch failed"

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_metadata_incomplete(self, mock_run):
        mock_run.return_value = _subprocess_ok("Title Only|||30")
        gate = _make_gate()

        result = gate.stage1_metadata("https://youtube.com/watch?v=abc")

        assert result.passed is False
        assert result.reason == "incomplete metadata"

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_stage1_correct_command(self, mock_run):
        mock_run.return_value = _subprocess_ok("Title|||30|||5000|||250")
        gate = _make_gate()
        gate.stage1_metadata("https://youtube.com/watch?v=test123")

        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "yt-dlp"
        assert "https://youtube.com/watch?v=test123" in cmd
        assert "--no-download" in cmd
        assert "%(title)s|||%(duration)s|||%(view_count)s|||%(like_count)s" in cmd


# ---------------------------------------------------------------------------
# run_gates — short-circuit behaviour
# ---------------------------------------------------------------------------


class TestRunGates:
    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_run_gates_short_circuits_on_stage1_failure(self, mock_run):
        # Stage 1 rejects (too short)
        mock_run.return_value = _subprocess_ok("Clip|||2.0|||50000|||2500")
        gate = _make_gate(min_duration_seconds=5, max_duration_seconds=60)

        passed, stages = gate.run_gates("https://youtube.com/watch?v=abc", Path("/tmp"))

        assert passed is False
        assert len(stages) == 1
        assert stages[0].stage == 1
        assert stages[0].passed is False
        # subprocess.run should only be called once (stage 1 metadata)
        assert mock_run.call_count == 1

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_run_gates_short_circuits_on_stage2_failure(self, mock_run):
        """Stage 1 passes, stage 2 fails (no thumbnail)."""

        def side_effect(cmd, **kwargs):
            if "--no-download" in cmd and "--print" in cmd:
                # Stage 1: metadata
                return _subprocess_ok("Good Trailer|||30.0|||50000|||2500")
            else:
                # Stage 2: thumbnail — fail (returncode ok but no jpg produced)
                return _subprocess_ok("")

        mock_run.side_effect = side_effect
        gate = _make_gate()

        passed, stages = gate.run_gates("https://youtube.com/watch?v=abc", Path("/tmp"))

        assert passed is False
        assert len(stages) == 2
        assert stages[0].passed is True
        assert stages[1].passed is False
        assert stages[1].stage == 2

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_run_gates_all_pass(self, mock_run):
        """All 3 stages pass."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            def side_effect(cmd, **kwargs):
                if "--no-download" in cmd and "--print" in cmd:
                    # Stage 1
                    return _subprocess_ok("Good Trailer|||30.0|||50000|||2500")
                elif "--write-thumbnail" in cmd:
                    # Stage 2: create a fake thumbnail in the temp dir used internally
                    # We need to find the -o path and create a jpg there
                    o_idx = cmd.index("-o")
                    template = cmd[o_idx + 1]
                    # template looks like /var/.../%(id)s.%(ext)s
                    parent = os.path.dirname(template)
                    thumb_path = os.path.join(parent, "test_id.jpg")
                    Path(thumb_path).write_bytes(b"\xff\xd8fake-jpg")
                    return _subprocess_ok("")
                elif "--download-sections" in cmd:
                    # Stage 3: create fake preview file + mock probe
                    preview_path = output_dir / "preview_clip.mp4"
                    preview_path.write_bytes(b"\x00" * 1024)
                    return _subprocess_ok("")
                return _subprocess_ok("")

            mock_run.side_effect = side_effect

            gate = _make_gate()
            with patch("niches.gaming.tools.clip_sourcer.probe_video") as mock_probe:
                mock_probe.return_value = {
                    "width": 1280,
                    "height": 720,
                    "duration": 15.0,
                    "fps": 30.0,
                    "aspect_ratio": "1280:720",
                }

                passed, stages = gate.run_gates("https://youtube.com/watch?v=abc", output_dir)

        assert passed is True
        assert len(stages) == 3
        assert all(s.passed for s in stages)

    @patch("niches.gaming.tools.clip_sourcer.subprocess.run")
    def test_run_gates_bytes_tracked(self, mock_run):
        """Total bytes_downloaded is accumulated across stages."""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)

            def side_effect(cmd, **kwargs):
                if "--no-download" in cmd and "--print" in cmd:
                    return _subprocess_ok("Good Trailer|||30.0|||50000|||2500")
                elif "--write-thumbnail" in cmd:
                    o_idx = cmd.index("-o")
                    template = cmd[o_idx + 1]
                    parent = os.path.dirname(template)
                    thumb_path = os.path.join(parent, "test_id.jpg")
                    Path(thumb_path).write_bytes(b"\xff\xd8" + b"x" * 50_000)
                    return _subprocess_ok("")
                elif "--download-sections" in cmd:
                    preview_path = output_dir / "preview_clip.mp4"
                    preview_path.write_bytes(b"\x00" * 10_000)
                    return _subprocess_ok("")
                return _subprocess_ok("")

            mock_run.side_effect = side_effect

            gate = _make_gate()
            with patch("niches.gaming.tools.clip_sourcer.probe_video") as mock_probe:
                mock_probe.return_value = {
                    "width": 1280,
                    "height": 720,
                    "duration": 15.0,
                    "fps": 30.0,
                    "aspect_ratio": "1280:720",
                }
                passed, stages = gate.run_gates("https://youtube.com/watch?v=abc", output_dir)

        assert passed is True
        total = sum(s.bytes_downloaded for s in stages)
        # Stage 1 = 0 bytes, stage 2 = ~50KB, stage 3 = ~10KB
        assert total > 0


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_min_views_default(self):
        config = ClipSourcerConfig()
        assert config.min_views == 1000

    def test_min_views_override(self):
        config = ClipSourcerConfig(min_views=5000)
        assert config.min_views == 5000

    def test_gate_uses_config_min_views(self):
        config = ClipSourcerConfig(min_views=2000)
        gate = ScoredDownloadGate(config)
        assert gate._min_views == 2000
