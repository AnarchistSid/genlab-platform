"""Shared content-quality constants.

Single-source-of-truth values that MUST NOT DRIFT across the write side
(hook generator, base_hooks) and the render side (frame_compositor).

Class-of-bug this module closes: **same-invariant-two-paths**. Prior
state had three independent hardcoded ``60`` sites for the hook char
ceiling (llm_hook_generator.py:1190, base_hooks.py:215,
frame_compositor.py:113). Bumping one silently diverged from the
others → operator changed a limit, only 1 of 3 gates honored it.

CLAUDE.md documents ``≤60 characters`` as the load-bearing content-
quality rule for hooks. This module centralizes it. Codified
2026-07-14 during the class-of-bug scan iteration.
"""

from __future__ import annotations

# Maximum hook length in characters. Load-bearing per CLAUDE.md
# ("Never write a hook longer than 60 characters"). Enforced at
# multiple pipeline stages — all sites MUST import from here.
MAX_HOOK_CHARS = 60

# Per-line hook char limit for frame layout (compositor wraps hooks
# into 2-line max). Kept alongside MAX_HOOK_CHARS since both are
# render-time invariants.
HOOK_MAX_CHARS_PER_LINE = 35
