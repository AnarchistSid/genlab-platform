"""Export cost metrics to Prometheus via OpenTelemetry."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from opentelemetry import metrics  # noqa: F401
    from opentelemetry.sdk.metrics import MeterProvider  # noqa: F401
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader  # noqa: F401

    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False

# Prometheus metrics (always available since prometheus_client is installed)
try:
    from prometheus_client import Counter, Gauge

    _llm_cost_total = Counter(
        "genlab_llm_cost_usd_total", "Total LLM cost in USD", ["model", "niche"]
    )
    _llm_calls_total = Counter(
        "genlab_llm_calls_total", "Total LLM API calls", ["model", "niche"]
    )
    _budget_remaining = Gauge(
        "genlab_budget_remaining_pct",
        "Budget remaining percentage",
        ["niche"],
    )
    _HAS_PROM = True
except ImportError:
    _HAS_PROM = False


def record_llm_cost(model: str, cost_usd: float, niche: str = "unknown") -> None:
    if _HAS_PROM:
        _llm_cost_total.labels(model=model, niche=niche).inc(cost_usd)
        _llm_calls_total.labels(model=model, niche=niche).inc(1)


def record_budget_remaining(niche: str, pct: float) -> None:
    if _HAS_PROM:
        _budget_remaining.labels(niche=niche).set(pct)
