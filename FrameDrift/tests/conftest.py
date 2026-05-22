"""Test harness for FrameDrift.

Adds FrameDrift root to sys.path for fd_strategies imports and disables
the GENLAB_USE_POSTGRES path so tests never silently reach into a real
production database (the bandit arm loader and other read paths used to
attempt connections to whatever DATABASE_URL was set in .env, including
production, which made tests slow + flaky depending on network).
"""

import os
import sys
from pathlib import Path

_CHANNEL_ROOT = str(Path(__file__).resolve().parent.parent)
if _CHANNEL_ROOT not in sys.path:
    sys.path.insert(0, _CHANNEL_ROOT)

# Force tests to never hit a real Postgres. The arm loader and a few
# other code paths check this env var and short-circuit when false.
os.environ.setdefault("GENLAB_USE_POSTGRES", "false")
os.environ.setdefault("DATABASE_URL", "")
