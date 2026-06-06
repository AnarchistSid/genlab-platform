"""Canonical per-platform engagement metrics type.

Used by every metric fetcher in :mod:`genlab_core.platforms.metrics` so that
downstream code (analytics writes, reward computation, dashboard read paths)
sees one consistent shape regardless of which platform produced the row.

All fields are optional because no single platform exposes every metric:

* YouTube  → ``views``, ``likes``, ``comments`` (and ``reach=views`` as alias)
* Instagram → ``views`` (from ``reach``), ``likes``, ``comments``, ``shares``,
              ``saves`` (from ``saved``)
* Facebook  → ``views``, ``likes`` (from ``reactions``), ``comments``,
              ``shares``, ``watch_time_ms``
* X         → ``views`` (from ``impressions``), ``likes``, ``comments``
              (from ``replies``), ``shares`` (from ``retweets``)

The ``engagement`` field is derived (``likes + comments + shares``) and is
present whenever the inputs are.

Callers should always use ``.get(key, 0)`` — missing keys mean "platform
doesn't expose this", not "the post is at zero".
"""

from __future__ import annotations

from typing import TypedDict


class PlatformMetrics(TypedDict, total=False):
    """Per-platform engagement snapshot. All keys optional."""

    views: int
    likes: int
    comments: int
    shares: int
    saves: int
    reach: int
    impressions: int
    watch_time_ms: int
    engagement: int
