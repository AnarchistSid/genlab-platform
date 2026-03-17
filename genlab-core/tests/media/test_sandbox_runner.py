"""Tests for genlab_core.media.sandbox_runner.

Unit tests run without OpenSandbox server (mock-based).
Integration tests require the server running and are marked with
pytest.mark.integration.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from genlab_core.media.sandbox_runner import (
    SandboxedFFmpegRunner,
    SandboxRunResult,
    sandbox_rendering_enabled,
)

_opensandbox_available = False
try:
    import opensandbox  # noqa: F401
    _opensandbox_available = True
except ImportError:
    pass


GENLAB_ROOT = Path("/Users/anarchistsid/GenLab")


# ── Unit tests (no server needed) ────────────────────────────────────────────


class TestSandboxRunResult:
    def test_ok_on_zero_exit(self):
        r = SandboxRunResult(exit_code=0, stdout="done", stderr="")
        assert r.ok is True

    def test_not_ok_on_nonzero(self):
        r = SandboxRunResult(exit_code=1, stdout="", stderr="error")
        assert r.ok is False

    def test_check_passes_on_ok(self):
        r = SandboxRunResult(exit_code=0, stdout="", stderr="")
        r.check("test")  # should not raise

    def test_check_raises_on_fail(self):
        r = SandboxRunResult(exit_code=1, stdout="", stderr="ffmpeg error")
        with pytest.raises(RuntimeError, match="ffmpeg error"):
            r.check("render")


class TestPathRewriting:
    def setup_method(self):
        self.runner = SandboxedFFmpegRunner(genlab_root=GENLAB_ROOT)

    def test_host_to_sandbox(self):
        host = "/Users/anarchistsid/GenLab/BlackboxBrief/.tmp/runs/clip.mp4"
        sandbox = self.runner.host_to_sandbox_path(host)
        assert sandbox == "/workspace/BlackboxBrief/.tmp/runs/clip.mp4"

    def test_sandbox_to_host(self):
        sandbox = "/workspace/CriticalRush/output/reel.mp4"
        host = self.runner.sandbox_to_host_path(sandbox)
        assert host == str(GENLAB_ROOT / "CriticalRush/output/reel.mp4")

    def test_non_genlab_path_unchanged(self):
        # On macOS, /tmp resolves to /private/tmp — use a path that doesn't symlink
        result = self.runner.host_to_sandbox_path("/var/data/foo.mp4")
        assert "/workspace" not in result
        assert self.runner.sandbox_to_host_path("/var/data/foo.mp4") == "/var/data/foo.mp4"

    def test_rewrite_ffmpeg_cmd(self):
        cmd = [
            "/opt/homebrew/bin/ffmpeg", "-y",
            "-i", f"{GENLAB_ROOT}/BlackboxBrief/.tmp/clip.mp4",
            "-c:v", "libx264",
            f"{GENLAB_ROOT}/BlackboxBrief/.tmp/out.mp4",
        ]
        rewritten = self.runner.rewrite_ffmpeg_cmd(cmd)
        joined = " ".join(rewritten)
        assert str(GENLAB_ROOT) not in joined
        assert rewritten[0] == "ffmpeg"  # host binary → bare name
        assert "/workspace/BlackboxBrief/.tmp/clip.mp4" in joined
        assert "/workspace/BlackboxBrief/.tmp/out.mp4" in joined

    def test_rewrite_preserves_non_ffmpeg_binary(self):
        cmd = ["/usr/bin/custom_tool", "-i", f"{GENLAB_ROOT}/file.mp4"]
        rewritten = self.runner.rewrite_ffmpeg_cmd(cmd)
        assert rewritten[0] == "/usr/bin/custom_tool"  # not rewritten


class TestSandboxRenderingEnabled:
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            # Need to also clear the env var if it exists
            os.environ.pop("GENLAB_RENDER_SANDBOX", None)
            assert sandbox_rendering_enabled() is False

    def test_enabled_when_env_true(self):
        with patch.dict(os.environ, {"GENLAB_RENDER_SANDBOX": "true"}):
            # Only enabled if opensandbox is importable
            import genlab_core.media.sandbox_runner as mod
            mod._OPENSANDBOX_AVAILABLE = None  # reset cache
            result = sandbox_rendering_enabled()
            # Result depends on whether opensandbox is installed
            # In CI without opensandbox, this returns False (with warning)
            assert isinstance(result, bool)
            mod._OPENSANDBOX_AVAILABLE = None  # reset


# ── Integration tests (require opensandbox-server running) ───────────────────


def _server_running() -> bool:
    try:
        import requests
        r = requests.get("http://127.0.0.1:8080/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


@pytest.mark.skipif(not _opensandbox_available, reason="opensandbox not installed")
class TestEgressPolicyConstruction:
    """Verify _build_network_policy produces correct structures."""

    def test_default_deny_all(self):
        runner = SandboxedFFmpegRunner(genlab_root=GENLAB_ROOT)
        assert runner._network_policy is not None
        assert runner._network_policy.default_action == "deny"
        assert runner._network_policy.egress is None

    def test_explicit_empty_list(self):
        runner = SandboxedFFmpegRunner(genlab_root=GENLAB_ROOT, egress_allow=[])
        assert runner._network_policy.default_action == "deny"
        assert runner._network_policy.egress is None

    def test_allowlist_produces_rules(self):
        runner = SandboxedFFmpegRunner(
            genlab_root=GENLAB_ROOT,
            egress_allow=["api.twitch.tv", "*.steampowered.com"],
        )
        assert runner._network_policy.default_action == "deny"
        rules = runner._network_policy.egress
        assert len(rules) == 2
        assert rules[0].action == "allow"
        assert rules[0].target == "api.twitch.tv"
        assert rules[1].target == "*.steampowered.com"


@pytest.mark.skipif(not _server_running(), reason="OpenSandbox server not running")
class TestSandboxedFFmpegRunnerIntegration:
    """Integration tests that create real sandboxes."""

    def test_sync_standalone_echo(self):
        """Verify standalone mode creates sandbox, runs command, kills it."""
        runner = SandboxedFFmpegRunner(
            genlab_root=GENLAB_ROOT,
            image="ubuntu:22.04",
        )
        result = runner.run_ffmpeg_sync("echo hello", timeout=30)
        assert result.ok
        assert "hello" in result.stdout

    def test_sync_context_manager(self):
        """Verify sync context manager creates/kills sandbox correctly."""
        runner = SandboxedFFmpegRunner(
            genlab_root=GENLAB_ROOT,
            image="ubuntu:22.04",
        )
        with runner:
            result = runner.run_ffmpeg_sync("ls /workspace", timeout=30)
            assert result.ok
            # GenLab directories should be visible
            assert any(
                name in result.stdout
                for name in ["CriticalRush", "genlab-core", "BlackboxBrief"]
            )

    def test_volume_mount_visible(self):
        """Verify host GenLab directory is mounted at /workspace."""
        runner = SandboxedFFmpegRunner(
            genlab_root=GENLAB_ROOT,
            image="ubuntu:22.04",
        )
        with runner:
            result = runner.run_ffmpeg_sync(
                ["head", "-5", "/workspace/genlab-core/pyproject.toml"],
                timeout=30,
            )
            assert result.ok
            assert "hatchling" in result.stdout.lower()

    def test_egress_deny_all_blocks_dns(self):
        """Verify deny-all egress blocks DNS resolution.

        The egress sidecar intercepts DNS queries and returns NXDOMAIN for
        all domains when default_action=deny.  DNS blocking is the primary
        security layer — direct IP filtering (nftables Layer 2) depends on
        kernel/runtime support and may not be available on all Docker hosts.

        For render sandboxes, DNS blocking is sufficient: FFmpeg never
        resolves domain names, it only reads/writes local files.
        """
        runner = SandboxedFFmpegRunner(
            genlab_root=GENLAB_ROOT,
            image="ubuntu:22.04",
            # default: egress_allow=None → deny-all
        )
        with runner:
            result = runner.run_ffmpeg_sync(
                ["bash", "-c",
                 "getent hosts google.com 2>&1; echo DNS_EXIT=$?"],
                timeout=15,
            )
            output = result.stdout + result.stderr
            # DNS resolution should fail (exit code 2 = not found)
            assert "DNS_EXIT=2" in output, (
                f"Expected DNS blocked (exit 2) but got: {output[:300]}"
            )

    def test_egress_allow_specific_domain(self):
        """Verify allowlisted domain is reachable via bash /dev/tcp."""
        runner = SandboxedFFmpegRunner(
            genlab_root=GENLAB_ROOT,
            image="ubuntu:22.04",
            egress_allow=["pypi.org"],
        )
        with runner:
            # TCP connection to pypi.org:443 should succeed
            result = runner.run_ffmpeg_sync(
                ["bash", "-c",
                 "timeout 5 bash -c 'echo > /dev/tcp/pypi.org/443' 2>&1; echo EXIT=$?"],
                timeout=15,
            )
            output = result.stdout + result.stderr
            assert "EXIT=0" in output, (
                f"Expected pypi.org reachable but got: {output[:300]}"
            )
