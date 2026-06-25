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


def send_compliance_digest(
    by_niche: dict[str, dict],
    *,
    window_days: int = 1,
) -> bool:
    """Best-effort Slack POST summarising compliance activity.

    Companion to ``notify_compliance_block`` for the proactive
    digest channel. While the block notifier fires reactively on
    enforcement events, the digest fires on a schedule (typically
    a daily systemd timer) and gives operators the at-a-glance
    summary of the prior window.

    Reuses ``GENLAB_COMPLIANCE_SLACK_WEBHOOK`` from PR #584 —
    same env, same webhook, different message tone. Operators who
    activate the webhook get both surfaces 'free' once the env
    flag is set.

    ## When to send

    Always-send semantics. Even a 'zero events' digest is useful
    because it confirms the digest job is alive — an operator
    seeing no daily summary doesn't know whether to interpret it
    as 'all clean' or 'monitoring is broken'. A daily ✅ message
    resolves the ambiguity at low Slack-volume cost (1/day).

    ## Return value

      * True — webhook POST succeeded (HTTP 2xx)
      * False — env unset, network error, non-2xx, library exception

    Same fail-OPEN contract as notify_compliance_block. NEVER raises.

    Args:
      by_niche — output of compliance.events.stats_by_niche().
                  Empty dict is a valid input (sends 'all clean').
      window_days — passed-in for the message subject line; should
                    match what was passed to stats_by_niche().
    """
    webhook = os.environ.get(_ENV_WEBHOOK, "").strip()
    if not webhook:
        logger.debug(
            "[compliance_slack] %s unset; digest skipped — operator must set env to activate",
            _ENV_WEBHOOK,
        )
        return False

    payload = _build_digest_payload(by_niche, window_days=window_days)

    try:
        import requests

        resp = requests.post(webhook, json=payload, timeout=_TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — fail-open per contract
        logger.warning("[compliance_slack] digest POST failed: %s", exc)
        return False

    if 200 <= resp.status_code < 300:
        n_niches = len(by_niche)
        total_warns = sum(b.get("warns", 0) for b in by_niche.values())
        total_blocks = sum(b.get("blocks", 0) for b in by_niche.values())
        logger.info(
            "[compliance_slack] digest sent — %d niche(s) with activity, %d warn, %d block",
            n_niches,
            total_warns,
            total_blocks,
        )
        return True

    logger.warning(
        "[compliance_slack] digest webhook returned HTTP %d — check %s",
        resp.status_code,
        _ENV_WEBHOOK,
    )
    return False


def _build_digest_payload(
    by_niche: dict[str, dict],
    *,
    window_days: int,
) -> dict:
    """Compose the daily-digest Slack message.

    Two visual modes:
      * Empty by_niche → ✅ 'all clean' single-line headline
      * Non-empty     → 📊 header + per-niche summary lines

    Per-niche line: ``• gaming   5w / 1b · top: spam_pattern_detected``
    """
    total_warns = sum(b.get("warns", 0) for b in by_niche.values())
    total_blocks = sum(b.get("blocks", 0) for b in by_niche.values())
    n_active = len(by_niche)

    window_label = f"{window_days}d" if window_days != 1 else "24h"

    if n_active == 0:
        # All-clean digest — small message, positive confirmation
        # that monitoring is alive
        fallback = f"✅ Compliance digest ({window_label}): all niches clean"
        return {
            "text": fallback,
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"✅ *Compliance digest* ({window_label}): "
                            "all niches clean — no warns, no blocks."
                        ),
                    },
                }
            ],
        }

    # Active digest — header + summary + per-niche lines
    fallback = (
        f"📊 Compliance digest ({window_label}): "
        f"{n_active} niche(s) active · {total_warns}w / {total_blocks}b total"
    )

    # Sort niches by (blocks desc, warns desc) so operator's eye
    # lands on the highest-attention niche first. Blocks weighted
    # above warns — enforcement events are more actionable.
    sorted_items = sorted(
        by_niche.items(),
        key=lambda kv: (kv[1].get("blocks", 0), kv[1].get("warns", 0)),
        reverse=True,
    )

    lines: list[str] = []
    for niche_id, bucket in sorted_items:
        warns = bucket.get("warns", 0)
        blocks = bucket.get("blocks", 0)
        top = bucket.get("top_event_type")
        if blocks > 0:
            counts = f"{warns}w / {blocks}b"
        else:
            counts = f"{warns}w"
        if top:
            lines.append(f"• `{niche_id}` — {counts} · top: `{top}`")
        else:
            lines.append(f"• `{niche_id}` — {counts}")

    summary_md = (
        f"*{n_active} niche{'s' if n_active != 1 else ''} active* · "
        f"{total_warns} warn{'s' if total_warns != 1 else ''} · "
        f"{total_blocks} block{'s' if total_blocks != 1 else ''}\n"
    ) + "\n".join(lines)

    return {
        "text": fallback,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"📊 Compliance digest — last {window_label}",
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": summary_md},
            },
        ],
    }
