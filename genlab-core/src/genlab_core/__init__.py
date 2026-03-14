"""
genlab-core — Shared infrastructure for all Gen Lab content agents.

All submodules are lazily loaded to avoid importing heavy dependencies
until they are actually needed.
"""
from __future__ import annotations

import importlib
from typing import Any

__version__ = "0.1.0"

_SUBMODULES = [
    "cache",
    "context",
    "engagement",
    "http",
    "intelligence",
    "interfaces",
    "learning",
    "media",
    "models",
    "niche_loader",
    "orchestration",
    "platforms",
    "publishing",
    "ratelimit",
    "rendering",
    "settings",
    "storage",
    "strategies",
    "testing",
    "tts",
    "utils",
]


def __getattr__(name: str) -> Any:
    if name in _SUBMODULES:
        return importlib.import_module(f".{name}", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return _SUBMODULES + ["__version__"]
