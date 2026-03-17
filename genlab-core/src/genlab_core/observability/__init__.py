"""Structured observability for Gen Lab pipelines.

Provides:
- ``configure_logging()`` — structlog-based JSON/console logging that wraps stdlib
- ``PipelineMetrics`` — per-stage timing/status recorder flushed to metrics.jsonl
"""

from genlab_core.observability.logging import configure_logging
from genlab_core.observability.metrics_writer import PipelineMetrics

__all__ = ["configure_logging", "PipelineMetrics"]
