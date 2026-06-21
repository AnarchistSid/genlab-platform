"""Mtime-keyed YAML loader cache.

Dashboard API endpoints repeatedly read the same niche/config YAMLs on
each request (registry, sources.yaml, scoring_weights.yaml, alerting.yaml,
affiliate_catalog.yaml, ...). The disk I/O + yaml.safe_load parse adds up
when the endpoint is polled every 60s by Mission Control. The contents
change on operator edits (rare); for the steady-state case the data is
stable and the read is wasted.

This module caches each parsed YAML by ``Path``, invalidating when the
file's mtime changes. That gives:

* Live updates — operator edits propagate on the next request (no TTL
  staleness window to tune).
* Zero cost when files are stable — a single ``stat()`` per request to
  compare mtimes (microseconds) replaces a full disk-read + YAML parse.

The cache is process-local. Each gunicorn worker maintains its own;
that's fine because the dashboard runs a single eventlet worker (see
``runbooks/review_server_wrapper.sh``). If the deploy ever scales to
multiple workers, each pays a one-time per-file warmup but the steady
state is still ~zero-cost.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# Per-path cache entry: (mtime_seen, parsed_value). The parsed value can
# be ``None`` if the YAML file legitimately parses to nothing (empty
# document) — we still cache that to avoid re-parsing.
_yaml_cache: dict[Path, tuple[float, Any]] = {}


def load_yaml_cached(path: Path) -> Any | None:
    """Read and parse ``path`` as YAML, using a process-local mtime cache.

    Returns ``None`` if the file doesn't exist or can't be read; the
    caller's existing error-handling stays unchanged.

    Tests that need a clean cache can call ``clear_yaml_cache()``.
    """
    try:
        mtime = path.stat().st_mtime
    except (OSError, FileNotFoundError):
        return None

    cached = _yaml_cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        # Don't poison the cache on parse errors — the next request gets
        # a fresh attempt, matching the prior (uncached) error behavior.
        logger.warning("[yaml_cache] failed to load %s: %s", path, exc)
        return None

    _yaml_cache[path] = (mtime, data)
    return data


def clear_yaml_cache() -> None:
    """Drop all cached entries — for tests that need a clean slate."""
    _yaml_cache.clear()
