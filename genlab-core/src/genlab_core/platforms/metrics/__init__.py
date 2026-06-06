"""Canonical per-platform engagement metrics fetchers.

One place to add a metric, one place to fix a bug. Replaces parallel
implementations in:

* :mod:`genlab_core.scripts.run_fetch_insights`
* :mod:`genlab_core.pipeline.stages.fetch_insights`
* :mod:`genlab_core.learning.metric_collector`

each of which had its own copy with subtly different return shapes and
divergent bug surfaces (Gap 2 — PR #54 — fixed the pipeline-stage path
but missed the script path, requiring a follow-up PR a week later).

This module is the new home; the three legacy call-sites migrate
incrementally. ``run_fetch_insights`` is the first delegated caller —
``fetch_insights`` (pipeline stage) and ``metric_collector`` follow in
later changes once their return-shape contracts are unified.
"""

from .types import PlatformMetrics
from .youtube import fetch_youtube

__all__ = [
    "PlatformMetrics",
    "fetch_youtube",
]
