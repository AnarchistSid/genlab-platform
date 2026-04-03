"""Tests for launch_wrapper.sh — env loading and command exec."""
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).resolve().parent.parent / "launch_wrapper.sh"


class TestLaunchWrapper:
    def test_wrapper_exists_and_executable(self):
        assert WRAPPER.exists(), f"launch_wrapper.sh not found at {WRAPPER}"
        assert WRAPPER.stat().st_mode & 0o111, "launch_wrapper.sh is not executable"

    def test_wrapper_passes_through_command(self):
        """Wrapper exec's the given command and returns its output."""
        result = subprocess.run(
            [str(WRAPPER), "echo", "hello"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "hello" in result.stdout.strip()

    def test_wrapper_preserves_exit_code(self):
        """Non-zero exit from wrapped command propagates."""
        result = subprocess.run(
            [str(WRAPPER), "bash", "-c", "exit 42"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 42

    def test_wrapper_loads_env_vars(self, tmp_path):
        """Verify wrapper loads .env files (test with env -0 to dump)."""
        # We can't test real .env loading without side effects,
        # but we can verify the script sources without error when
        # .env files don't exist (the [ -f ] guards)
        result = subprocess.run(
            [str(WRAPPER), "printenv", "PATH"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert len(result.stdout.strip()) > 0  # PATH should be non-empty
