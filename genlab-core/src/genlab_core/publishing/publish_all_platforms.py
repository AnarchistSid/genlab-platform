#!/usr/bin/env python3
"""Canonical multi-platform publisher for all Gen Lab niches.

One ~300 LOC orchestrator that delegates to existing infrastructure:
  - PublishGatekeeper: 7 composable gates (approval, schedule, score, media, cap)
  - get_client(): lazy platform client registry
  - DailyCapEnforcer: per-niche daily post caps
  - niche_credentials: per-niche token resolution
  - BacklogClient: SharePoint data access

Usage:
    python -m genlab_core.publishing.publish_all_platforms --niche gaming
    python -m genlab_core.publishing.publish_all_platforms --niche ai_creators

Exit codes:
    0 = success (>= 1 platform published)
    1 = no eligible blueprints
    2 = all platforms failed
    3 = daily cap reached for all platforms
    4 = lock held by another process
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from genlab_core.platforms.gatekeeper import PublishGatekeeper
from genlab_core.platforms.models import (
    PublishResult,
)
from genlab_core.platforms.registry import get_client
from genlab_core.publishing.affiliate_reply import (
    post_affiliate_reply as _post_affiliate_reply,
)
from genlab_core.publishing.analytics_recorder import record_publish
from genlab_core.publishing.error_classifier import (
    classify,
    is_ambiguous_failure,
    retry_delay_seconds,
    should_retry,
)

# Re-exports from sibling modules so existing import paths (notably the
# test suite + any future direct caller) keep working byte-stable. The
# actual implementations live in:
#   - ``platform_status`` — already_published / terminal-failure helpers
#   - ``niche_validation`` — VALID_NICHE_IDS + normalize/validate
#   - ``pid_lock``         — PidLock class
#   - ``transcode``        — per-platform ffmpeg variant + encode semaphore
# Refactor-#9 PR 1/N + PR 2/N extracted them; see those modules for
# docstrings + tests.
from genlab_core.publishing.niche_validation import (
    normalize_niche as _normalize_niche,
)
from genlab_core.publishing.niche_validation import (
    validate_niche as _validate_niche,
)
from genlab_core.publishing.payload_builder import build_payload
from genlab_core.publishing.pid_lock import PidLock
from genlab_core.publishing.platform_status import (
    already_published_platforms as _already_published_platforms,
)
from genlab_core.publishing.platform_status import (
    terminal_failed_platforms as _terminal_failed_platforms,
)
from genlab_core.publishing.platform_status import (
    to_registry_id as _to_registry_id,
)
from genlab_core.publishing.preflight import (
    preflight_token_refresh as _preflight_token_refresh,
)
from genlab_core.publishing.preflight import (
    resolve_client_kwargs as _resolve_client_kwargs,
)
from genlab_core.publishing.transcode import (
    ENCODE_SEMAPHORE as _ENCODE_SEMAPHORE,  # noqa: F401  re-exported for back-compat
)
from genlab_core.publishing.transcode import (
    MAX_CONCURRENT_ENCODES as _MAX_CONCURRENT_ENCODES,  # noqa: F401  back-compat
)
from genlab_core.publishing.transcode import (
    PLATFORM_MAP as _PLATFORM_MAP,  # noqa: F401  back-compat
)
from genlab_core.publishing.transcode import (
    transcode_for_platform as _transcode_for_platform,  # noqa: F401  back-compat
)

logger = logging.getLogger(__name__)

# Exit codes
EXIT_SUCCESS = 0
EXIT_NO_BLUEPRINTS = 1
EXIT_ALL_FAILED = 2
EXIT_DAILY_CAP = 3
EXIT_LOCK_HELD = 4


# ---------------------------------------------------------------------------
# First-Reply Affiliate Posting
# ---------------------------------------------------------------------------


# _post_affiliate_reply extracted to publishing/affiliate_reply.py in
# refactor-#9 PR 5/N. Re-exported with the underscore-prefixed alias
# the orchestrator's run_publish calls.


# ---------------------------------------------------------------------------
# Payload Builder
# ---------------------------------------------------------------------------


# build_payload + helpers extracted to publishing/payload_builder.py in
# refactor-#9 PR 3/N. Re-exported here for backwards compatibility with
# test_publish_all_platforms.py (which imports ``build_payload`` directly).
# The three former helpers (_build_platform_specific, _parse_visual_paths,
# _parse_json_field) had zero external callers; they live as non-underscore
# public names in payload_builder.py.


# ---------------------------------------------------------------------------
# Credential Resolution
# ---------------------------------------------------------------------------


# _resolve_client_kwargs + _preflight_token_refresh extracted to
# publishing/preflight.py in refactor-#9 PR 4/N. Re-exported below with
# underscore-prefixed aliases — the orchestrator continues to call
# ``_resolve_client_kwargs(...)`` / ``_preflight_token_refresh(...)``
# locally, AND test_publish_all_platforms.py patches at
# ``publish_all_platforms._resolve_client_kwargs`` (the local binding),
# which the re-export pattern keeps byte-stable.


# ---------------------------------------------------------------------------
# Core Publish Loop
# ---------------------------------------------------------------------------


def run_publish(
    *,
    niche_id: str,
    backlog_client,
    daily_cap,
    enabled_platforms: list[str],
    retry_only: bool = False,
) -> int:
    """Core publish logic. Returns an exit code.

    This is the testable entry point — main() handles CLI + lock + client creation.

    ``retry_only`` (R-83): when True, run crash-recovery + the retry pass ONLY —
    never select or publish a fresh VISUAL_READY blueprint. This is the mode for
    the 2nd daily publisher run, so it can recover failed platforms without
    doubling the day's content (1 reel/channel/day).
    """
    niche_id = _validate_niche(niche_id)
    logger.info(
        "[publish] niche=%s platforms=%s retry_only=%s", niche_id, enabled_platforms, retry_only
    )

    # R-35: refresh near-expiry tokens before any publish attempt (non-fatal).
    _preflight_token_refresh(niche_id, enabled_platforms)

    # Recover blueprints stuck in PUBLISHING status (crash recovery)
    try:
        stuck = backlog_client.get_blueprints_by_status("PUBLISHING", niche_id=niche_id)
        for bp in stuck:
            fields = bp.get("fields", bp)
            updated_at = fields.get("updated_at", "")
            attempt_count = int(fields.get("publish_attempts", 0) or 0)

            # If stuck for >30 minutes, reset to VISUAL_READY (or PUBLISH_FAILED if 3+ attempts)
            if updated_at:
                try:
                    dt = datetime.fromisoformat(updated_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if datetime.now(UTC) - dt > timedelta(minutes=30):
                        # R-29 per-platform recovery: previously "any platform
                        # published -> mark the whole blueprint PUBLISHED", which
                        # MASKED the platforms that failed/never ran (they were
                        # never retried). Now that the publish loop skips
                        # platforms already PUBLISHED (per-platform), we instead
                        # reset to VISUAL_READY and let it retry ONLY the failed/
                        # unattempted platforms — and an all-published blueprint
                        # self-finalizes to PUBLISHED on the next run. The
                        # attempt cap still bounds a permanently-stuck laggard.
                        if attempt_count >= 3:
                            backlog_client.blueprints.update(bp["id"], {"status": "PUBLISH_FAILED"})
                            logger.error(
                                "[publish] Blueprint %s stuck after %d attempts — marking PUBLISH_FAILED",
                                bp["id"][:8],
                                attempt_count,
                            )
                        else:
                            backlog_client.blueprints.update(bp["id"], {"status": "VISUAL_READY"})
                            logger.warning(
                                "[publish] Recovered stuck PUBLISHING blueprint %s -> VISUAL_READY "
                                "(per-platform retry skips already-published)",
                                bp["id"][:8],
                            )
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.warning("[publish] PUBLISHING recovery check failed: %s", e)

    # Auto-recover PUBLISH_FAILED blueprints after 24h cooldown
    try:
        failed = backlog_client.get_blueprints_by_status("PUBLISH_FAILED", niche_id=niche_id)
        for bp in failed:
            fields = bp.get("fields", bp)
            updated_at = fields.get("updated_at", "")
            if updated_at:
                try:
                    dt = datetime.fromisoformat(updated_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    if datetime.now(UTC) - dt > timedelta(hours=24):
                        backlog_client.blueprints.update(
                            bp["id"],
                            {
                                "status": "VISUAL_READY",
                                "publish_attempts": 0,
                            },
                        )
                        logger.info(
                            "[publish] Auto-recovered PUBLISH_FAILED blueprint %s after 24h cooldown",
                            bp["id"][:8],
                        )
                except (ValueError, TypeError):
                    pass
    except Exception as e:
        logger.warning("[publish] PUBLISH_FAILED recovery check failed: %s", e)

    # R-83: retry-only mode (2nd daily run) — recover + retry failed platforms,
    # but never publish a fresh blueprint (would double the day's content).
    if retry_only:
        logger.info("[publish] retry-only mode: skipping fresh publish, retrying failures only")
        _run_retry_pass(niche_id, backlog_client, daily_cap)
        return EXIT_SUCCESS

    # 1. Query VISUAL_READY blueprints for this niche
    all_blueprints = backlog_client.get_blueprints_by_status(
        "VISUAL_READY",
        niche_id=niche_id,
    )
    if not all_blueprints:
        logger.info("[publish] No VISUAL_READY blueprints for niche=%s", niche_id)
        # R-83: still retry failed platforms on no-fresh-content days (the old
        # code returned here, skipping the retry pass entirely).
        _run_retry_pass(niche_id, backlog_client, daily_cap)
        return EXIT_NO_BLUEPRINTS

    # 2. Run gatekeeper on each blueprint (filters by approval, schedule, score, media)
    #    Daily cap is checked per-platform in step 4, not at gatekeeper level.
    gatekeeper = PublishGatekeeper()
    eligible = []
    for bp in all_blueprints:
        fields = bp.get("fields", bp)
        # Cross-niche safety: skip blueprints that don't match our niche
        bp_niche = _normalize_niche(fields.get("niche_id", "") or "")
        if bp_niche != niche_id:
            logger.warning(
                "[publish] Skipping blueprint %s: niche mismatch (%s != %s)",
                bp.get("id", "?"),
                bp_niche,
                niche_id,
            )
            continue
        gate = gatekeeper.evaluate(fields, "")  # platform-agnostic evaluation
        if gate.allowed:
            eligible.append(bp)
        else:
            logger.info(
                "[publish] Blueprint %s blocked by %s: %s (title='%s')",
                bp.get("id", "?")[:8],
                gate.gate_name,
                gate.reason,
                (fields.get("title") or "")[:50],
            )

    if not eligible:
        logger.info("[publish] No blueprints passed gatekeeper for niche=%s", niche_id)
        return EXIT_NO_BLUEPRINTS

    # 3. Sort by priority_score descending, take top 1
    eligible.sort(
        key=lambda b: float(b.get("fields", {}).get("priority_score", 0) or 0),
        reverse=True,
    )
    best = eligible[0]
    fields = best.get("fields", best)
    record_id = best.get("id", "")
    candidate_id = fields.get("candidate_id", "")
    logger.info(
        "[publish] Selected blueprint %s (score=%.2f, hook=%s)",
        record_id[:16],
        float(fields.get("priority_score", 0) or 0),
        (fields.get("hook", "") or "")[:50],
    )

    # 4. Daily cap check (per-platform) + skip platforms already published in a
    #    prior (partial / crashed) run so re-publishing never double-posts a
    #    succeeded platform — only the failed/unattempted ones are retried (R-29,
    #    and the safety primitive behind a retry-only run, R-83).
    prior_published = _already_published_platforms(fields)
    if prior_published:
        logger.info(
            "[publish] %s already PUBLISHED for blueprint %s (prior run) — will skip",
            sorted(prior_published),
            record_id[:16],
        )
    platforms_to_publish = []
    for p in enabled_platforms:
        if p in prior_published:
            continue  # already live — never re-post
        if daily_cap and not daily_cap.can_publish(p):
            logger.info("[publish] %s: daily cap reached, skipping", p)
        else:
            platforms_to_publish.append(p)

    if not platforms_to_publish:
        # Distinguish "all enabled already published" (finalize) from "capped".
        if prior_published and set(enabled_platforms) <= prior_published:
            logger.info(
                "[publish] All enabled platforms already PUBLISHED for %s — finalizing",
                record_id[:16],
            )
            try:
                backlog_client.blueprints.update(record_id, {"status": "PUBLISHED"})
            except Exception as exc:
                logger.warning(
                    "[publish] Failed to finalize already-published %s: %s", record_id, exc
                )
            return EXIT_SUCCESS
        logger.info("[publish] All platforms capped for niche=%s", niche_id)
        return EXIT_DAILY_CAP

    # 5. Claim the blueprint: atomically flip VISUAL_READY -> PUBLISHING. If the
    #    claim is lost (another publisher already took it, or it's no longer
    #    VISUAL_READY), skip — never double-publish (R-24). The bare update used
    #    here previously had no status guard, so two concurrent/staggered
    #    publishers (e.g. the 06:35 and 10:30 runs, or a stray Mac launchd) could
    #    both select and publish the same blueprint.
    claim = getattr(backlog_client.blueprints, "claim_status", None)
    if claim is not None:
        try:
            claimed = claim(record_id, expected_status="VISUAL_READY", new_status="PUBLISHING")
        except Exception as exc:
            logger.warning("[publish] Claim failed for %s: %s", record_id, exc)
            claimed = False
        if not claimed:
            logger.info(
                "[publish] Blueprint %s no longer VISUAL_READY (claimed elsewhere?) — "
                "skipping to avoid double-publish",
                record_id,
            )
            return EXIT_NO_BLUEPRINTS
    else:
        # Legacy backend without an atomic claim (SharePoint) — best-effort flip.
        try:
            backlog_client.blueprints.update(
                record_id,
                {"status": "PUBLISHING"},
                typecast=True,
            )
        except Exception as exc:
            logger.warning("[publish] Failed to set PUBLISHING status: %s", exc)

    # 6. Publish to each platform. Pre-seed with platforms already published in a
    #    prior run so the persisted platform_publish_status keeps the full
    #    picture (R-29). `any_success` deliberately tracks only THIS run, so a
    #    still-partial blueprint (a prior success + a fresh failure) returns to
    #    VISUAL_READY and retries just the failed platform next time.
    platform_status: dict[str, Any] = {p: "PUBLISHED" for p in prior_published}
    any_success = False

    def _publish_one(platform: str) -> tuple[str, PublishResult]:
        registry_id = _to_registry_id(platform)
        try:
            kwargs = _resolve_client_kwargs(registry_id, niche_id)
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
                platform, result = future.result(timeout=600)  # 10-min max per platform
            except TimeoutError:
                platform = futures[future]
                result = PublishResult(
                    platform=_to_registry_id(platform),
                    success=False,
                    error=f"Publish timed out after 600s for {platform}",
                )
            except Exception as exc:
                platform = futures[future]
                result = PublishResult(
                    platform=_to_registry_id(platform),
                    success=False,
                    error=f"Publish error: {exc}",
                )
            error_class = ""
            if result.success:
                any_success = True
                platform_status[platform] = "PUBLISHED"
                if daily_cap:
                    daily_cap.record_publish(platform)
                logger.info(
                    "[publish] %s: SUCCESS post_id=%s url=%s",
                    platform,
                    result.post_id,
                    result.post_url,
                )
                # Post affiliate link as first reply/comment (non-blocking)
                _post_affiliate_reply(platform, result.post_id, fields, niche_id)
                # Immediately persist platform status to prevent double-post on crash
                try:
                    backlog_client.blueprints.update(
                        record_id,
                        {"platform_publish_status": json.dumps(platform_status)},
                    )
                except Exception as exc:
                    # Best-effort: final update at step 7 will re-try. Log at
                    # warning so if step 7 also fails and we crash mid-publish,
                    # the debug trail makes the double-post risk visible.
                    logger.warning(
                        "[publish] Mid-publish state persistence failed for %s "
                        "(will retry at final update): %s",
                        platform,
                        exc,
                    )
            else:
                error_class = classify(result.error, platform)
                attempt_data = platform_status.get(platform, {})
                if isinstance(attempt_data, dict):
                    prev_attempts = attempt_data.get("attempts", 0)
                else:
                    prev_attempts = 0
                platform_status[platform] = {
                    "status": "FAILED",
                    "attempts": prev_attempts + 1,
                    "last_error": result.error[:200],
                    "error_class": error_class,
                    # R-21: tag failures that may have actually landed so the
                    # cross-run retry pass won't blindly re-publish them.
                    "ambiguous": is_ambiguous_failure(result.error),
                }
                logger.error("[publish] %s: FAILED error=%s", platform, result.error)

            # Record to Publishing_Analytics
            # Use SKIPPED for credential failures (not retryable, not a real failure)
            if result.success:
                analytics_status = "SUCCESS"
            elif error_class == "CREDENTIAL":
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
            )

    # 7. Update blueprint final status
    # Track publish attempts
    attempt_count = int(fields.get("publish_attempts", 0) or 0) + 1
    if not any_success and attempt_count >= 3:
        final_status = "PUBLISH_FAILED"
        logger.error(
            "[publish] Blueprint %s failed %d times — marking PUBLISH_FAILED",
            record_id,
            attempt_count,
        )
    elif any_success:
        final_status = "PUBLISHED"
    else:
        final_status = "VISUAL_READY"
    try:
        backlog_client.blueprints.update(
            record_id,
            {
                "status": final_status,
                "platform_publish_status": json.dumps(platform_status),
                "publish_attempts": attempt_count,
            },
            typecast=True,
        )
        logger.info(
            "[publish] Blueprint %s -> %s (%s)", record_id[:16], final_status, platform_status
        )
    except Exception as exc:
        logger.error("[publish] Failed to update final status: %s", exc)

    # Push dashboard notification event
    try:
        from genlab_core.observability.dashboard_events import push_event

        hook_text = (fields.get("hook_text") or fields.get("hook") or fields.get("title") or "")[
            :50
        ]
        success_platforms = [p for p, s in platform_status.items() if s == "PUBLISHED"]
        terminal_failed = _terminal_failed_platforms(platform_status)
        if any_success and terminal_failed:
            # Some platforms succeeded, but others are PERMANENTLY failed (won't auto-retry).
            # Surface to the dashboard so the user knows publishing was partial.
            failed_summary = ", ".join(
                f"{p}({d.get('error_class', '?')})" for p, d in terminal_failed.items()
            )
            push_event(
                "publish_partial",
                f"Partial publish: {hook_text}",
                f"OK: {', '.join(success_platforms) or 'none'}; permanent failures: {failed_summary}",
                entity_id=record_id,
                entity_type="blueprint",
                niche_id=niche_id,
            )
        elif any_success:
            push_event(
                "publish_success",
                f"Published: {hook_text}",
                f"{len(success_platforms)} platform(s): {', '.join(success_platforms)}",
                entity_id=record_id,
                entity_type="blueprint",
                niche_id=niche_id,
            )
        elif final_status == "PUBLISH_FAILED":
            push_event(
                "publish_failure",
                f"Publish failed: {hook_text}",
                f"All {attempt_count} attempts failed",
                entity_id=record_id,
                entity_type="blueprint",
                niche_id=niche_id,
            )
    except Exception:
        pass  # non-fatal

    # 8. Register PendingFeedback for the learning loop (bandit updates)
    if any_success:
        try:
            from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
            from genlab_core.learning.pending_feedback_task import PendingFeedbackTask

            fb_store = PendingFeedbackStore(backlog_client)
            for plat, pstatus in platform_status.items():
                if pstatus == "PUBLISHED" or (
                    isinstance(pstatus, dict) and pstatus.get("status") == "PUBLISHED"
                ):
                    # Find the post_id from the publish results
                    post_id_for_plat = ""
                    for future in futures:
                        try:
                            fp, fr = future.result()
                            if fp == plat and fr.success:
                                post_id_for_plat = fr.post_id or ""
                                break
                        except Exception as exc:
                            logger.debug("Failed to get post_id from future: %s", exc)

                    # Build bandit_context with hook features for LinUCB (Break 11 fix)
                    bandit_ctx = None
                    try:
                        from genlab_core.learning.hook_features import build_feature_vector
                        from genlab_core.learning.linucb import build_content_context

                        hook_txt = fields.get("hook", "")
                        hook_feats = build_feature_vector(hook_txt) if hook_txt else {}
                        linucb_ctx = build_content_context(fields, niche_id).tolist()
                        bandit_ctx = {
                            "hook_features": hook_feats,
                            "linucb_context": linucb_ctx,
                        }
                        # Carry the bandit-picked hook style so the
                        # metric_collector update can also credit the
                        # style arm at 48h (closes the loop opened in
                        # commit 84b7801).
                        hook_style = fields.get("hook_style", "")
                        if hook_style:
                            bandit_ctx["extra_arms"] = [f"style:{niche_id}:{hook_style}"]
                    except Exception as ctx_exc:
                        logger.debug("[publish] bandit_context build failed: %s", ctx_exc)

                    task = PendingFeedbackTask(
                        content_id=candidate_id or record_id[:16],
                        platform=plat,
                        niche_id=niche_id,
                        published_at=datetime.now(UTC),
                        platform_post_id=post_id_for_plat,
                        content_type="video",
                        hook_text=fields.get("hook", "")[:100],
                        hook_length=len(fields.get("hook", "")),
                        bandit_arm=fields.get("arm_id", ""),
                        bandit_context=bandit_ctx,
                    )
                    fb_store.create(task)
        except Exception as e:
            logger.warning("[publish] PendingFeedback registration failed (non-fatal): %s", e)

    _run_retry_pass(niche_id, backlog_client, daily_cap)
    return EXIT_SUCCESS if any_success else EXIT_ALL_FAILED


def _run_retry_pass(niche_id: str, backlog_client, daily_cap) -> None:
    """Retry recently-PUBLISHED blueprints' failed platforms (R-83).

    Extracted so it can run standalone: on a retry-only 2nd daily run (which must
    NOT publish fresh content) and on no-fresh-content days (where run_publish
    used to return before reaching it). The R-29 per-platform skip guarantees a
    retry never re-posts a platform that already succeeded.
    """
    # ── Retry pass: check recent PUBLISHED blueprints for failed platforms ──
    # Only check blueprints published in the last 7 days (not the entire history)
    try:
        (datetime.now(UTC) - timedelta(days=7)).isoformat()
        published_bps = backlog_client.get_blueprints_by_status(
            "PUBLISHED",
            niche_id=niche_id,
            max_records=50,
        )
        for bp in published_bps:
            fields = bp.get("fields", bp)
            pps_raw = fields.get("platform_publish_status", "{}")
            if isinstance(pps_raw, str):
                try:
                    pps = json.loads(pps_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            else:
                pps = pps_raw or {}

            # Find platforms that failed and are eligible for retry
            retry_platforms = []
            for plat, status_data in pps.items():
                if isinstance(status_data, dict) and status_data.get("status") == "FAILED":
                    error_class = status_data.get("error_class", "TRANSIENT")
                    attempts = status_data.get("attempts", 0)
                    if not should_retry(error_class) or attempts >= 3:
                        continue
                    # R-21: never auto-re-publish a failure that may have actually
                    # landed (timeout / broken pipe / container-expired) — that
                    # risks a duplicate post on the live channel. Skip; these need
                    # a "did it land" check or human review, not a blind retry.
                    if status_data.get("ambiguous") or is_ambiguous_failure(
                        status_data.get("last_error", "")
                    ):
                        logger.warning(
                            "[publish] Skipping cross-run retry of %s for blueprint %s — "
                            "ambiguous failure may have landed (needs manual/landing check)",
                            plat,
                            bp["id"][:8],
                        )
                        continue
                    # Check backoff timing
                    next_retry = status_data.get("next_retry_after", "")
                    if next_retry:
                        try:
                            retry_dt = datetime.fromisoformat(next_retry)
                            if retry_dt.tzinfo is None:
                                retry_dt = retry_dt.replace(tzinfo=UTC)
                            if retry_dt > datetime.now(UTC):
                                continue  # Not yet time to retry
                        except (ValueError, TypeError):
                            pass
                    # Check daily cap before retrying
                    if daily_cap and not daily_cap.can_publish(plat):
                        logger.info("[publish] Retry skipped for %s — daily cap reached", plat)
                        continue
                    retry_platforms.append(plat)

            if not retry_platforms:
                continue

            # Skip retry if media files are gone (cleaned up)
            vp_raw = fields.get("visual_paths", "[]")
            try:
                vp_list = json.loads(vp_raw) if isinstance(vp_raw, str) else (vp_raw or [])
            except (json.JSONDecodeError, TypeError):
                vp_list = []
            if vp_list and not any(Path(p).exists() for p in vp_list if p):
                logger.info("[publish] Skipping retry for %s — media files deleted", bp["id"][:8])
                continue

            logger.info(
                "[publish] Retrying %d failed platform(s) for blueprint %s: %s",
                len(retry_platforms),
                bp["id"][:8],
                retry_platforms,
            )

            # Retry each failed platform with its own payload
            try:
                record_id = bp["id"]

                for plat in retry_platforms:
                    payload = build_payload(fields, plat)
                    registry_id = _to_registry_id(plat)
                    kwargs = _resolve_client_kwargs(registry_id, niche_id)
                    if not kwargs:
                        continue

                    try:
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
                            attempts = (
                                prev.get("attempts", 0) if isinstance(prev, dict) else 0
                            ) + 1
                            delay = retry_delay_seconds(ec, attempts)
                            pps[plat] = {
                                "status": "FAILED",
                                "attempts": attempts,
                                "last_error": result.error[:200],
                                "error_class": ec,
                                "next_retry_after": (
                                    datetime.now(UTC) + timedelta(seconds=delay)
                                ).isoformat(),
                            }
                            logger.warning(
                                "[publish] Retry FAILED: %s/%s (%s): %s",
                                niche_id,
                                plat,
                                ec,
                                result.error[:100],
                            )

                        # Record to analytics
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

                # Update platform_publish_status
                backlog_client.blueprints.update(
                    record_id,
                    {
                        "platform_publish_status": json.dumps(pps),
                    },
                )

                # If any platforms remain terminally failed after retries, surface
                # to the dashboard. Without this, partial-publish failures are
                # only visible by inspecting platform_publish_status by hand.
                terminal_failed = _terminal_failed_platforms(pps)
                if terminal_failed:
                    try:
                        from genlab_core.observability.dashboard_events import push_event

                        hook_text = (
                            fields.get("hook_text")
                            or fields.get("hook")
                            or fields.get("title")
                            or ""
                        )[:50]
                        ok_plats = [
                            p
                            for p, s in pps.items()
                            if s == "PUBLISHED"
                            or (isinstance(s, dict) and s.get("status") == "PUBLISHED")
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
            except Exception as e:
                logger.warning(
                    "[publish] Retry processing failed for blueprint %s: %s", bp["id"][:8], e
                )

    except Exception as e:
        logger.debug("[publish] Retry pass failed: %s", e)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Canonical multi-platform publisher for Gen Lab niches",
    )
    parser.add_argument(
        "--niche",
        required=True,
        help="Niche ID (ai_creators, gaming, sports, movies, anime) or 'all'",
    )
    parser.add_argument(
        "--platforms",
        nargs="+",
        default=None,
        help="Override enabled platforms (default: instagram youtube facebook twitter threads)",
    )
    parser.add_argument(
        "--retry-only",
        action="store_true",
        help="R-83: recover + retry failed platforms only; never publish a fresh "
        "blueprint. Use for the 2nd daily run so it can't double the day's content.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from dotenv import load_dotenv

    load_dotenv(override=True)

    from genlab_core.http.backlog_client import BacklogClient
    from genlab_core.publishing.daily_cap import DailyCapEnforcer

    enabled = args.platforms or [
        "instagram",
        "youtube",
        "facebook",
        "twitter",
        "threads",
    ]

    niches = (
        ["ai_creators", "gaming", "sports", "movies", "anime"]
        if args.niche == "all"
        else [_validate_niche(args.niche)]
    )

    # Reuse a single BacklogClient across all niches to avoid creating
    # multiple connection pools (P21)
    shared_client = BacklogClient()

    total_exit = 0
    for nid in niches:
        nid = _validate_niche(nid)
        logger.info("=" * 60)
        logger.info("Publishing for niche: %s", nid)
        logger.info("=" * 60)

        lock = PidLock(nid)
        if not lock.acquire():
            logger.warning("[publish] Lock held for niche=%s — skipping", nid)
            continue

        try:
            enforcer = DailyCapEnforcer(backlog_client=shared_client, niche_id=nid)
            enforcer.log_headroom()

            exit_code = run_publish(
                niche_id=nid,
                backlog_client=shared_client,
                daily_cap=enforcer,
                enabled_platforms=enabled,
                retry_only=args.retry_only,
            )
            total_exit = max(total_exit, exit_code)
        except Exception as exc:
            logger.error("[publish] Failed for %s: %s", nid, exc)
            total_exit = 1
        finally:
            lock.release()

    return total_exit


if __name__ == "__main__":
    sys.exit(main())
