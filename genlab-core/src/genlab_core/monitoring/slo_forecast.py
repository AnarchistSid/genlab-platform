"""Phase 2.C — SLO time-series forecasting.

Pure functions for EWMA smoothing + 24h-ahead forecast of when a
metric will cross a threshold. Consumed by
``scripts/run_slo_forecast.py`` runner + ``/api/v1/monitoring/
slo-forecasts`` endpoint.

## Approach

The observability history we have is ``pipeline_alerts.created_at``.
Every firing of a check_name (e.g., ``zero_blueprints``,
``slo:p95_pipeline``, ``download_failure_rate``) becomes a data
point. From that time series we can:

  1. Bucket into daily counts (past 14 days)
  2. Smooth with EWMA (alpha=0.3 — recent days weighted higher)
  3. Extrapolate 24h forward (simple linear regression on the
     smoothed series)
  4. Compare forecast to `warning_threshold` (default: 2× current
     baseline) and `critical_threshold` (default: 5× current)

## Output

`compute_forecast(...)` returns a SloForecast with:

  * ``check_name`` — which SLO
  * ``current_rate`` — smoothed EWMA of daily count today
  * ``forecast_rate`` — projected daily count 24h ahead
  * ``trend_pct`` — (forecast - current) / current × 100
  * ``verdict`` — 'stable' | 'watch' | 'forecast_warning' |
    'forecast_critical'
  * ``ttb_hours`` — hours until baseline × 2 breach (None if
    stable/decreasing)

## Fail-safe

Fewer than 3 data points → 'insufficient_data' verdict (avoids
noisy forecasts from cold-start). Any exception → returns None.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

# EWMA smoothing factor: alpha=0.3 means today's count contributes
# 30%, yesterday's smoothed value 70%. Empirically balanced —
# alpha=0.1 too smooth (misses real acceleration), alpha=0.5 too
# jittery (over-reacts to single-day spikes).
_EWMA_ALPHA: float = 0.3

# Warning: forecast 2× current baseline
_WARNING_MULTIPLIER: float = 2.0
# Critical: forecast 5× current baseline
_CRITICAL_MULTIPLIER: float = 5.0

# Minimum samples before we trust the forecast
_MIN_SAMPLES: int = 3


@dataclass(frozen=True)
class SloForecast:
    """One SLO check's projected trajectory."""

    check_name: str
    niche_id: str | None  # None = system-wide
    current_rate: float   # smoothed EWMA of today's daily count
    forecast_rate: float  # projected daily count 24h ahead
    trend_pct: float      # (forecast - current) / current × 100
    verdict: str          # stable | watch | forecast_warning | forecast_critical | insufficient_data
    ttb_hours: float | None  # hours until warning threshold breach, if trend is up


def _ewma(daily_counts: list[float], alpha: float = _EWMA_ALPHA) -> list[float]:
    """Exponentially-weighted moving average over a daily count series.
    Returns a same-length list where each element is the smoothed
    value at that day."""
    if not daily_counts:
        return []
    smoothed = [daily_counts[0]]
    for x in daily_counts[1:]:
        smoothed.append(alpha * x + (1 - alpha) * smoothed[-1])
    return smoothed


def _linear_slope(smoothed: list[float]) -> float:
    """Simple least-squares slope of the EWMA series over its last
    7 days. Slope is 'daily count change per day.' Positive slope =
    trend up. Uses last 7 points to weight recent trend, not the
    whole history (which would flatten the signal for older changes)."""
    tail = smoothed[-7:] if len(smoothed) >= 7 else smoothed
    n = len(tail)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(tail) / n
    num = sum((xs[i] - x_mean) * (tail[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    if den == 0:
        return 0.0
    return num / den


def compute_forecast(
    check_name: str,
    daily_counts: list[float],
    *,
    niche_id: str | None = None,
) -> SloForecast | None:
    """Compute the 24h-ahead forecast for one SLO check.

    Args:
      check_name: the pipeline_alerts.check_name identifier
      daily_counts: chronological list of daily alert counts,
        oldest to newest, one entry per day (missing days = 0.0)
      niche_id: optional niche scope

    Returns SloForecast with verdict + trajectory, or None on error.
    """
    try:
        if len(daily_counts) < _MIN_SAMPLES:
            return SloForecast(
                check_name=check_name, niche_id=niche_id,
                current_rate=0.0, forecast_rate=0.0, trend_pct=0.0,
                verdict="insufficient_data", ttb_hours=None,
            )
        smoothed = _ewma(daily_counts)
        current = smoothed[-1]
        slope = _linear_slope(smoothed)
        # Forecast 24h ahead = current + 1 day of slope
        forecast = max(0.0, current + slope)
        # Trend %: avoid div-by-zero when current is 0
        trend_pct = (
            ((forecast - current) / current * 100.0)
            if current > 0 else 0.0
        )

        verdict = _classify_verdict(current, forecast)
        ttb = _time_to_warning_breach(current, slope) if slope > 0 else None

        return SloForecast(
            check_name=check_name, niche_id=niche_id,
            current_rate=round(current, 3),
            forecast_rate=round(forecast, 3),
            trend_pct=round(trend_pct, 1),
            verdict=verdict,
            ttb_hours=round(ttb, 1) if ttb is not None else None,
        )
    except Exception as exc:
        logger.warning(
            "[slo_forecast] compute_forecast failed check=%s: %s",
            check_name, exc,
        )
        return None


def _classify_verdict(current: float, forecast: float) -> str:
    """Compare forecast against multiplicative thresholds."""
    # Special case: current is zero. If forecast is also zero, stable.
    # If forecast > 0, watch (accelerating from baseline).
    if current == 0:
        if forecast == 0:
            return "stable"
        return "watch"

    ratio = forecast / current
    if ratio >= _CRITICAL_MULTIPLIER:
        return "forecast_critical"
    if ratio >= _WARNING_MULTIPLIER:
        return "forecast_warning"
    if ratio >= 1.2:  # 20% up from current
        return "watch"
    return "stable"


def _time_to_warning_breach(current: float, slope: float) -> float | None:
    """Hours until the smoothed value exceeds warning threshold
    (2× current). Returns None if slope is non-positive or current is 0."""
    if slope <= 0 or current <= 0:
        return None
    # target = 2 * current; days = (target - current) / slope = current / slope
    days = current / slope
    return days * 24.0


def bucket_by_day(
    events: list[datetime], *, days_back: int = 14, now: datetime | None = None,
) -> list[float]:
    """Bucket a list of alert-fired datetimes into daily counts.
    Returns days_back-long list, oldest-to-newest, missing days = 0."""
    now = now or datetime.now(UTC)
    today = now.date()
    counts: dict[str, int] = defaultdict(int)
    for ts in events:
        # Normalize to date string
        day = ts.date().isoformat()
        counts[day] += 1
    result = []
    for i in range(days_back - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        result.append(float(counts.get(day, 0)))
    return result


__all__ = [
    "SloForecast",
    "bucket_by_day",
    "compute_forecast",
]
