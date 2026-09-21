"""Capability registry — selection by KIND, not by app name.

See ``registry.py``. Six apps were in use out of a hundred-plus because
selection-by-kind was designed and never built.
"""

from genlab_core.capabilities.registry import (
    Capability,
    UnknownKind,
    Unselectable,
    for_kind,
    select,
)

__all__ = ["Capability", "UnknownKind", "Unselectable", "for_kind", "select"]
