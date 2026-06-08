"""Cross-run retry pass for failed-platform recovery.

The publisher's "retry pass" runs at the end of every invocation
(including the no-fresh-content path and the explicit
``retry_only=True`` mode) and walks the last 50 PUBLISHED blueprints
of the niche, retrying any platforms that earlier runs marked FAILED.

The pass is deliberately conservative — it skips:

* Failures whose ``error_class`` is non-retryable
  (``CREDENTIAL`` / ``CONTENT`` / ``PERMANENT``) — those need human
  attention, not a blind retry.
* Failures that have already burned through ``MAX_RETRY_ATTEMPTS``
  attempts — bounded retries keep a permanently-broken platform from
  spinning the orchestrator forever.
* **R-21** Ambiguous failures (timeouts, broken pipes, container-
  expired errors) where the post may have actually landed. Blindly
  retrying these risks a duplicate post on the live channel; they
  need a "did it land" check or human review, not auto-retry.
* Failures whose ``next_retry_after`` backoff hasn't elapsed yet.
* Platforms at daily-cap — the cap is a hard limit, retries don't
  bypass it.
* Blueprints whose media files have been cleaned off disk (the
  retry would 404 on missing media).

Lives in its own module so the multi-platform publisher orchestrator
(:mod:`genlab_core.publishing.publish_all_platforms`) stays focused
on first-pass publish flow. Extracted in the refactor-#9 decomposition
(PR 6c/N). Re-exported from the orchestrator with the underscore-
prefixed ``_run_retry_pass`` alias the orchestrator's call sites use.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from genlab_core.platforms.registry import get_client
from genlab_core.publishing.analytics_recorder import record_publish
from genlab_core.publishing.error_classifier import (
    classify,
    is_ambiguous_failure,
    retry_delay_seconds,
    should_retry,
)
from genlab_core.publishing.payload_builder import build_payload
from genlab_core.publishing.platform_status import (
    terminal_failed_platforms,
    to_registry_id,
)
from genlab_core.publishing.preflight import resolve_client_kwargs

logger = logging.getLogger(__name__)

# Cap on attempts per-platform — past this, even a "TRANSIENT" failure is
# treated as terminal (matches the cap in publish_all_platforms's first-pass
# retry budget).
MAX_RETRY_ATTEMPTS: int = 3

# How many recent PUBLISHED blueprints to scan per niche per run.
RETRY_PASS_BATCH_LIMIT: int = 50


def run_retry_pass(niche_id: str, backlog_client: Any, daily_cap: Any | None) -> None:
    """Retry failed platforms on recently-PUBLISHED blueprints.

    Strictly non-fatal: any exception is logged and swallowed — the
    retry pass MUST NOT block the main publish flow.
    """
    try:
        published_bps = backlog_client.get_blueprints_by_status(
            "PUBLISHED",
            niche_id=niche_id,
            max_records=RETRY_PASS_BATCH_LIMIT,
        )
        for bp in published_bps:
            _process_blueprint(bp, niche_id, backlog_client, daily_cap)
    except Exception as e:
        logger.debug("[publish] Retry pass failed: %s", e)


def _process_blueprint(bp: dict, niche_id: str, backlog_client: Any, daily_cap: Any | None) -> None:
    """Inspect one blueprint, select retry-eligible failed platforms, retry
    them, and persist the updated platform_publish_status.

    Per-blueprint failures are caught locally so one bad blueprint doesn't
    abort the whole pass.
    """
    fields = bp.get("fields", bp)
    pps = _parse_platform_publish_status(fields.get("platform_publish_status", "{}"))
    if pps is None:
        return  # Malformed JSON; skip.

    retry_platforms = _eligible_retry_platforms(pps, daily_cap)
    if not retry_platforms:
        return

    if _media_files_missing(fields):
        logger.info("[publish] Skipping retry for %s — media files deleted", bp["id"][:8])
        return

    logger.info(
        "[publish] Retrying %d failed platform(s) for blueprint %s: %s",
        len(retry_platforms),
        bp["id"][:8],
        retry_platforms,
    )

    try:
        record_id = bp["id"]
        for plat in retry_platforms:
            _retry_one_platform(
                plat=plat,
                fields=fields,
                pps=pps,
                niche_id=niche_id,
                record_id=record_id,
                backlog_client=backlog_client,
            )

        # Persist the updated status map after all platforms have been retried.
        backlog_client.blueprints.update(
            record_id,
            {"platform_publish_status": json.dumps(pps)},
        )

        # If any platforms remain terminally failed after retries, surface to
        # the dashboard. Without this, partial-publish failures are only
        # visible by hand-inspecting platform_publish_status.
        _emit_terminal_failure_event_if_any(bp, fields, pps, niche_id)
    except Exception as e:
        logger.warning("[publish] Retry processing failed for blueprint %s: %s", bp["id"][:8], e)


def _parse_platform_publish_status(raw: Any) -> dict | None:
    """Decode ``platform_publish_status`` to a dict. Returns ``None`` on
    malformed JSON so the caller skips the blueprint."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return raw or {}


def _eligible_retry_platforms(pps: dict, daily_cap: Any | None) -> list[str]:
    """Apply the conservative retry-eligibility filter to ``pps``.

    See module docstring for the exhaustive skip list. Returns a list
    of platform names that ARE eligible for retry right now.
    """
    eligible: list[str] = []
    for plat, status_data in pps.items():
        if not (isinstance(status_data, dict) and status_data.get("status") == "FAILED"):
            continue

        error_class = status_data.get("error_class", "TRANSIENT")
        attempts = status_data.get("attempts", 0)
        if not should_retry(error_class) or attempts >= MAX_RETRY_ATTEMPTS:
            continue

        # R-21: never auto-re-publish a failure that may have actually landed
        # (timeout / broken pipe / container-expired). Duplicate-post risk.
        if status_data.get("ambiguous") or is_ambiguous_failure(status_data.get("last_error", "")):
            logger.warning(
                "[publish] Skipping cross-run retry of %s — ambiguous failure may have landed",
                plat,
            )
            continue

        if _retry_not_yet_due(status_data):
            continue

        if daily_cap and not daily_cap.can_publish(plat):
            logger.info("[publish] Retry skipped for %s — daily cap reached", plat)
            continue

        eligible.append(plat)
    return eligible


def _retry_not_yet_due(status_data: dict) -> bool:
    """Return True if ``next_retry_after`` is in the future."""
    next_retry = status_data.get("next_retry_after", "")
    if not next_retry:
        return False
    try:
        retry_dt = datetime.fromisoformat(next_retry)
    except (ValueError, TypeError):
        return False
    if retry_dt.tzinfo is None:
        retry_dt = retry_dt.replace(tzinfo=UTC)
    return retry_dt > datetime.now(UTC)


def _media_files_missing(fields: dict) -> bool:
    """Return True if ``visual_paths`` has entries but none exist on disk —
    the retry would 404 on missing media."""
    vp_raw = fields.get("visual_paths", "[]")
    try:
        vp_list = json.loads(vp_raw) if isinstance(vp_raw, str) else (vp_raw or [])
    except (json.JSONDecodeError, TypeError):
        vp_list = []
    if not vp_list:
        return False
    return not any(Path(p).exists() for p in vp_list if p)


def _retry_one_platform(
    *,
    plat: str,
    fields: dict,
    pps: dict,
    niche_id: str,
    record_id: str,
    backlog_client: Any,
) -> None:
    """Retry a single platform. Mutates ``pps`` in place with the result.

    Failures here are logged and swallowed so the next platform in the
    retry loop still gets its chance.
    """
    registry_id = to_registry_id(plat)
    kwargs = resolve_client_kwargs(registry_id, niche_id)
    if not kwargs:
        return

    try:
        payload = build_payload(fields, plat)
        client_instance = get_client(registry_id, **kwargs)
        result = client_instance.publish(payload)

        if result.success:
            pps[plat] = "PUBLISHED"
            logger.info(
                "[publish] Retry SUCCESS: %s/%s post_id=%s",
                niche_id,
                plat,
                result.post_id,
            )
        else:
            ec = classify(result.error, plat)
            prev = pps[plat] if isinstance(pps[plat], dict) else {}
            prev_attempts = prev.get("attempts", 0) if isinstance(prev, dict) else 0
            attempts = prev_attempts + 1
            delay = retry_delay_seconds(ec, attempts)
            pps[plat] = {
                "status": "FAILED",
                "attempts": attempts,
                "last_error": result.error[:200],
                "error_class": ec,
                "next_retry_after": (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
            }
            logger.warning(
                "[publish] Retry FAILED: %s/%s (%s): %s",
                niche_id,
                plat,
                ec,
                result.error[:100],
            )

        record_publish(
            client=backlog_client,
            niche_id=niche_id,
            platform=plat,
            status="SUCCESS" if result.success else "FAILED",
            post_url=result.post_url,
            blueprint_id=record_id,
            error_message=result.error if not result.success else "",
        )
    except Exception as e:
        logger.warning("[publish] Retry exception for %s/%s: %s", niche_id, plat, e)


def _emit_terminal_failure_event_if_any(bp: dict, fields: dict, pps: dict, niche_id: str) -> None:
    """Push a dashboard ``publish_partial`` event if any platforms remain
    terminally failed after this pass. Non-fatal — a missing observability
    module never blocks the retry pass."""
    terminal_failed = terminal_failed_platforms(pps)
    if not terminal_failed:
        return
    try:
        from genlab_core.observability.dashboard_events import push_event

        hook_text = (fields.get("hook_text") or fields.get("hook") or fields.get("title") or "")[
            :50
        ]
        ok_plats = [
            p
            for p, s in pps.items()
            if s == "PUBLISHED" or (isinstance(s, dict) and s.get("status") == "PUBLISHED")
        ]
        failed_summary = ", ".join(
            f"{p}({d.get('error_class', '?')},{d.get('attempts', '?')}x)"
            for p, d in terminal_failed.items()
        )
        push_event(
            "publish_partial",
            f"Retries exhausted: {hook_text}",
            f"OK: {', '.join(ok_plats) or 'none'}; permanent: {failed_summary}",
            entity_id=bp["id"],
            entity_type="blueprint",
            niche_id=niche_id,
        )
    except Exception:
        pass  # non-fatal
