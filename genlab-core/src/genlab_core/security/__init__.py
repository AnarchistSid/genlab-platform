"""Cross-cutting security primitives shared by every layer of the codebase.

The contents here are intentionally tiny and pure — no I/O, no external
deps, no business logic. The goal is to give every other module ONE place
to reach for the small set of "this is the safe way" helpers (sanitising
logs, etc.) rather than each module reinventing them and drifting.
"""

from .log_sanitizer import scrub_token

__all__ = ["scrub_token"]
