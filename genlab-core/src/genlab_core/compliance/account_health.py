"""Per-platform account-health signals (PR #570, 2026-06-25 — Phase A close).

Phase A step 5 — the LAST compliance-moat PR. Detects engagement-rate
cliffs that precede shadowbans. Each shadowban-recovery report from
operators in 2023-2025 contained the same pattern: average reach
dropped 5-20× over 24-48h with no content changes. The system can
detect this 12-48h faster than the operator can.

## How

  baseline = avg reach over last 14 days per (niche, platform)
  recent   = avg reach over last 48h per (niche, platform)
  ratio    = recent / baseline

  ratio < 0.1  → CRITICAL  (publishing should pause)
  ratio < 0.5  → WARNING   (operator should investigate)
  ratio >= 0.5 → HEALTHY
  insufficient data → None (fail-OPEN, proceed as healthy)

Insufficient data conditions:
  * < 5 publishes in 14-day baseline window
  * < 2 publishes in last 48h window
  * baseline avg reach is 0 (ratio undefined; could be platform
    just not publishing yet)
  * DB unavailable / DSN missing

The fail-OPEN choice is deliberate: locking publishing for the wrong
reason (transient DB hiccup, new niche with no history yet) is worse
than missing a shadowban signal. The downstream gate enforcement
PR (per-niche flag) lets operators decide which trade-off they want.

## What this PR ships

  * Replace the get_signal stub from PR #566 with real implementation
  * Register check_account_health into policy_gate (observation-only,
    same Phase A pattern as the other 3 checks)
  * 18 tests covering baseline + ratio + insufficient-data + the
    Phase A WARN behavior

## Phase A status after this PR

  #566 — Foundation ✅
  #567 — AI disclosure ✅
  #568 — Copyright attribution ✅
  #569 — Spam pattern detection ✅
  #570 — Account health (this PR) — Phase A COMPLETE

After this, Phase B (autonomy expansion — source discovery, per-
platform product variation, auto-approval enforcement) becomes safe
to ship because the compliance moat catches the new failure modes
each Phase B PR introduces.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HealthSignal:
    """Per-platform health snapshot.

    state — 'healthy' | 'warning' | 'critical'
    message — human-readable summary for the dashboard card
    evidence — structured detail dict (baseline + recent + ratio)
    """

    state: Literal["healthy", "warning", "critical"]
    message: str
    evidence: dict = field(default_factory=dict)


# Thresholds for cliff detection. Conservative defaults — operators
# can tune via env vars without a code deploy.
CRITICAL_RATIO_THRESHOLD: float = float(os.environ.get("GENLAB_HEALTH_CRITICAL_RATIO", "0.1"))
WARNING_RATIO_THRESHOLD: float = float(os.environ.get("GENLAB_HEALTH_WARNING_RATIO", "0.5"))

# Minimum samples to compute a meaningful signal. Too few → None
# (fail-OPEN — proceed as healthy). 5 publishes in 14d covers all
# 5 niches; 2 in 48h covers the minimum publish cadence.
MIN_BASELINE_SAMPLES: int = int(os.environ.get("GENLAB_HEALTH_MIN_BASELINE", "5"))
MIN_RECENT_SAMPLES: int = int(os.environ.get("GENLAB_HEALTH_MIN_RECENT", "2"))


def _connect():
    """Lazy-import + connect helper. None on missing DSN / connection
    error — get_signal then fail-OPEN with None."""
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        logger.debug("[account_health] DATABASE_URL not set; signal disabled")
        return None
    try:
        from psycopg.rows import dict_row

        from genlab_core.storage.tenant_context import pg_connect

        return pg_connect(dsn, row_factory=dict_row, niche_id="all", connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 — fail-open per contract
        logger.warning("[account_health] connection failed: %s", exc)
        return None


def _compute_reach_stats(platform: str, niche_id: str) -> dict | None:
    """Query analytics for baseline (14d) + recent (48h) avg reach.

    Returns None when insufficient samples in either window.
    Returns dict with baseline_avg, recent_avg, baseline_n, recent_n
    when both windows have enough data.
    """
    conn_cm = _connect()
    if conn_cm is None:
        return None
    try:
        with conn_cm as conn:
            row = conn.execute(
                """
                SELECT
                  AVG(CASE
                      WHEN collected_at > NOW() - INTERVAL '14 days'
                      THEN COALESCE((extra->>'reach')::numeric, 0)
                  END) AS baseline_avg,
                  COUNT(CASE
                      WHEN collected_at > NOW() - INTERVAL '14 days'
                      THEN 1
                  END) AS baseline_n,
                  AVG(CASE
                      WHEN collected_at > NOW() - INTERVAL '48 hours'
                      THEN COALESCE((extra->>'reach')::numeric, 0)
                  END) AS recent_avg,
                  COUNT(CASE
                      WHEN collected_at > NOW() - INTERVAL '48 hours'
                      THEN 1
                  END) AS recent_n
                FROM analytics
                WHERE niche_id = %s AND platform = %s
                  AND collected_at > NOW() - INTERVAL '14 days'
                """,
                (niche_id, platform),
            ).fetchone()
        if row is None:
            return None
        return {
            "baseline_avg": float(row["baseline_avg"] or 0),
            "baseline_n": int(row["baseline_n"] or 0),
            "recent_avg": float(row["recent_avg"] or 0),
            "recent_n": int(row["recent_n"] or 0),
        }
    except Exception as exc:  # noqa: BLE001 — fail-open
        logger.warning(
            "[account_health] reach-stats query failed for (%r, %r): %s",
            platform,
            niche_id,
            exc,
        )
        return None


def get_signal(platform: str, niche_id: str) -> HealthSignal | None:
    """Return current health signal for (platform, niche), or None.

    PR #570 replaces the PR #566 stub with real implementation:
      * Pull 14d baseline + 48h recent avg reach from analytics
      * If insufficient samples or baseline=0 → None (fail-OPEN)
      * Compute recent/baseline ratio
      * Return CRITICAL (<0.1) / WARNING (<0.5) / HEALTHY (>=0.5)

    Callers should treat None as 'no signal; proceed as healthy' —
    the conservative fail-OPEN choice from the module docstring.
    """
    if not platform or not niche_id:
        return None
    stats = _compute_reach_stats(platform, niche_id)
    if stats is None:
        return None
    # Insufficient data → None (fail-OPEN)
    if stats["baseline_n"] < MIN_BASELINE_SAMPLES:
        logger.debug(
            "[account_health] insufficient baseline (%d < %d) for %r/%r",
            stats["baseline_n"],
            MIN_BASELINE_SAMPLES,
            platform,
            niche_id,
        )
        return None
    if stats["recent_n"] < MIN_RECENT_SAMPLES:
        logger.debug(
            "[account_health] insufficient recent (%d < %d) for %r/%r",
            stats["recent_n"],
            MIN_RECENT_SAMPLES,
            platform,
            niche_id,
        )
        return None
    # baseline=0 makes ratio undefined; treat as no signal
    if stats["baseline_avg"] <= 0:
        return None
    ratio = stats["recent_avg"] / stats["baseline_avg"]
    evidence = {
        "ratio": round(ratio, 3),
        "baseline_avg_reach": round(stats["baseline_avg"], 1),
        "recent_avg_reach": round(stats["recent_avg"], 1),
        "baseline_samples": stats["baseline_n"],
        "recent_samples": stats["recent_n"],
        "critical_threshold": CRITICAL_RATIO_THRESHOLD,
        "warning_threshold": WARNING_RATIO_THRESHOLD,
    }
    # 2026-07-21: guard divide-by-zero when ratio==0 (recent_avg=0 and
    # baseline_avg>0). Prior code raised ZeroDivisionError → policy_gate
    # fell back to warn default_mode (log-only bug, not a blocking gate),
    # but generated confusing "float division by zero" noise on every
    # gate fire. Compute the drop-factor string once for both branches.
    drop_str = f"{(1 / ratio):.1f}x" if ratio > 0 else "∞x"
    if ratio < CRITICAL_RATIO_THRESHOLD:
        return HealthSignal(
            state="critical",
            message=(
                f"Reach dropped {drop_str}: 48h avg "
                f"{stats['recent_avg']:.0f} vs 14d baseline "
                f"{stats['baseline_avg']:.0f}. Probable shadowban."
            ),
            evidence=evidence,
        )
    if ratio < WARNING_RATIO_THRESHOLD:
        return HealthSignal(
            state="warning",
            message=(
                f"Reach dropped {drop_str}: 48h avg "
                f"{stats['recent_avg']:.0f} vs 14d baseline "
                f"{stats['baseline_avg']:.0f}."
            ),
            evidence=evidence,
        )
    return HealthSignal(
        state="healthy",
        message=f"Reach within baseline (ratio {ratio:.2f})",
        evidence=evidence,
    )


def check_account_health(
    blueprint: Mapping,  # unused — health is account-level not blueprint-level
    platform: str,
    niche_id: str,
) -> ComplianceDecision:  # noqa: F821 — forward ref to avoid circular import
    """Registered policy_gate check. Account-level state — blueprint
    parameter is unused (signature matches the CheckFn contract).

    Fires:
      * 'allow' when get_signal returns None (no data, fail-OPEN)
      * 'allow' when state='healthy'
      * 'warn' when state in ('warning', 'critical') — Phase A is
        observation-only; PR after enforcement-toggle lands can
        upgrade 'critical' → 'block' per-niche

    Reasons:
      * 'account_health_warning' or 'account_health_critical'
    """
    # Lazy import to avoid circular import at module load
    from genlab_core.compliance.events import ComplianceDecision

    signal = get_signal(platform, niche_id)
    if signal is None or signal.state == "healthy":
        return ComplianceDecision(decision="allow")
    # PR (2026-06-25, ships after PR #578): opt-in auto-pause when
    # critical (10x reach drop = shadowban signal). Fires BEFORE
    # operator notices the warning — protects the niche from
    # publishing more content that might compound the shadowban.
    # Best-effort: failures don't change the ComplianceDecision
    # return value (which the policy_gate already wires through).
    if signal.state == "critical":
        _maybe_auto_pause_on_critical(niche_id, signal)
    return ComplianceDecision(
        decision="warn",
        reasons=[f"account_health_{signal.state}"],
        metadata={"message": signal.message, **signal.evidence},
    )


def _maybe_auto_pause_on_critical(niche_id: str, signal: HealthSignal) -> None:
    """Opt-in auto-pause when account_health_check returns 'critical'.

    Gated by the env var GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL=1 — when
    unset, this is a no-op + a DEBUG log. Default-off lets the feature
    ship without changing behavior on existing deployments; operators
    flip the flag when they're confident in the false-positive rate.

    When the flag is set:
      * pause() is called with paused_by='system:account_health_check'
        + reason embedding the critical-signal message
      * paused_until is NOW() + 4 hours — gives operator time to
        investigate without locking the niche indefinitely. If the
        shadowban is real, operator can extend OR the next 4h-window
        critical signal will re-pause automatically (UPSERT semantics
        from PR #575).

    Best-effort: every failure path logs at WARNING and returns
    None. The ComplianceDecision returned by check_account_health is
    UNAFFECTED — auto-pause failure doesn't change the 'warn' verdict
    the operator sees.
    """
    if os.environ.get("GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL", "").strip() != "1":
        logger.debug(
            "[account_health] auto-pause disabled (set "
            "GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL=1 to enable); "
            "would have paused %r on critical signal",
            niche_id,
        )
        return
    try:
        from datetime import UTC, datetime, timedelta

        from genlab_core.scheduling.niche_pause import pause

        # 4-hour pause window — gives operator time to investigate
        # without locking the niche indefinitely. If the underlying
        # shadowban persists, the next account_health check will
        # re-fire (UPSERT extends the window).
        paused_until = datetime.now(tz=UTC) + timedelta(hours=4)
        result = pause(
            niche_id=niche_id,
            reason=f"auto_paused_health_critical: {signal.message}",
            paused_until=paused_until,
            paused_by="system:account_health_check",
        )
        if result:
            logger.warning(
                "[account_health] AUTO-PAUSED niche=%r for 4h on critical signal: %s",
                niche_id,
                signal.message,
            )
        else:
            logger.warning(
                "[account_health] auto-pause attempt for %r returned False (see niche_pause logs)",
                niche_id,
            )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "[account_health] auto-pause for %r failed: %s; operator must pause manually",
            niche_id,
            exc,
        )


# Register at module import. Same pattern as PR #568 + #569 — each
# compliance module self-registers its check at load time.
from genlab_core.compliance.policy_gate import register_check  # noqa: E402

register_check("account_health_check", check_account_health)
