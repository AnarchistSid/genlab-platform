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


#: Persisted story-summary ceiling. ANIME-03 §2.
#:
#: Was 255 at the persist site in push_to_backlog, and `stories.summary` is
#: `text` — so the 255 was never a column limit, it was a literal, and no
#: migration was needed to lift it.
#:
#: What it cost: AniList returns 200-2000 characters. The Firefly Wedding
#: description ends at character 255 on "...redeem her worth in her", and the
#: sentence that follows is the one carrying the story:
#:
#:   "...she finds herself the target of the mysterious assassin Shinpei...
#:    she makes a desperate proposal - of marriage!"
#:
#: Every hook for that title was written from the setup with the turn cut off.
#: Measured 2026-09-21: 34 anime, 23 movies and 7 sports stories sat at
#: exactly 255, i.e. truncated.
#:
#: Still bounded. `text` is unbounded but a story summary is not a document,
#: and an unbounded field is a payload nobody sized.
SUMMARY_MAX_CHARS = 4000
