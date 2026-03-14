"""Sandboxed FFmpeg execution via OpenSandbox.

Provides a SandboxedFFmpegRunner that runs FFmpeg commands inside an
OpenSandbox container with volume-mounted GenLab directories.  This gives
TTL-scoped isolation (no leftover temp files on host) and enforces resource
limits on CPU-heavy render stages.

Egress policies (Phase 2):
    Render sandboxes enforce deny-all network egress by default — FFmpeg
    only needs local file I/O via volume mounts, never the network.
    Custom policies can be passed via ``egress_allow`` for stages that
    need specific domain access (e.g. fetch or publish stages in future).

Usage:
    from genlab_core.media.sandbox_runner import SandboxedFFmpegRunner

    runner = SandboxedFFmpegRunner(genlab_root=Path("/Users/me/GenLab"))
    async with runner:
        result = await runner.run_ffmpeg(
            "ffmpeg -y -i /workspace/clips/clip.mp4 -c:v libx264 /workspace/output/reel.mp4"
        )

Configuration (env vars):
    GENLAB_RENDER_SANDBOX   — "true" to enable sandbox rendering (default: "false")
    OPENSANDBOX_SERVER      — server URL (default: "http://127.0.0.1:8080")
    OPENSANDBOX_RENDER_IMAGE — Docker image with FFmpeg (default: "jrottenberg/ffmpeg:7-ubuntu")
    OPENSANDBOX_RENDER_TTL  — sandbox TTL in seconds (default: "600")
"""

from __future__ import annotations

import logging
import os
import shlex
from datetime import timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy imports — opensandbox is optional
_OPENSANDBOX_AVAILABLE: bool | None = None


def _check_opensandbox() -> bool:
    global _OPENSANDBOX_AVAILABLE
    if _OPENSANDBOX_AVAILABLE is None:
        try:
            import opensandbox  # noqa: F401

            _OPENSANDBOX_AVAILABLE = True
        except ImportError:
            _OPENSANDBOX_AVAILABLE = False
    return _OPENSANDBOX_AVAILABLE


def sandbox_rendering_enabled() -> bool:
    """Check if sandbox rendering is enabled and available."""
    enabled = os.getenv("GENLAB_RENDER_SANDBOX", "false").lower() == "true"
    if enabled and not _check_opensandbox():
        logger.warning(
            "GENLAB_RENDER_SANDBOX=true but opensandbox not installed. "
            "Falling back to local rendering."
        )
        return False
    return enabled


def _build_network_policy(egress_allow: list[str] | None):
    """Build an OpenSandbox NetworkPolicy from a domain allowlist.

    Returns None if opensandbox is unavailable (caller should skip policy).
    An empty or None ``egress_allow`` results in deny-all (no outbound network).
    """
    if not _check_opensandbox():
        return None
    from opensandbox.models import NetworkPolicy, NetworkRule

    rules = [NetworkRule(action="allow", target=d) for d in (egress_allow or [])]
    return NetworkPolicy(defaultAction="deny", egress=rules or None)


def _execution_to_result(execution) -> "SandboxRunResult":
    """Convert an OpenSandbox Execution to SandboxRunResult.

    The Execution model doesn't expose exit_code directly — we infer it
    from the error field (None = success, non-None = failure).
    """
    stdout = "".join(l.text for l in execution.logs.stdout)
    stderr = "".join(l.text for l in execution.logs.stderr)
    # Execution.error is None on success, an ExecutionError on failure
    exit_code = 0 if execution.error is None else 1
    return SandboxRunResult(exit_code=exit_code, stdout=stdout, stderr=stderr)


class SandboxedFFmpegRunner:
    """Run FFmpeg commands inside an OpenSandbox container.

    Mounts the GenLab root into the sandbox at /workspace so all file paths
    can be rewritten from host paths to sandbox paths by simple prefix
    replacement.

    The sandbox is created on __aenter__ and killed on __aexit__, giving
    TTL-scoped cleanup.
    """

    def __init__(
        self,
        genlab_root: Path,
        *,
        server_url: str | None = None,
        image: str | None = None,
        ttl_seconds: int | None = None,
        egress_allow: list[str] | None = None,
    ) -> None:
        self._genlab_root = genlab_root.resolve()
        self._server_url = server_url or os.getenv(
            "OPENSANDBOX_SERVER", "http://127.0.0.1:8080"
        )
        self._image = image or os.getenv(
            "OPENSANDBOX_RENDER_IMAGE", "jrottenberg/ffmpeg:7-ubuntu"
        )
        self._ttl = ttl_seconds or int(
            os.getenv("OPENSANDBOX_RENDER_TTL", "600")
        )
        self._egress_allow = egress_allow  # None = deny-all (default for render)
        self._network_policy = _build_network_policy(egress_allow)
        self._sandbox = None
        self._sandbox_sync = None

    @property
    def workspace_mount(self) -> str:
        """Container-side mount point for GenLab root."""
        return "/workspace"

    def host_to_sandbox_path(self, host_path: str | Path) -> str:
        """Convert a host filesystem path to the sandbox mount path."""
        host_str = str(Path(host_path).resolve())
        genlab_str = str(self._genlab_root)
        if host_str.startswith(genlab_str):
            relative = host_str[len(genlab_str):]
            return f"{self.workspace_mount}{relative}"
        return host_str

    def sandbox_to_host_path(self, sandbox_path: str) -> str:
        """Convert a sandbox mount path back to the host path."""
        if sandbox_path.startswith(self.workspace_mount):
            relative = sandbox_path[len(self.workspace_mount):]
            return str(self._genlab_root / relative.lstrip("/"))
        return sandbox_path

    def rewrite_ffmpeg_cmd(self, cmd: list[str]) -> list[str]:
        """Rewrite file paths in an FFmpeg command from host to sandbox paths.

        Handles two rewrites:
        1. GenLab root paths → /workspace mount paths
        2. Host-local ffmpeg/ffprobe binary → bare name (on PATH in container)
        """
        genlab_str = str(self._genlab_root)
        rewritten = []
        for i, arg in enumerate(cmd):
            # Replace host binary path with bare name (ffmpeg/ffprobe on PATH)
            if i == 0:
                basename = Path(arg).name
                if basename in ("ffmpeg", "ffprobe"):
                    rewritten.append(basename)
                    continue
            rewritten.append(arg.replace(genlab_str, self.workspace_mount))
        return rewritten

    # ── Sync API (used by video_compositor.py + ffmpeg_utils.py) ──────

    def run_ffmpeg_sync(
        self,
        cmd: str | list[str],
        *,
        timeout: int = 300,
    ) -> "SandboxRunResult":
        """Run an FFmpeg command synchronously inside the sandbox.

        If the sandbox isn't started yet, creates one for this single command
        (standalone mode).
        """
        if not _check_opensandbox():
            raise RuntimeError("opensandbox package not installed")

        from opensandbox import SandboxSync
        from opensandbox.config import ConnectionConfigSync
        from opensandbox.models import Volume, Host

        cmd_list = cmd if isinstance(cmd, list) else shlex.split(cmd)
        cmd_list = self.rewrite_ffmpeg_cmd(cmd_list)
        cmd_str = shlex.join(cmd_list)

        if self._sandbox_sync:
            from opensandbox.models.execd import RunCommandOpts

            opts = RunCommandOpts(timeout=timedelta(seconds=timeout))
            execution = self._sandbox_sync.commands.run(cmd_str, opts=opts)
            return _execution_to_result(execution)

        # Standalone mode — create sandbox for this command only
        volume = Volume(
            name="genlab",
            host=Host(path=str(self._genlab_root)),
            mount_path=self.workspace_mount,
            read_only=False,
        )
        create_kwargs: dict = dict(
            image=self._image,
            timeout=timedelta(seconds=self._ttl),
            metadata={"purpose": "ffmpeg-render", "genlab": "true"},
            volumes=[volume],
            connection_config=ConnectionConfigSync(domain=self._server_url),
        )
        if self._network_policy is not None:
            create_kwargs["network_policy"] = self._network_policy
        sbx = SandboxSync.create(**create_kwargs)
        try:
            from opensandbox.models.execd import RunCommandOpts

            opts = RunCommandOpts(timeout=timedelta(seconds=timeout))
            execution = sbx.commands.run(cmd_str, opts=opts)
            return _execution_to_result(execution)
        finally:
            sbx.kill()

    # ── Async context manager API ─────────────────────────────────────

    async def __aenter__(self) -> "SandboxedFFmpegRunner":
        if not _check_opensandbox():
            raise RuntimeError("opensandbox package not installed")

        from opensandbox import Sandbox
        from opensandbox.config import ConnectionConfig
        from opensandbox.models import Volume, Host

        volume = Volume(
            name="genlab",
            host=Host(path=str(self._genlab_root)),
            mount_path=self.workspace_mount,
            read_only=False,
        )
        logger.info(
            "[SANDBOX] Creating render sandbox: image=%s ttl=%ds mount=%s→%s",
            self._image,
            self._ttl,
            self._genlab_root,
            self.workspace_mount,
        )
        create_kwargs: dict = dict(
            image=self._image,
            timeout=timedelta(seconds=self._ttl),
            metadata={"purpose": "ffmpeg-render", "genlab": "true"},
            volumes=[volume],
            connection_config=ConnectionConfig(domain=self._server_url),
        )
        if self._network_policy is not None:
            create_kwargs["network_policy"] = self._network_policy
        self._sandbox = await Sandbox.create(**create_kwargs)
        logger.info("[SANDBOX] Render sandbox ready: %s", self._sandbox.sandbox_id)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._sandbox:
            logger.info("[SANDBOX] Killing render sandbox: %s", self._sandbox.sandbox_id)
            await self._sandbox.kill()
            self._sandbox = None

    async def run_ffmpeg(
        self,
        cmd: str | list[str],
        *,
        timeout: int = 300,
    ) -> "SandboxRunResult":
        """Run an FFmpeg command asynchronously inside the sandbox."""
        if not self._sandbox:
            raise RuntimeError("Sandbox not started. Use `async with` or call __aenter__.")

        cmd_list = cmd if isinstance(cmd, list) else shlex.split(cmd)
        cmd_list = self.rewrite_ffmpeg_cmd(cmd_list)
        cmd_str = shlex.join(cmd_list)

        from opensandbox.models.execd import RunCommandOpts

        logger.debug("[SANDBOX] Running: %s", cmd_str[:200])
        opts = RunCommandOpts(timeout=timedelta(seconds=timeout))
        execution = await self._sandbox.commands.run(cmd_str, opts=opts)

        result = _execution_to_result(execution)
        if not result.ok:
            logger.error(
                "[SANDBOX] FFmpeg failed (exit %d): %s",
                result.exit_code,
                result.stderr[-500:],
            )
        return result

    # ── Sync context manager API ──────────────────────────────────────

    def __enter__(self) -> "SandboxedFFmpegRunner":
        if not _check_opensandbox():
            raise RuntimeError("opensandbox package not installed")

        from opensandbox import SandboxSync
        from opensandbox.config import ConnectionConfigSync
        from opensandbox.models import Volume, Host

        volume = Volume(
            name="genlab",
            host=Host(path=str(self._genlab_root)),
            mount_path=self.workspace_mount,
            read_only=False,
        )
        logger.info(
            "[SANDBOX] Creating render sandbox (sync): image=%s ttl=%ds",
            self._image,
            self._ttl,
        )
        create_kwargs: dict = dict(
            image=self._image,
            timeout=timedelta(seconds=self._ttl),
            metadata={"purpose": "ffmpeg-render", "genlab": "true"},
            volumes=[volume],
            connection_config=ConnectionConfigSync(domain=self._server_url),
        )
        if self._network_policy is not None:
            create_kwargs["network_policy"] = self._network_policy
        self._sandbox_sync = SandboxSync.create(**create_kwargs)
        logger.info("[SANDBOX] Render sandbox ready (sync)")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._sandbox_sync:
            logger.info("[SANDBOX] Killing render sandbox (sync)")
            self._sandbox_sync.kill()
            self._sandbox_sync = None


class SandboxRunResult:
    """Result of running a command inside the sandbox."""

    __slots__ = ("exit_code", "stdout", "stderr")

    def __init__(self, exit_code: int, stdout: str, stderr: str) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def check(self, label: str = "ffmpeg") -> None:
        """Raise RuntimeError if the command failed."""
        if not self.ok:
            raise RuntimeError(
                f"Sandbox command failed [{label}] (exit {self.exit_code}):\n"
                + self.stderr[-2000:]
            )
