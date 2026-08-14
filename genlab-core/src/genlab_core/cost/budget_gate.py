"""Phase 2.D — Autonomous cost throttling.

Bounds daily LLM spend by throttling optional callers as the daily
total climbs. Prevents another Anthropic-credit-exhaustion silent
outage (07-13 → 08-09 strategist gap traced to this exact class).

## Throttle ladder

Reads today's `pipeline_run_costs.llm_usd` sum. Returns a
`ThrottleLevel` that consumers translate into behavior:

  | Level              | Threshold | Effect                              |
  |--------------------|-----------|-------------------------------------|
  | none               | < $5/day  | all callers allowed                 |
  | reduce_50pct       | ≥ $5      | optional callers skip 50% of calls  |
  | pause_optional     | ≥ $10     | all optional callers paused         |
  | emergency_shutoff  | ≥ $20     | only essential callers allowed      |

## Caller taxonomy

`is_call_allowed(caller_type)` interprets:

  * `'essential'` — writer (content generation is pipeline-critical);
    never blocked except at emergency_shutoff
  * `'optional'` — strategist, LLM reviewer, hook classifier, meta-
    analysis; throttled first
  * `'ambient'` — same as optional today; reserved for future
    fine-tuning

Callers self-tag when checking. Missing tag defaults to 'optional'
(fail-safe — the important case is "don't block writer").

## Bypass

`GENLAB_COST_BUDGET_DISABLED=1` in env → always returns none/allowed.
Emergency operator override for debugging or one-off backfill runs.

## Caching

Query hits DB once per 60s per process. Callers can call
`is_call_allowed()` in tight loops without DB thrash. Cache is
process-local — a spike happening between processes takes up to
60s to propagate, which is acceptable given daily-cap semantics.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Final

logger = logging.getLogger(__name__)

# Bound thresholds — bumping requires updating the pin test that
# ensures they never invert.
_REDUCE_50_THRESHOLD_USD: Final[float] = 5.0
_PAUSE_OPTIONAL_THRESHOLD_USD: Final[float] = 10.0
_EMERGENCY_THRESHOLD_USD: Final[float] = 20.0

# Cache TTL: query pipeline_run_costs once per minute per process.
_CACHE_TTL_S: Final[int] = 60

# Bypass env var — operator emergency override
_BYPASS_ENV_VAR: Final[str] = "GENLAB_COST_BUDGET_DISABLED"

# Per-process cache: (spend_usd, cached_at_monotonic)
_cache: dict[str, tuple[float, float]] = {}


class ThrottleLevel(str, Enum):
    NONE = "none"
    REDUCE_50PCT = "reduce_50pct"
    PAUSE_OPTIONAL = "pause_optional"
    EMERGENCY_SHUTOFF = "emergency_shutoff"


@dataclass(frozen=True)
class BudgetStatus:
    """Snapshot returned by `get_status()` for surfaces."""
    spend_today_usd: float
    throttle_level: ThrottleLevel
    reduce_50_threshold: float
    pause_threshold: float
    emergency_threshold: float


def _is_bypassed() -> bool:
    return os.environ.get(_BYPASS_ENV_VAR, "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _daily_llm_spend_usd() -> float:
    """Query today's LLM spend from pipeline_run_costs. Cached 60s.
    Fail-open (returns 0.0 on any error — worse to silently block
    than to overshoot the cap by one caller)."""
    now = time.monotonic()
    cached = _cache.get("spend")
    if cached is not None and (now - cached[1]) < _CACHE_TTL_S:
        return cached[0]

    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        _cache["spend"] = (0.0, now)
        return 0.0

    try:
        import psycopg
        # Cross-niche read; connect straight (not via pg_connect —
        # this is a system-wide roll-up, not niche-scoped).
        with psycopg.connect(dsn, connect_timeout=5) as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(llm_usd), 0)::float AS spend
                FROM pipeline_run_costs
                WHERE completed_at >= CURRENT_DATE
                """,
            ).fetchone()
        spend = float(row[0]) if row else 0.0
    except Exception as exc:
        logger.debug("[budget_gate] daily spend query failed: %s", exc)
        spend = 0.0

    _cache["spend"] = (spend, now)
    return spend


def get_throttle_level() -> ThrottleLevel:
    """Return the current throttle level based on today's spend."""
    if _is_bypassed():
        return ThrottleLevel.NONE
    spend = _daily_llm_spend_usd()
    if spend >= _EMERGENCY_THRESHOLD_USD:
        return ThrottleLevel.EMERGENCY_SHUTOFF
    if spend >= _PAUSE_OPTIONAL_THRESHOLD_USD:
        return ThrottleLevel.PAUSE_OPTIONAL
    if spend >= _REDUCE_50_THRESHOLD_USD:
        return ThrottleLevel.REDUCE_50PCT
    return ThrottleLevel.NONE


def is_call_allowed(caller_type: str = "optional") -> bool:
    """Return True if the caller should proceed with its LLM call.

    Caller types:
      * 'essential' — writer, always allowed except at emergency
      * 'optional' (default) — strategist, reviewer, hook_classifier
      * 'ambient' — same as optional today

    Fail-open: unknown caller_type treated as 'optional'. Bypass env
    short-circuits to True.
    """
    if _is_bypassed():
        return True

    level = get_throttle_level()
    ct = (caller_type or "optional").strip().lower()

    # Essential callers only stop at emergency shutoff
    if ct == "essential":
        return level != ThrottleLevel.EMERGENCY_SHUTOFF

    # Optional/ambient: three tiers
    if level == ThrottleLevel.NONE:
        return True
    if level == ThrottleLevel.REDUCE_50PCT:
        # 50% skip — coin flip. Deterministic per-process seeding is
        # a follow-up; for now cryptographic random is fine.
        import secrets
        return secrets.randbelow(2) == 0
    # PAUSE_OPTIONAL or EMERGENCY_SHUTOFF → blocked
    return False


def get_status() -> BudgetStatus:
    """One-shot snapshot for surfaces (dashboard card, /api/v1/cost-budget)."""
    return BudgetStatus(
        spend_today_usd=_daily_llm_spend_usd(),
        throttle_level=get_throttle_level(),
        reduce_50_threshold=_REDUCE_50_THRESHOLD_USD,
        pause_threshold=_PAUSE_OPTIONAL_THRESHOLD_USD,
        emergency_threshold=_EMERGENCY_THRESHOLD_USD,
    )


def reset_cache() -> None:
    """Test-only: drop the cache so a fresh query fires next time."""
    _cache.clear()


__all__ = [
    "BudgetStatus",
    "ThrottleLevel",
    "get_status",
    "get_throttle_level",
    "is_call_allowed",
    "reset_cache",
]
