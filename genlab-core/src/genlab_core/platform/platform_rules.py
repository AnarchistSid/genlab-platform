"""SHIM: Re-exports from genlab_core.platforms.rules."""
from genlab_core.platforms.rules import *  # noqa: F401,F403
from genlab_core.platforms.rules import (  # explicit re-exports for type checkers
    AdaptedContent,
    enforce_platform_rules,
)
