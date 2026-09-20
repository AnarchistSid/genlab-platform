"""Queue transport for matte jobs: local filesystem, or a remote one over SSH.

The pipeline writes jobs on prod; the worker runs where there is a GPU. Rather
than teach the worker two code paths, both sides speak to a transport, and only
the transport knows whether the queue is a directory or an ssh target.

Local is not just a test double -- it is the correct transport when the worker
runs on the same host, which is what a GPU box would mean.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

SUBDIRS = ("queued", "running", "done", "failed")


class Transport(Protocol):
    def list_queued(self) -> list[str]: ...
    def claim(self, name: str) -> dict | None: ...
    def finish(self, job_id: str, payload: dict, *, ok: bool) -> None: ...
    def heartbeat(self) -> None: ...
    def fetch(self, remote: str, local: Path) -> bool: ...
    def push(self, local: Path, remote: str) -> bool: ...


class LocalTransport:
    """The queue is a directory on this machine."""

    def __init__(self, root: Path):
        self.root = Path(root)
        for d in SUBDIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def list_queued(self) -> list[str]:
        return sorted(p.name for p in (self.root / "queued").glob("*.json"))

    def claim(self, name: str) -> dict | None:
        """Claim by atomic rename. Two workers racing: one rename wins, the
        other gets FileNotFoundError and moves on -- no lock needed."""
        src = self.root / "queued" / name
        dst = self.root / "running" / name
        try:
            src.rename(dst)
        except OSError:
            return None
        try:
            return json.loads(dst.read_text())
        except (OSError, ValueError) as exc:
            logger.warning("[matte-queue] %s claimed but unreadable: %s", name, exc)
            return None

    def finish(self, job_id: str, payload: dict, *, ok: bool) -> None:
        out = self.root / ("done" if ok else "failed") / f"{job_id}.json"
        tmp = out.with_name(f".{out.name}.tmp")
        tmp.write_text(json.dumps(payload))
        tmp.rename(out)  # the waiter must never read a partial
        running = self.root / "running" / f"{job_id}.json"
        if running.exists():
            running.unlink()

    def heartbeat(self) -> None:
        (self.root / "worker.heartbeat").write_text(str(int(__import__("time").time())))

    def fetch(self, remote: str, local: Path) -> bool:
        return Path(remote).exists()

    def push(self, local: Path, remote: str) -> bool:
        return True


class SSHTransport:
    """The queue lives on another host; reach it with ssh and scp.

    Chosen over a broker because prod's Redis is bound to 127.0.0.1 -- using it
    would mean opening a port or holding a tunnel. This needs neither, and the
    SSH trust already exists for deploys.
    """

    def __init__(self, host: str, root: str, *, ssh_opts: tuple[str, ...] = ()):
        self.host, self.root = host, root.rstrip("/")
        self.ssh = ("ssh", *ssh_opts, host)

    def _run(self, cmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run([*self.ssh, cmd], capture_output=True, text=True, timeout=timeout)

    def list_queued(self) -> list[str]:
        r = self._run(f"ls -1 {shlex.quote(self.root)}/queued/*.json 2>/dev/null || true")
        return sorted(Path(x).name for x in r.stdout.split() if x.endswith(".json"))

    def claim(self, name: str) -> dict | None:
        q = f"{self.root}/queued/{name}"
        run = f"{self.root}/running/{name}"
        r = self._run(
            f"mv {shlex.quote(q)} {shlex.quote(run)} 2>/dev/null && cat {shlex.quote(run)}"
        )
        if r.returncode != 0 or not r.stdout.strip():
            return None
        try:
            return json.loads(r.stdout)
        except ValueError:
            return None

    def finish(self, job_id: str, payload: dict, *, ok: bool) -> None:
        sub = "done" if ok else "failed"
        blob = shlex.quote(json.dumps(payload))
        dest = f"{self.root}/{sub}/{job_id}.json"
        self._run(
            f"printf '%s' {blob} > {shlex.quote(dest)}.tmp "
            f"&& mv {shlex.quote(dest)}.tmp {shlex.quote(dest)} "
            f"&& rm -f {shlex.quote(self.root)}/running/{job_id}.json"
        )

    def heartbeat(self) -> None:
        self._run(
            f"mkdir -p {shlex.quote(self.root)} && "
            f"date +%s > {shlex.quote(self.root)}/worker.heartbeat",
            timeout=30,
        )

    def fetch(self, remote: str, local: Path) -> bool:
        local.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["scp", "-q", "-r", f"{self.host}:{remote}", str(local)],
            capture_output=True,
            text=True,
            timeout=900,
        )
        return r.returncode == 0

    def push(self, local: Path, remote: str) -> bool:
        self._run(f"mkdir -p {shlex.quote(str(Path(remote).parent))}")
        r = subprocess.run(
            ["scp", "-q", "-r", str(local), f"{self.host}:{remote}"],
            capture_output=True,
            text=True,
            timeout=900,
        )
        return r.returncode == 0
