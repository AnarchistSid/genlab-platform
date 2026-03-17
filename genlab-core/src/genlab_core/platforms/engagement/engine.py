"""DEPRECATED: Use genlab_core.engagement instead.

This module is kept for backward compatibility. All new code should
import from genlab_core.engagement directly.

Deprecated in Sprint 24 (2026-03-10).
"""
import warnings as _warnings

_warnings.warn(
    "genlab_core.platform.engagement_engine is deprecated. "
    "Use genlab_core.engagement instead.",
    DeprecationWarning,
    stacklevel=2,
)

# Re-exports for backward compatibility
from genlab_core.engagement.persona_schema import NichePersona  # noqa: F401
from genlab_core.engagement.timing import human_delay as human_like_delay_seconds  # noqa: F401
from genlab_core.engagement.toxicity_gate import ToxicityGate  # noqa: F401
