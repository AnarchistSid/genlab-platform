"""Typed loader for ``genlab-core/config/alerting.yaml``.

The audit (M-1) found this YAML was effectively dead: 11 threshold keys
documented but **zero code references**. Operators were editing it
thinking they changed behaviour. This module is the single reader.

Public surface
--------------
* :class:`AlertingConfig` — Pydantic root model. Nested
  :class:`Thresholds`, :class:`AffiliateAlerts`, :class:`PublishingAlerts`.
* :func:`get_alerting_config` — ``@lru_cache``-d loader. Returns the
  validated model from ``genlab-core/config/alerting.yaml`` (or field
  defaults when the YAML is missing — keeps unit-test paths zero-config).
* :func:`_reset_cache_for_tests` — test-only helper to drop the cached
  load so a follow-up :func:`get_alerting_config` re-reads from disk.

Wiring status (2026-06-08)
--------------------------
Only the disk_usage_pct and swap-related thresholds are wired in
``health_monitor.check_disk`` and ``check_swap``. Other keys remain
unwired but no longer drift silently because the loader validates them
on import — operators get a Pydantic error if a typo lands in the YAML.

Follow-up tickets for wiring the rest:
    * ``thresholds.pipeline_duration_p95_seconds`` — health_monitor needs a
      ``check_pipeline_duration_p95`` that reads run_report.json timings.
    * ``thresholds.engagement_reply_rate_1h`` — engagement poller needs a
      counter sink.
    * ``publishing.*`` — multiple sites in publishing analytics flow.
    * ``affiliate.*`` — affiliate flow on a separate cron.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, Field


class Thresholds(BaseModel):
    """Process-wide thresholds for the pipeline observability checks."""

    pipeline_duration_p95_seconds: int = Field(
        default=600,
        gt=0,
        description=(
            "Maximum acceptable p95 pipeline duration. Consistently above "
            "this indicates a perf regression."
        ),
    )
    error_rate_24h_pct: int = Field(
        default=10,
        ge=0,
        le=100,
        description="24-hour error rate (percent of pipeline runs that fail).",
    )
    disk_usage_pct: int = Field(
        default=80,
        ge=0,
        le=100,
        description=(
            "Disk usage percent at which the warning alert fires. The "
            "critical alert fires at +10% above this (so 80 → warn, 90 → "
            "critical)."
        ),
    )
    engagement_reply_rate_1h: int = Field(
        default=0,
        ge=0,
        description=(
            "Minimum engagement replies processed in a 1-hour window. "
            "Alert if no replies are processed (poller or worker down)."
        ),
    )
    # Swap thresholds were inline literals in check_swap; surfaced here so
    # the 4 GB box can be tuned without a deploy. Both required > 0.
    swap_warning_mb: int = Field(
        default=500,
        gt=0,
        description="Swap usage in MB above which the warning alert fires.",
    )
    swap_critical_pct: float = Field(
        default=0.9,
        gt=0,
        le=1.0,
        description=(
            "Swap usage as fraction of total swap above which the critical "
            "alert fires (imminent OOM signal on the 4 GB box)."
        ),
    )


class AffiliateAlerts(BaseModel):
    """Thresholds for the affiliate flow's separate cron."""

    zero_match_runs_alert: int = Field(default=3, ge=1)
    min_match_rate_pct: int = Field(default=5, ge=0, le=100)
    placeholder_url_alert: bool = Field(default=True)
    link_health_failures: int = Field(default=5, ge=1)


class PublishingAlerts(BaseModel):
    """Thresholds for publish-pipeline observability."""

    consecutive_missed_publishes: int = Field(default=2, ge=1)
    min_success_rate_pct: int = Field(default=60, ge=0, le=100)
    max_credential_skips_per_day: int = Field(default=10, ge=0)
    youtube_quota_usage_pct: int = Field(default=80, ge=0, le=100)
    max_cdn_failures_per_day: int = Field(default=3, ge=0)
    max_publish_failed_blueprints: int = Field(default=5, ge=0)
    gatekeeper_blocked_consecutive: int = Field(default=3, ge=1)


class AlertingConfig(BaseModel):
    """Root model for ``alerting.yaml``. Each nested block is optional and
    falls back to the field defaults above."""

    thresholds: Thresholds = Field(default_factory=Thresholds)
    affiliate: AffiliateAlerts = Field(default_factory=AffiliateAlerts)
    publishing: PublishingAlerts = Field(default_factory=PublishingAlerts)


_DEFAULT_YAML_PATH: Final[Path] = Path(__file__).resolve().parents[3] / "config" / "alerting.yaml"


@lru_cache(maxsize=1)
def get_alerting_config() -> AlertingConfig:
    """Return the validated platform-wide alerting config.

    Reads ``genlab-core/config/alerting.yaml`` once per process and caches
    the parsed result. Missing file → field defaults. Malformed YAML →
    ``yaml.YAMLError`` propagates (opt-in operational state; a bad file
    should fail loudly, not silently fall back).
    """
    if not _DEFAULT_YAML_PATH.exists():
        return AlertingConfig()

    with open(_DEFAULT_YAML_PATH) as f:
        data = yaml.safe_load(f) or {}

    return AlertingConfig.model_validate(data)


def _reset_cache_for_tests() -> None:
    """Test-only helper — drop the cached load so a follow-up
    :func:`get_alerting_config` re-reads from disk."""
    get_alerting_config.cache_clear()
