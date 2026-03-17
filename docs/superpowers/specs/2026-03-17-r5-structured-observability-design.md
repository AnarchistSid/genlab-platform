# R5: Structured Observability

**Goal**: JSON structured logging + pipeline metrics file + health endpoint + alerting config.
**Effort**: ~3h

## Problem

Plain `logging.getLogger` everywhere. Logs can't be queried or aggregated. No metrics export. No alerting beyond SLOs in run_report.

## Changes

### 1. Add structlog JSON logging

Add `structlog` to genlab-core deps. Configure in `genlab_core/observability/logging.py`:

```python
import structlog

def configure_logging(json_output: bool = True):
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer() if json_output
            else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
```

### 2. Pipeline metrics JSONL writer

New `genlab_core/observability/metrics_writer.py`:

```python
class PipelineMetrics:
    def record_stage(self, stage: str, duration_ms: float, status: str, **kwargs):
        self._entries.append({"stage": stage, "duration_ms": duration_ms, "status": status, **kwargs})

    def flush(self, run_dir: Path):
        (run_dir / "metrics.jsonl").write_text(
            "\n".join(json.dumps(e) for e in self._entries)
        )
```

Wire into `StageRunner` — auto-records timing for every stage.

### 3. Detailed health endpoint

Add to dashboard: `GET /api/v1/health/detailed` returning:
```json
{
  "services": {"redis": "up", "prefect": "up", "dashboard": "up"},
  "last_run": {"gaming": "2026-03-17T04:00Z", "anime": "..."},
  "error_rate_24h": 0.02,
  "disk_usage_pct": 34
}
```

### 4. Alerting config

`genlab-core/config/alerting.yaml`:
```yaml
thresholds:
  pipeline_duration_p95_seconds: 600
  error_rate_24h_pct: 10
  disk_usage_pct: 80
  engagement_reply_rate_1h: 0  # Alert if no replies in 1h
```

## Files

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/observability/__init__.py` | NEW |
| `genlab-core/src/genlab_core/observability/logging.py` | NEW — structlog config |
| `genlab-core/src/genlab_core/observability/metrics_writer.py` | NEW — JSONL metrics |
| `genlab-core/src/genlab_core/pipeline/stage_runner.py` | Wire metrics recording |
| `genlab-core/config/alerting.yaml` | NEW |
| `genlab-core/pyproject.toml` | Add structlog dep |
| `genlab-core/tests/observability/test_metrics_writer.py` | NEW |
