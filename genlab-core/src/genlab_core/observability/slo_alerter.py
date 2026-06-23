"""SLO-violation webhook sink.

PR #505 (2026-06-23): closes the operator-notification gap surfaced by
the silent-failure audit. Pre-PR, the only signal an operator had for
an SLO violation (e.g. "zero blueprints produced") was either reading
the latest run_report.json manually OR noticing the dashboard's
status badge on the next visit — typically 30+ minutes after the
violation occurred.

This module fires HTTP POSTs to a webhook URL configured via
``GENLAB_SLO_ALERT_WEBHOOK``. The payload is Slack-compatible
(``{"text": "..."}``) AND carries structured fields for custom
consumers. The webhook is opt-in: when the env var is unset, the
function is a no-op (the cron-driven pipeline runs every few minutes
and adding a webhook everywhere would be noisy without explicit
operator opt-in).

## Failure mode

Any error (network timeout, non-2xx response, bad webhook URL) is
caught and logged at DEBUG. The function NEVER raises into the
caller — SLO alerting failing must not block the pipeline's
``run_report.json`` write or any downstream stage.

## Severity

  * ``critical`` — ``status == "failed"`` (zero blueprints, dark day)
  * ``warning``  — ``status == "partial"`` with at least one SLO
    violation
  * No alert otherwise (clean runs stay silent — operators don't
    need a heartbeat for normal operation)

## Caller responsibility

Caller passes the full ``report`` dict (the one RunReport just
wrote). The alerter extracts what it needs and constructs the
payload. Decoupled from RunReport's internals so a future
refactor of RunReport doesn't break alerting.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

# 5s HTTP timeout — webhook destinations (Slack incoming-webhook,
# generic HTTPS endpoints) typically respond <500ms. 5s leaves
# headroom for the rare slow request without blocking the pipeline.
_HTTP_TIMEOUT_S: float = 5.0

_WEBHOOK_ENV_VAR = "GENLAB_SLO_ALERT_WEBHOOK"


def _severity_for_status(status: str, slo_violations: list[str]) -> str | None:
    """Return ``"critical"`` / ``"warning"`` / None.

    Returns None when no alert should fire — caller short-circuits.
    """
    if not slo_violations:
        # Clean run with no violations — no alert
        return None
    if status == "failed":
        return "critical"
    if status == "partial":
        return "warning"
    # status == "success" + violations is logically impossible (the
    # status logic in run_report.py promotes to "partial" or "failed"
    # when violations exist) — defensively, no alert.
    return None


def _build_payload(report: dict[str, Any], severity: str) -> dict[str, Any]:
    """Construct the webhook payload from a run_report dict."""
    niche_id = report.get("niche_id", "?")
    run_id = report.get("run_id", "?")
    status = report.get("status", "?")
    metrics = report.get("metrics", {}) or {}
    blueprints_count = metrics.get("blueprints_count", "?")
    slo_violations = report.get("slo_violations", []) or []

    # Headline string — Slack-friendly. First violation gives the
    # operator the most relevant signal at a glance; remaining
    # violations go in the structured field below.
    headline_violation = slo_violations[0] if slo_violations else "(no violation text)"
    text = (
        f"[{severity}] {niche_id} run {run_id} → status={status}, "
        f"blueprints_count={blueprints_count} | {headline_violation}"
    )

    return {
        # Slack incoming-webhook compat: ``text`` is the message body.
        "text": text,
        # Structured fields for custom consumers (Discord, internal
        # alerting routers, custom dashboards).
        "severity": severity,
        "niche_id": niche_id,
        "run_id": run_id,
        "status": status,
        "blueprints_count": blueprints_count,
        "slo_violations": slo_violations,
        # PR #504 surfaces these — when present they're the most useful
        # actionable signal for a zero-blueprint run.
        "bottleneck_stage": report.get("bottleneck_stage"),
        "bottleneck_reason": report.get("bottleneck_reason"),
    }


def fire_slo_alert(report: dict[str, Any]) -> bool:
    """Post the SLO alert webhook if configured. Returns True on POST sent.

    Returns False on:
      - No ``GENLAB_SLO_ALERT_WEBHOOK`` configured (opt-in)
      - Severity check returned None (no violations to alert on)
      - Network failure (caught + logged at DEBUG)
      - Non-2xx HTTP response

    NEVER raises — SLO alerting failing must not block pipeline writes.
    """
    webhook_url = os.environ.get(_WEBHOOK_ENV_VAR, "").strip()
    if not webhook_url:
        return False

    status = str(report.get("status", ""))
    slo_violations = report.get("slo_violations") or []
    severity = _severity_for_status(status, slo_violations)
    if severity is None:
        return False

    payload = _build_payload(report, severity)

    try:
        resp = requests.post(webhook_url, json=payload, timeout=_HTTP_TIMEOUT_S)
    except Exception as exc:
        logger.debug("[slo_alerter] webhook POST failed (%s): %s", _WEBHOOK_ENV_VAR, exc)
        return False

    if not (200 <= resp.status_code < 300):
        logger.debug(
            "[slo_alerter] webhook returned non-2xx %d for niche=%s run=%s",
            resp.status_code,
            report.get("niche_id", "?"),
            report.get("run_id", "?"),
        )
        return False

    logger.info(
        "[slo_alerter] alert fired: severity=%s niche=%s run=%s",
        severity,
        report.get("niche_id", "?"),
        report.get("run_id", "?"),
    )
    return True
