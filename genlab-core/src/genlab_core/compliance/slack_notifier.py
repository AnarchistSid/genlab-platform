"""Slack webhook notifier for compliance block decisions.

PR after #583 (2026-06-25). Foundation for Phase B enforcement
alerting. When ``log_compliance_event`` records a ``decision='block'``,
this module fires an opt-in Slack POST so the operator knows
enforcement just halted a publish.

## Why opt-in

  * Phase A is observation-only — there are no real blocks today.
    Without an env-flag gate, this module would still attempt POSTs
    on every test run that exercises the code path.
  * When Phase B flips on per (niche, check), operators set the
    webhook URL once and alerts start flowing.
  * Same opt-in pattern as PR #579's
    ``GENLAB_AUTO_PAUSE_ON_HEALTH_CRITICAL`` — default-off ships
    safely; operator activates when ready.

## Public surface

  notify_compliance_block(
      niche_id, event_type, *,
      blueprint_id=None, platform=None,
      reasons=None, metadata=None,
  ) -> bool

  Returns True when a webhook POST succeeded (HTTP 2xx). Returns
  False when:
    * Env flag unset (no-op — default-off semantics)
    * Webhook POST returned non-2xx
    * Network error / timeout
    * requests library raised

## Fail-OPEN throughout

Every failure path returns False after logging WARNING. The function
NEVER raises — a Slack outage must not break the compliance write
chain. ``log_compliance_event`` checks the return value but does not
gate its own success on it.

## Strict timeout

2.0s. Slack's edge usually responds in <500ms, so a 2s ceiling is
defensive but doesn't add meaningful latency to the DB write path.
Operators see slow compliance writes as a worse outcome than
delayed Slack notifications — when Phase B blocks fire, the audit
record is the authoritative state; the Slack message is convenience.
"""

from __future__ import annotations

import logging
import os
import uuid

logger = logging.getLogger(__name__)

_ENV_WEBHOOK = "GENLAB_COMPLIANCE_SLACK_WEBHOOK"
_TIMEOUT_SECONDS = 2.0


def notify_compliance_block(
    niche_id: str,
    event_type: str,
    *,
    blueprint_id: uuid.UUID | str | None = None,
    platform: str | None = None,
    reasons: list[str] | None = None,
    metadata: dict | None = None,
) -> bool:
    """Best-effort Slack POST for a compliance block decision.

    Returns True on successful HTTP 2xx response, False on every
    other path (env unset, network error, non-2xx, library exception).
    NEVER raises.
    """
    webhook = os.environ.get(_ENV_WEBHOOK, "").strip()
    if not webhook:
        logger.debug(
            "[compliance_slack] %s unset; block alert for niche=%r event=%r "
            "would have been sent — set the env to activate",
            _ENV_WEBHOOK,
            niche_id,
            event_type,
        )
        return False

    payload = _build_slack_payload(
        niche_id=niche_id,
        event_type=event_type,
        blueprint_id=blueprint_id,
        platform=platform,
        reasons=reasons,
        metadata=metadata,
    )

    try:
        # Lazy import keeps the hot path fast — `requests` is heavy
        # to import and the no-env-flag path above short-circuits
        # before we ever need it.
        import requests

        resp = requests.post(
            webhook,
            json=payload,
            timeout=_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 — fail-open per contract
        logger.warning(
            "[compliance_slack] POST failed for niche=%r event=%r: %s",
            niche_id,
            event_type,
            exc,
        )
        return False

    if 200 <= resp.status_code < 300:
        logger.info(
            "[compliance_slack] block alert sent — niche=%r event=%r",
            niche_id,
            event_type,
        )
        return True

    # Slack returns 400 on malformed webhook URL, 404 on revoked.
    # Surface the status so operators can fix the URL without
    # tail -f-ing logs at full verbosity.
    logger.warning(
        "[compliance_slack] webhook returned HTTP %d for niche=%r event=%r — "
        "check that %s is valid + not revoked",
        resp.status_code,
        niche_id,
        event_type,
        _ENV_WEBHOOK,
    )
    return False


def _build_slack_payload(
    *,
    niche_id: str,
    event_type: str,
    blueprint_id: uuid.UUID | str | None,
    platform: str | None,
    reasons: list[str] | None,
    metadata: dict | None,
) -> dict:
    """Compose the Slack message body. Uses ``blocks`` for rich
    formatting + ``text`` as the notification-preview fallback.

    Kept as a pure function so tests can pin the shape without
    needing to mock the HTTP layer.
    """
    reasons_list = reasons or []
    reasons_str = ", ".join(reasons_list) if reasons_list else "_(no reasons)_"

    # Notification fallback (mobile preview, screen readers)
    fallback = (
        f"🚨 Compliance block: {niche_id} / {event_type} — "
        f"{', '.join(reasons_list) if reasons_list else 'no reasons'}"
    )

    fields: list[dict] = [
        {"type": "mrkdwn", "text": f"*Niche:*\n`{niche_id}`"},
        {"type": "mrkdwn", "text": f"*Event:*\n`{event_type}`"},
    ]
    if platform:
        fields.append({"type": "mrkdwn", "text": f"*Platform:*\n`{platform}`"})
    if blueprint_id:
        fields.append({"type": "mrkdwn", "text": f"*Blueprint:*\n`{str(blueprint_id)}`"})

    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 Compliance block fired"},
        },
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reasons:*\n{reasons_str}"},
        },
    ]

    # Metadata only included when present + non-empty, to avoid
    # an empty `{}` section taking screen real-estate
    if metadata:
        import json

        # Truncate long metadata blobs so the message fits Slack's
        # 3000-char-per-block limit. Pretty-print for the operator.
        pretty = json.dumps(metadata, indent=2, default=str, sort_keys=True)
        if len(pretty) > 1500:
            pretty = pretty[:1500] + "\n…(truncated)"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Metadata:*\n```{pretty}```",
                },
            }
        )

    return {"text": fallback, "blocks": blocks}
