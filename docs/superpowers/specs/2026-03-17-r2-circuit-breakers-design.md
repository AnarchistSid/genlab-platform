# R2: Circuit Breakers + Resilience

**Goal**: Add circuit breakers to all external APIs. Wrap the 105 unprotected API calls with retry + circuit breaker.
**Effort**: ~4h

## Problem

105 `urllib.request.urlopen` / `requests.get/post` calls in genlab-core have no circuit breaker. A flapping API (e.g., SharePoint 429, YouTube 403) causes repeated timeouts that cascade through the pipeline.

## Changes

### 1. Generalize CircuitBreaker from TTS to shared module

Move `CircuitBreaker` from `tts/cascade.py` to `genlab_core/http/circuit_breaker.py`. States: CLOSED → OPEN (after `failure_threshold` failures in `window_seconds`) → HALF_OPEN (probe after `recovery_timeout`).

```python
class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3,
                 window_seconds: float = 60, recovery_timeout: float = 30):
        ...
    def call(self, fn, *args, **kwargs):
        if self.state == "open":
            if time.time() - self.last_failure > self.recovery_timeout:
                self.state = "half_open"
            else:
                raise CircuitOpenError(f"{self.name} circuit is open")
        try:
            result = fn(*args, **kwargs)
            self._record_success()
            return result
        except Exception as e:
            self._record_failure()
            raise
```

### 2. Create per-service circuit breaker instances

```python
# genlab_core/http/circuit_breaker.py
SHAREPOINT_CB = CircuitBreaker("sharepoint", failure_threshold=5, recovery_timeout=60)
META_API_CB = CircuitBreaker("meta_api", failure_threshold=3, recovery_timeout=30)
YOUTUBE_CB = CircuitBreaker("youtube_api", failure_threshold=3, recovery_timeout=30)
ANTHROPIC_CB = CircuitBreaker("anthropic", failure_threshold=2, recovery_timeout=60)
```

### 3. Add @resilient decorator combining retry + circuit breaker

```python
def resilient(circuit_breaker: CircuitBreaker, max_attempts: int = 3, backoff: float = 2.0):
    """Combines retry with circuit breaker."""
```

### 4. Wire into critical API calls

Priority targets (highest-impact):
- `backlog_client.py` — all SharePoint Graph calls
- `fetch_insights.py` — platform metric fetches
- `metric_collector.py` — platform fetchers
- `trending_video_fetcher.py` — YouTube API calls
- `persona_engine.py` — Anthropic LLM call
- `niche_credentials.py` — Meta token validation

## Files

| File | Change |
|---|---|
| `genlab-core/src/genlab_core/http/circuit_breaker.py` | NEW — shared CircuitBreaker + @resilient |
| `genlab-core/src/genlab_core/http/backlog_client.py` | Wire SHAREPOINT_CB |
| `genlab-core/src/genlab_core/media/trending_video_fetcher.py` | Wire YOUTUBE_CB |
| `genlab-core/src/genlab_core/engagement/persona_engine.py` | Wire ANTHROPIC_CB |
| `genlab-core/src/genlab_core/pipeline/stages/fetch_insights.py` | Wire per-platform CB |
| `genlab-core/tests/http/test_circuit_breaker.py` | NEW — tests |
