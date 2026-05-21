"""Backward-compat shim — canonical location is media/standards.py."""
from genlab_core.media.standards import *  # noqa: F401,F403
from genlab_core.media.standards import (  # noqa: F401
    LayoutStandard,
    Platform,
    all_standards,
    get_standard,
)
