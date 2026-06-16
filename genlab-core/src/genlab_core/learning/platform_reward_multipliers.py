"""Per-platform reward multipliers for bandit updates (D3.8, AUTO #2 runbook).

Loads `genlab-core/config/platform_reward_multipliers.yaml` and exposes
``get_multiplier(platform)`` for the bandit-update path.

Backwards-compat
================
The multiplier applies only to NEW updates. Historical alpha/beta in
``bandit_arms`` reflect pre-D3.8 un-multiplied rewards and are NOT
retroactively scaled. Operator's S6 snapshots are the rollback line if
a chosen multiplier proves wrong.

Safety
======
* Missing config file → all multipliers return 1.0 (no behaviour change).
* Missing platform key → 1.0 (default).
* Malformed YAML → log + return 1.0; never raise.
* Negative or non-finite values → clamped to ``_MIN_MULTIPLIER`` and
  ``_MAX_MULTIPLIER`` so a typo can't permanently kill an arm.

The hot path in ``metric_collector._default_bandit_updater`` calls
``get_multiplier`` for every reward observation. Loading is cached
per-process so the YAML hits disk at most once.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from threading import Lock
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Sane bounds. Even an operator typo (multiplier=99 or -5) shouldn't
# permanently bias the bandit beyond what tuning can recover from.
_MIN_MULTIPLIER = 0.1
_MAX_MULTIPLIER = 3.0
_DEFAULT_MULTIPLIER = 1.0


def _config_path() -> Path:
    """Resolve the YAML config path.

    Two levels up from this module file lands in ``genlab-core/`` (the
    package's parent), so ``config/`` sits as a sibling of ``src/``.
    """
    return Path(__file__).resolve().parents[3] / "config" / "platform_reward_multipliers.yaml"


# Process-level cache + lock. The cache is invalidated on `clear_cache()`
# so the test suite can swap configs without restarting.
_CACHE: dict[str, float] | None = None
_CACHE_LOCK = Lock()


def clear_cache() -> None:
    """Reset the in-process cache. Called from tests + reload helpers."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


def _clamp(value: float) -> float:
    """Coerce a raw value into ``[_MIN_MULTIPLIER, _MAX_MULTIPLIER]``."""
    if not math.isfinite(value):
        return _DEFAULT_MULTIPLIER
    if value < _MIN_MULTIPLIER:
        return _MIN_MULTIPLIER
    if value > _MAX_MULTIPLIER:
        return _MAX_MULTIPLIER
    return value


def load_multipliers(path: Path | None = None) -> dict[str, float]:
    """Load the multiplier table from YAML, with safe defaults.

    Args:
        path: Override config path (used by tests). Defaults to the
            canonical ``platform_reward_multipliers.yaml`` location.

    Returns:
        ``{platform_id: float}`` map. Always returns a non-None dict —
        empty when the config is missing so the caller's lookup falls
        through to the default 1.0.
    """
    cfg = path or _config_path()
    if not cfg.exists():
        return {}
    try:
        with open(cfg, encoding="utf-8") as f:
            data: Any = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(
            "[platform_reward_multipliers] failed to load %s: %s — using defaults",
            cfg,
            exc,
        )
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "[platform_reward_multipliers] %s is not a dict at top level — using defaults",
            cfg,
        )
        return {}
    raw = data.get("multipliers")
    if not isinstance(raw, dict):
        logger.info(
            "[platform_reward_multipliers] no 'multipliers' block in %s — using defaults",
            cfg,
        )
        return {}

    out: dict[str, float] = {}
    for platform, value in raw.items():
        if not isinstance(platform, str):
            continue
        try:
            mult = float(value)
        except (TypeError, ValueError):
            logger.warning(
                "[platform_reward_multipliers] %s has non-numeric value %r — using default",
                platform,
                value,
            )
            continue
        out[platform.lower()] = _clamp(mult)
    return out


def get_multiplier(platform: str) -> float:
    """Return the multiplier for ``platform``, defaulting to 1.0.

    Cached after the first call. Use ``clear_cache()`` between
    config edits to force a reload.
    """
    if not platform:
        return _DEFAULT_MULTIPLIER
    global _CACHE
    if _CACHE is None:
        with _CACHE_LOCK:
            if _CACHE is None:
                _CACHE = load_multipliers()
    return _CACHE.get(platform.lower(), _DEFAULT_MULTIPLIER)
