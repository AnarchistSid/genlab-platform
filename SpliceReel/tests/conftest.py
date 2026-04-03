"""Add SpliceReel root to sys.path for sr_strategies imports."""
import sys
from pathlib import Path

_CHANNEL_ROOT = str(Path(__file__).resolve().parent.parent)
if _CHANNEL_ROOT not in sys.path:
    sys.path.insert(0, _CHANNEL_ROOT)
