"""Gaming writing strategy — shared BaseWritingStrategy subclass.

Migrated from WriteGamingContent (506 lines) to the shared path (Sprint 69).
All gaming-specific config lives in config/writing.yaml and config/templates.yaml.
"""
from __future__ import annotations

from pathlib import Path

from genlab_core.strategies import BaseWritingStrategy

_NICHE_ROOT = Path(__file__).resolve().parent.parent


class GamingWritingStrategy(BaseWritingStrategy):
    """Generate written content for gaming stories."""

    def __init__(self) -> None:
        super().__init__(niche_id="gaming", niche_root=_NICHE_ROOT)
