"""Pins for ARCH #45 phase 2 — FrameCompositor sandbox routing.

The capability is opt-in: when ``sandbox_runner`` is None (today's
default for every niche except gaming), behavior must be byte-identical
to pre-phase-2. When ``sandbox_runner`` is set, every FFmpeg call routes
through ``sandbox_runner.run_ffmpeg_sync`` instead of executing locally
via ``run_ffmpeg``.

If a future PR breaks this invariant by accident, these tests should be
the first thing to fail — so the regression surfaces before reaching a
prod render run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from genlab_core.media.frame_compositor import ChannelBranding, FrameCompositor


@pytest.fixture
def fake_branding(tmp_path: Path) -> ChannelBranding:
    """Minimal ChannelBranding good enough for the tests below.

    Logo file is created on disk so the strict logo-exists invariant
    (R-26) doesn't refuse to render before we get to the FFmpeg call.
    """
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return ChannelBranding(
        niche_id="test",
        logo_path=str(logo),
        accent_color="#FF4500",
        channel_name="Test",
        handle="@test",
    )


class TestSandboxOptIn:
    def test_default_compositor_routes_through_run_ffmpeg(
        self,
        fake_branding: ChannelBranding,
        tmp_path: Path,
    ) -> None:
        """No sandbox_runner → run_ffmpeg gets the FFmpeg command.

        This is the no-change pin. Every niche other than gaming
        currently constructs FrameCompositor without sandbox; if this
        test fails the non-gaming render path silently changed.
        """
        comp = FrameCompositor(fake_branding)
        cmd = ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"]
        with patch("genlab_core.media.frame_compositor.run_ffmpeg") as mock_run:
            comp._run_ffmpeg(cmd, timeout=300, fallback_preset="fast", label="x")

        mock_run.assert_called_once()
        # The forwarded args carry the actual command + the timeout/preset
        call = mock_run.call_args
        assert call.args[0] == cmd
        assert call.kwargs["timeout"] == 300
        assert call.kwargs["fallback_preset"] == "fast"

    def test_sandbox_runner_routes_through_sandbox(
        self,
        fake_branding: ChannelBranding,
    ) -> None:
        """sandbox_runner set → FFmpeg goes through run_ffmpeg_sync."""
        mock_runner = MagicMock()
        mock_result = MagicMock()
        mock_result.check = MagicMock()
        mock_runner.run_ffmpeg_sync.return_value = mock_result

        comp = FrameCompositor(fake_branding, sandbox_runner=mock_runner)
        cmd = ["ffmpeg", "-y", "-i", "in.mp4", "out.mp4"]

        with patch("genlab_core.media.frame_compositor.run_ffmpeg") as mock_local_run:
            comp._run_ffmpeg(cmd, timeout=300, fallback_preset="fast", label="x")

        # Sandbox path: local run_ffmpeg must NOT be called
        mock_local_run.assert_not_called()
        # Sandbox runner gets the command + timeout
        mock_runner.run_ffmpeg_sync.assert_called_once_with(cmd, timeout=300)
        # check() raises on non-zero exit, matching the local contract
        mock_result.check.assert_called_once_with("x")

    def test_sandbox_runner_check_raises_to_caller(
        self,
        fake_branding: ChannelBranding,
    ) -> None:
        """When the sandbox runner reports failure, the exception
        bubbles up so existing try/except wrapping in compose() and
        overlay_branding() still catches it."""
        mock_runner = MagicMock()
        mock_result = MagicMock()
        mock_result.check.side_effect = RuntimeError("sandbox said no")
        mock_runner.run_ffmpeg_sync.return_value = mock_result

        comp = FrameCompositor(fake_branding, sandbox_runner=mock_runner)
        with pytest.raises(RuntimeError, match="sandbox said no"):
            comp._run_ffmpeg(["ffmpeg"], timeout=1, fallback_preset=None)


class TestBackwardCompatibility:
    """Existing callers must keep working without code changes."""

    def test_init_accepts_branding_only(self, fake_branding: ChannelBranding) -> None:
        """The old constructor signature (branding only) must still work
        — 4 of 5 niches don't pass sandbox_runner."""
        comp = FrameCompositor(fake_branding)
        assert comp.branding is fake_branding
        assert comp._sandbox_runner is None

    def test_from_visuals_yaml_accepts_path_only(
        self,
        tmp_path: Path,
    ) -> None:
        """from_visuals_yaml's old signature must still work."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"\x89PNG" + b"\x00" * 64)
        yaml_path = tmp_path / "visuals.yaml"
        yaml_path.write_text(
            "niche_id: test\n"
            f"logo_path: {logo}\n"
            "accent_color: '#FF0000'\n"
            "channel_name: Test\n"
            "channel_handle: '@test'\n"
        )
        comp = FrameCompositor.from_visuals_yaml(str(yaml_path))
        assert comp.branding.niche_id == "test"
        assert comp._sandbox_runner is None

    def test_from_visuals_yaml_propagates_sandbox_runner(
        self,
        tmp_path: Path,
    ) -> None:
        """from_visuals_yaml forwards sandbox_runner to __init__."""
        logo = tmp_path / "logo.png"
        logo.write_bytes(b"\x89PNG" + b"\x00" * 64)
        yaml_path = tmp_path / "visuals.yaml"
        yaml_path.write_text(
            "niche_id: test\n"
            f"logo_path: {logo}\n"
            "accent_color: '#FF0000'\n"
            "channel_name: Test\n"
            "channel_handle: '@test'\n"
        )
        runner = MagicMock()
        comp = FrameCompositor.from_visuals_yaml(str(yaml_path), sandbox_runner=runner)
        assert comp._sandbox_runner is runner
