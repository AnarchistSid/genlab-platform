"""Shared environment utilities for Gen Lab agents."""
from __future__ import annotations

import os
from pathlib import Path


def get_agent_root() -> Path:
    """Resolve AGENT_ROOT from the environment and return it as a Path.

    Raises RuntimeError if the variable is not set. We never fall back to
    cwd because that produces different paths depending on where the process
    is invoked — correct in development, silently wrong in cron and launchd.
    """
    root = os.environ.get("AGENT_ROOT", "")
    if not root:
        raise RuntimeError(
            "AGENT_ROOT environment variable is not set. "
            "Set it to the niche root directory before running any pipeline "
            "component (e.g. AGENT_ROOT=/Users/.../CriticalRush). "
            "For cron entries, add it before the python command: "
            "AGENT_ROOT=/path/to/CriticalRush python -m ..."
        )
    return Path(root)
