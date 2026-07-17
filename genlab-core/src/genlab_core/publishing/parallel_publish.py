"""Per-blueprint parallel publish dispatcher.

Single public entry point :func:`execute_parallel_publish` runs the
per-platform publish fan-out for a single blueprint:

  1. Build each platform's payload + client kwargs.
  2. Dispatch to each platform via ``ThreadPoolExecutor`` so they ship
     concurrently (each ``client.publish()`` is network-bound).
  3. As each future settles, persist the per-platform PUBLISHED marker
     immediately (so a crash before the orchestrator's final update
     doesn't strand a successful post into the double-publish trap),
     record per-platform analytics, and on success kick off the
     non-blocking affiliate-comment reply.
  4. Return a :class:`ParallelPublishOutcome` carrying the final
     per-platform status dict, whether any platform succeeded this
     run, and the per-platform post_id dict for the PendingFeedback
     registration step.

Lives in its own module so the orchestrator
(:mod:`genlab_core.publishing.publish_all_platforms`) doesn't have to
hold the per-platform-publish state machine inline. Extracted in the
refactor-#9 decomposition (PR 6b/N).

Cleanup it accomplishes
-----------------------
The pre-extraction orchestrator built up ``platform_status`` and
``any_success`` inline, then re-iterated ``futures.result()`` later in
the PendingFeedback step to fetch per-platform post_ids — a redundant
second iteration (the futures were already done; ``.result()``
returned the cached value). The :class:`ParallelPublishOutcome` carries
``successful_post_ids`` so the caller doesn't need futures at all
post-dispatch.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from genlab_core.platforms.models import PublishResult
from genlab_core.platforms.registry import get_client
from genlab_core.publishing.affiliate_reply import post_affiliate_reply
from genlab_core.publishing.analytics_recorder import record_publish
from genlab_core.publishing.error_classifier import classify, is_ambiguous_failure
from genlab_core.publishing.payload_builder import build_payload
from genlab_core.publishing.platform_status import to_registry_id
from genlab_core.publishing.preflight import resolve_client_kwargs

logger = logging.getLogger(__name__)

# Per-platform publish wall-clock cap.
#
# 2026-07-17 (audit round 4): raised 600 → 900 to give IG's
# _TOTAL_PUBLISH_BUDGET_SECONDS=420 (see instagram.py:46) a full 480s
# slippage margin. Prior state: IG budget 540 + executor timeout 600
# = 60s margin. Meta's async container-polling occasionally exceeds
# 540s (2207077 slow-fetch → longer retry cycles) → hits 600s
# executor kill → TimeoutError → marked ambiguous → permanently
# skipped by retry_pass.py:177. 480s margin drops that hazard to
# near-zero for the observed distribution.
DEFAULT_PUBLISH_TIMEOUT_SECONDS: int = 900


@dataclass
class ParallelPublishOutcome:
    """The state the orchestrator needs after the parallel fan-out.

    ``platform_status`` is the per-platform map persisted to the
    blueprint's ``platform_publish_status`` column. ``any_success``
    tracks only THIS run's successes (so a still-partial blueprint
    correctly returns to ``VISUAL_READY`` and retries just the failed
    platform next time). ``successful_post_ids`` lets the caller
    register PendingFeedback tasks without re-iterating futures.
    """

    platform_status: dict[str, Any] = field(default_factory=dict)
    any_success: bool = False
    successful_post_ids: dict[str, str] = field(default_factory=dict)


def execute_parallel_publish(
    *,
    platforms_to_publish: list[str],
    prior_published: set[str],
    niche_id: str,
    fields: dict[str, Any],
    record_id: str,
    candidate_id: str,
    backlog_client: Any,
    daily_cap: Any | None,
    timeout_seconds: int = DEFAULT_PUBLISH_TIMEOUT_SECONDS,
) -> ParallelPublishOutcome:
    """Dispatch + collect per-platform publishes for a single blueprint.

    Pre-seeds the outcome's ``platform_status`` with the platforms
    already PUBLISHED in a prior run (R-29) so the persisted state keeps
    the full picture across re-runs. The fan-out targets only
    ``platforms_to_publish`` — the caller filters out already-published
    platforms before invoking this.
    """
    # R-29: pre-seed with prior successes so the persisted state always
    # carries the full per-platform picture. ``any_success`` only tracks
    # *this run*, so a still-partial blueprint returns to VISUAL_READY and
    # only the failed platform is retried next time.
    outcome = ParallelPublishOutcome(
        platform_status={p: "PUBLISHED" for p in prior_published},
    )

    def _publish_one(platform: str) -> tuple[str, PublishResult]:
        """Single-platform publish, fully self-contained so it can be safely
        run inside the ThreadPoolExecutor."""
        registry_id = to_registry_id(platform)
        try:
            kwargs = resolve_client_kwargs(registry_id, niche_id)
            if kwargs is None:
                return platform, PublishResult(
                    platform=registry_id,
                    success=False,
                    error=f"No {registry_id} credentials for niche '{niche_id}'",
                )
            payload = build_payload(fields, platform)
            client = get_client(registry_id, **kwargs)
            result = client.publish(payload)
            return platform, result
        except Exception as exc:
            return platform, PublishResult(
                platform=registry_id,
                success=False,
                error=str(exc),
            )

    with ThreadPoolExecutor(max_workers=len(platforms_to_publish)) as pool:
        futures = {pool.submit(_publish_one, p): p for p in platforms_to_publish}
        for future in futures:
            try:
                platform, result = future.result(timeout=timeout_seconds)
            except TimeoutError:
                platform = futures[future]
                result = PublishResult(
                    platform=to_registry_id(platform),
                    success=False,
                    error=f"Publish timed out after {timeout_seconds}s for {platform}",
                )
            except Exception as exc:
                # 2026-07-17 (audit round 4): capture type + traceback in
                # error string. Prior state: `f"Publish error: {exc}"` where
                # `str(exc)` is often empty for wrapped urllib3 / requests
                # exceptions → analytics_recorder writes empty error_message
                # → 24/24 IG failures over 30 days had empty error strings
                # and no way to diagnose. Full type + traceback tail
                # guarantees non-empty diagnostic for every failure.
                import traceback as _tb

                platform = futures[future]
                tb_tail = _tb.format_exc()[-400:]
                result = PublishResult(
                    platform=to_registry_id(platform),
                    success=False,
                    error=(
                        f"Publish error [{type(exc).__name__}]: {exc!r} | "
                        f"tb_tail: {tb_tail}"
                    ),
                )

            error_class = ""
            if result.success:
                _on_success(
                    outcome=outcome,
                    platform=platform,
                    result=result,
                    record_id=record_id,
                    fields=fields,
                    niche_id=niche_id,
                    backlog_client=backlog_client,
                    daily_cap=daily_cap,
                )
            else:
                error_class = classify(result.error, platform)
                _on_failure(
                    outcome=outcome,
                    platform=platform,
                    result=result,
                    error_class=error_class,
                )

            # Record to Publishing_Analytics. SKIPPED for both CREDENTIAL and
            # QUOTA failures — neither represents a real platform outage, and
            # the loud "FAILED" signal would drown out genuine breakage in the
            # dashboard.
            #
            #   CREDENTIAL — token expired / revoked. Not retryable in-run.
            #     Operator must rotate the token; until then, every attempt
            #     would write the same FAILED row, polluting the rate.
            #
            #   QUOTA — deliberate budget protection. The YouTube quota gate
            #     at ``platforms/youtube.py:399-416`` returns success=False
            #     when ``can_afford("upload")`` is False — that's a restraint
            #     mechanism, not an outage. Counting it as FAILED inflated
            #     YouTube failures by ~50% in the prod 60-day window (13 of
            #     26 "failures" were quota-guards; see PR D 2026-06-27
            #     investigation).
            #
            # Both classes are still surfaced in their platform_status entry
            # as FAILED so the orchestrator's retry / pause logic can act on
            # them; only the analytics row is downshifted to SKIPPED.
            if result.success:
                analytics_status = "SUCCESS"
            elif error_class in ("CREDENTIAL", "QUOTA", "MISSING_RENDER"):
                # MISSING_RENDER added 2026-07-01 after the disk-cleanup
                # cascade incident: blueprints whose visual files got
                # nuked kept failing the publisher window at 06:30 UTC AND
                # again at the 10:30 UTC retry, producing 6 FAILED rows
                # per affected niche per day. Downshifting to SKIPPED
                # (like CREDENTIAL/QUOTA) surfaces the real class of
                # issue (a data-side render problem, not a platform
                # outage) in the health dashboard without polluting the
                # platform-failure rate.
                analytics_status = "SKIPPED"
            else:
                analytics_status = "FAILED"
            record_publish(
                client=backlog_client,
                niche_id=niche_id,
                platform=platform,
                status=analytics_status,
                post_url=result.post_url,
                blueprint_id=record_id,
                candidate_id=candidate_id,
                error_message=result.error if not result.success else "",
                # 2026-07-14: pass native platform ID (numeric for IG +
                # Threads, matches pending_feedback.post_id shape).
                # Closes multi-identifier-drift class-of-bug that broke
                # reward-loop JOINs for those platforms.
                post_id_override=result.post_id if result.success else "",
            )

    return outcome


def _on_success(
    *,
    outcome: ParallelPublishOutcome,
    platform: str,
    result: PublishResult,
    record_id: str,
    fields: dict[str, Any],
    niche_id: str,
    backlog_client: Any,
    daily_cap: Any | None,
) -> None:
    """Mutate ``outcome`` for a successful publish, then run the
    post-success side-effects: daily cap accounting, affiliate reply,
    immediate per-platform state persistence (so a crash before the
    final blueprint update doesn't strand the success into the
    double-publish trap)."""
    outcome.any_success = True
    outcome.platform_status[platform] = "PUBLISHED"
    if result.post_id:
        outcome.successful_post_ids[platform] = result.post_id
    if daily_cap:
        daily_cap.record_publish(platform)
    logger.info(
        "[publish] %s: SUCCESS post_id=%s url=%s",
        platform,
        result.post_id,
        result.post_url,
    )
    # Post affiliate link as first reply/comment (non-blocking).
    post_affiliate_reply(platform, result.post_id, fields, niche_id)
    # 2026-06-23 — cross-platform synergy (PR S). YouTube → X teaser
    # is the initial route. Other routes (IG → X, FB → X) are
    # intentional follow-ups pending stable public post URLs from
    # the Meta API. Opt-in per niche via publishing.yaml; default off.
    # Non-blocking — fails the same way as affiliate_reply does.
    try:
        from genlab_core.publishing.cross_post_teaser import post_cross_teaser

        post_cross_teaser(platform, result.post_id, result.post_url, fields, niche_id)
    except Exception as exc:  # noqa: BLE001 — cross-teaser failure must never block publish
        logger.warning(
            "[publish] cross_teaser invocation failed for %s (non-blocking): %s",
            platform,
            exc,
        )
    # Persist the per-platform state NOW so a crash between here and the
    # orchestrator's final update doesn't lose the success and re-post on
    # the next run.
    try:
        backlog_client.blueprints.update(
            record_id,
            {"platform_publish_status": json.dumps(outcome.platform_status)},
        )
    except Exception as exc:
        # Best-effort: the orchestrator's final update will re-try. Log
        # at WARN so if that update also fails and we crash mid-publish,
        # the debug trail makes the double-post risk visible.
        logger.warning(
            "[publish] Mid-publish state persistence failed for %s "
            "(will retry at final update): %s",
            platform,
            exc,
        )


def _on_failure(
    *,
    outcome: ParallelPublishOutcome,
    platform: str,
    result: PublishResult,
    error_class: str,
) -> None:
    """Mutate ``outcome`` for a failed publish: increment attempts,
    record the error class, flag ambiguous failures (R-21)."""
    attempt_data = outcome.platform_status.get(platform, {})
    if isinstance(attempt_data, dict):
        prev_attempts = attempt_data.get("attempts", 0)
    else:
        prev_attempts = 0
    outcome.platform_status[platform] = {
        "status": "FAILED",
        "attempts": prev_attempts + 1,
        "last_error": result.error[:200],
        "error_class": error_class,
        # R-21: tag failures that may have actually landed so the
        # cross-run retry pass won't blindly re-publish them.
        "ambiguous": is_ambiguous_failure(result.error),
    }
    logger.error("[publish] %s: FAILED error=%s", platform, result.error)
