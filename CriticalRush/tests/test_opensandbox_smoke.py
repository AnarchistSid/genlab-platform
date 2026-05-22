"""
Phase 0 smoke test: Verify OpenSandbox server + Docker runtime works locally.

Run with:
    1. Start server: uv run opensandbox-server  (in separate terminal)
    2. Run test: uv run --package CriticalRush pytest tests/test_opensandbox_smoke.py -v

Requires: opensandbox, opensandbox-server, Docker running
"""

import os
from datetime import timedelta

import pytest
import requests

# Skip entire module if opensandbox not installed
opensandbox = pytest.importorskip("opensandbox")
from opensandbox.config import ConnectionConfigSync  # noqa: I001

from opensandbox import SandboxSync  # noqa: I001

SANDBOX_SERVER = os.getenv("OPENSANDBOX_SERVER", "http://127.0.0.1:8080")
SANDBOX_IMAGE = os.getenv("OPENSANDBOX_IMAGE", "ubuntu:22.04")


def _connection_config() -> ConnectionConfigSync:
    return ConnectionConfigSync(domain=SANDBOX_SERVER)


def _server_reachable() -> bool:
    try:
        r = requests.get(f"{SANDBOX_SERVER}/health", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _server_reachable(),
    reason=f"OpenSandbox server not running at {SANDBOX_SERVER}",
)


@pytest.fixture
def sandbox():
    """Create a sandbox and kill it after the test."""
    sbx = SandboxSync.create(
        image=SANDBOX_IMAGE,
        timeout=timedelta(seconds=60),
        metadata={"test": "genlab-phase0"},
        connection_config=_connection_config(),
    )
    yield sbx
    sbx.kill()


def test_server_health():
    """OpenSandbox server is reachable."""
    resp = requests.get(f"{SANDBOX_SERVER}/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_sandbox_create_and_run(sandbox):
    """Create a sandbox, run echo, verify stdout."""
    result = sandbox.commands.run("echo 'hello from sandbox'")
    stdout = "".join(line.text for line in result.logs.stdout)
    assert "hello from sandbox" in stdout


def test_sandbox_file_write_read(sandbox):
    """Write a file in the sandbox and read it back."""
    from opensandbox.models import WriteEntry

    sandbox.files.write_files(
        [WriteEntry(path="/tmp/genlab_test.txt", data="GenLab Phase 0", mode=644)]
    )
    content = sandbox.files.read_file("/tmp/genlab_test.txt")
    assert "GenLab Phase 0" in content


def test_sandbox_bash_available(sandbox):
    """Verify bash works inside the sandbox."""
    result = sandbox.commands.run("bash --version")
    stdout = "".join(line.text for line in result.logs.stdout)
    assert "bash" in stdout.lower()
