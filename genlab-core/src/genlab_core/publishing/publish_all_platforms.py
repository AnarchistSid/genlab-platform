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
import os
import random
import sys
import time

from genlab_core.publishing.blueprint_selector import select_blueprint
from genlab_core.publishing.crash_recovery import (
    recover_publish_failed,
    recover_stuck_publishing,
)
from genlab_core.publishing.feedback_registration import register_pending_feedback

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
    validate_niche as _validate_niche,
)
from genlab_core.publishing.parallel_publish import execute_parallel_publish
from genlab_core.publishing.payload_builder import build_payload  # noqa: F401  back-compat
from genlab_core.publishing.pid_lock import PidLock
from genlab_core.publishing.platform_status import (
    already_published_platforms as _already_published_platforms,
)
from genlab_core.publishing.platform_status import (
    terminal_failed_platforms as _terminal_failed_platforms,
)
from genlab_core.publishing.platform_status import (
    transient_failed_platforms as _transient_failed_platforms,
)
from genlab_core.publishing.preflight import (
    preflight_token_refresh as _preflight_token_refresh,
)
from genlab_core.publishing.retry_pass import (
    run_retry_pass as _run_retry_pass,
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

# Exit codes — semantics pinned in the genlab-publisher.service
# SuccessExitStatus allowlist (0, 1, 3, 4). Any code OUTSIDE the
# allowlist (2 + 5+ + unexpected) triggers the OnFailure handler →
# CRITICAL alert on Mission Control. Adding a new exit code? Update
# both this enum AND the .service file's SuccessExitStatus to keep
# the dashboard signal honest.
EXIT_SUCCESS = 0
EXIT_NO_BLUEPRINTS = 1  # benign — no fresh content today
EXIT_ALL_FAILED = 2  # real signal — every platform failed on the chosen blueprint
EXIT_DAILY_CAP = 3  # benign — already published today
EXIT_LOCK_HELD = 4  # benign — concurrent run, next timer retry
EXIT_UNEXPECTED = 5  # real signal — unhandled exception in run_publish


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
# Per-niche enabled-platform filter (2026-06-18)
# ---------------------------------------------------------------------------


# Mapping from per-niche config's platform names to the canonical names
# the publisher uses. Inverse of ``platform_status.PLATFORM_ID_MAP``:
# config uses "x", code uses "twitter".
_CONFIG_PLATFORM_ALIASES: dict[str, str] = {
    "x": "twitter",
    "x_twitter": "twitter",
}


def _publisher_niche_is_paused(niche_id: str) -> bool:
    """PR #577 consumer wire (2026-06-25): consult the per-niche
    pause primitive shipped in PR #575.

    Returns True when the niche has an active pause row. Returns
    False on:
      * No active pause
      * ImportError on niche_pause module (graceful degrade against
        pre-PR-577 core — safe to deploy this PR independently of
        the niche_pause module)
      * Any exception from is_paused (fail-OPEN — publishing MUST
        NOT halt on a transient DB hiccup; the auto-approver wire
        in PR #576 uses the identical pattern)

    Lazy import — the cost of pause-checking is paid only on the
    publisher's actual run (once per niche per cron tick), not on
    module load.
    """
    try:
        from genlab_core.scheduling.niche_pause import is_paused

        return is_paused(niche_id)
    except ImportError:
        # Pre-PR-577 core → no pause table → not paused. Safe default.
        return False
    except Exception as exc:  # noqa: BLE001 — never crash the publish
        logger.warning(
            "[publish] niche_pause.is_paused(%r) failed: %s",
            niche_id,
            exc,
        )
        return False


def _filter_enabled_platforms(niche_id: str, default_list: list[str]) -> list[str]:
    """Return ``default_list`` minus any platform the niche's
    ``publishing.yaml`` explicitly disables.

    Pre-2026-06-18 the publisher hardcoded
    ``["instagram", "youtube", "facebook", "twitter", "threads"]``
    regardless of per-niche settings. Three niches (sports, movies,
    anime) set ``platforms.x.enabled: false`` because they don't have
    Twitter credentials provisioned. The hardcoded list still tried
    Twitter, hit "No x_twitter credentials" CREDENTIAL failure, and
    the run_publish ``any_success=False`` path returned ``EXIT_ALL_
    FAILED`` causing systemd to mark publisher.service as failed
    every cycle.

    Defaults are conservative: if config can't be loaded or the
    ``platforms`` block is missing, fall back to ``default_list``
    unchanged. ONLY explicit ``enabled: false`` removes a platform.

    Config alias note: per-niche YAMLs use ``platforms.x`` while the
    publisher uses ``twitter``. ``_CONFIG_PLATFORM_ALIASES`` maps
    config-side names to publisher-side names so the filter compares
    apples to apples.
    """
    try:
        from pathlib import Path

        import yaml as _yaml

        from genlab_core.pipeline.cli import NICHE_DIR_NAMES, _resolve_genlab_root

        root = _resolve_genlab_root()
        dir_name = NICHE_DIR_NAMES.get(niche_id)
        if not dir_name:
            return default_list
        niche_root = Path(root) / dir_name
        # Gaming's nested layout first, then flat layout (matches
        # ``auto_approver.load_policy``).
        candidates = [
            niche_root / "niches" / niche_id / "config" / "publishing.yaml",
            niche_root / "config" / "publishing.yaml",
        ]
        data = None
        for candidate in candidates:
            if candidate.exists():
                with open(candidate, encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                break
        if not isinstance(data, dict):
            return default_list
        platforms_block = data.get("platforms")
        if not isinstance(platforms_block, dict):
            # BB-style configs use per-platform yamls instead of a
            # consolidated ``platforms`` block — no filter needed.
            return default_list

        # PR #514 (2026-06-24): list-format allowlist. Gaming uses
        #
        #   platforms:
        #     enabled:
        #       - instagram
        #       - youtube
        #       # - twitter        # Disabled Sprint 30
        #
        # rather than per-platform ``enabled: false`` dicts. Pre-PR the
        # filter only knew the dict shape; the list shape's keys
        # (``enabled``, ``concurrent_publish``, ``publish_strategy``)
        # all failed the ``isinstance(settings, dict)`` check + got
        # skipped, returning the full default list. The publisher then
        # tried Twitter, hit "No x_twitter credentials" CREDENTIAL skip,
        # and recorded a noisy SKIPPED row in publishing_analytics
        # every day. The list shape is the INTENDED form for niches
        # that disable platforms by omission.
        #
        # Resolution order (mutually exclusive):
        #   1. ``platforms.enabled: [list]``  → ALLOWLIST: default_list ∩ list
        #   2. ``platforms.<name>.enabled: false`` per-platform dict
        #      → DENYLIST (legacy shape, preserved)
        #
        # If both shapes exist somehow, the list-allowlist wins (it's
        # the more explicit operator intent).
        enabled_list = platforms_block.get("enabled")
        if isinstance(enabled_list, list):
            allowed: set[str] = set()
            for entry in enabled_list:
                if not isinstance(entry, str):
                    continue
                publisher_name = _CONFIG_PLATFORM_ALIASES.get(entry, entry)
                allowed.add(publisher_name)
            filtered = [p for p in default_list if p in allowed]
            if len(filtered) < len(default_list):
                logger.info(
                    "[publish] %s: allowlist platforms %s (config-disabled: %s)",
                    niche_id,
                    filtered,
                    sorted(set(default_list) - set(filtered)),
                )
            return filtered

        # Build the set of explicitly-disabled platforms (publisher-side
        # names, applying the config→publisher alias map).
        disabled: set[str] = set()
        for config_name, settings in platforms_block.items():
            if not isinstance(settings, dict):
                continue
            if settings.get("enabled") is False:
                publisher_name = _CONFIG_PLATFORM_ALIASES.get(config_name, config_name)
                disabled.add(publisher_name)

        if not disabled:
            return default_list

        filtered = [p for p in default_list if p not in disabled]
        if len(filtered) < len(default_list):
            logger.info(
                "[publish] %s: filtered platforms %s (config-disabled: %s)",
                niche_id,
                filtered,
                sorted(disabled),
            )
        return filtered
    except Exception:
        logger.warning(
            "[publish] %s: platform-enabled filter failed — using full default list",
            niche_id,
            exc_info=True,
        )
        return default_list


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

    # PR #577 consumer wire (2026-06-25): per-niche pause halts BOTH
    # fresh publish AND retry pass. Without this, operator-approved
    # blueprints would still publish through a paused niche (only the
    # auto-approver respected the pause until now). Same fail-OPEN
    # pattern as the auto_approver wire — never halt publishing on a
    # transient DB hiccup.
    #
    # Returns EXIT_SUCCESS so the launchd job is recorded as success
    # (the pause is a deliberate skip, not a failure). The pipeline
    # log carries the reason for operators investigating.
    if _publisher_niche_is_paused(niche_id):
        logger.info(
            "[publish] niche=%s — niche is paused (PR #577 emergency-stop), "
            "skipping fresh publish + retry pass",
            niche_id,
        )
        return EXIT_SUCCESS

    # R-35: refresh near-expiry tokens before any publish attempt (non-fatal).
    _preflight_token_refresh(niche_id, enabled_platforms)

    # Crash-recovery passes (extracted in refactor-#9 PR 6a/N into
    # publishing/crash_recovery.py — see that module for docstrings + tests).
    # Both passes are strictly best-effort; failures log + swallow.
    recover_stuck_publishing(backlog_client, niche_id)
    recover_publish_failed(backlog_client, niche_id)

    # R-83: retry-only mode (2nd daily run) — recover + retry failed platforms,
    # but never publish a fresh blueprint (would double the day's content).
    if retry_only:
        logger.info("[publish] retry-only mode: skipping fresh publish, retrying failures only")
        _run_retry_pass(niche_id, backlog_client, daily_cap)
        return EXIT_SUCCESS

    # 1-3. Query + gatekeeper + top-1-by-score, extracted to
    # publishing/blueprint_selector.py in refactor-#9 PR 6d/N.
    best = select_blueprint(niche_id, backlog_client)
    if best is None:
        # No fresh content — still run the retry pass so failed platforms
        # don't dark across days. (R-83.)
        _run_retry_pass(niche_id, backlog_client, daily_cap)
        return EXIT_NO_BLUEPRINTS
    fields = best.get("fields", best)
    record_id = best.get("id", "")
    candidate_id = fields.get("candidate_id", "")

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

    # 2026-07-21: pre-flight media check. If ALL visual_paths point to
    # files that no longer exist on disk (disk-cleanup cascade / GC),
    # fan-out to N platforms is doomed — each will hit MISSING_RENDER
    # in payload_builder and record a SKIPPED analytics row. Better to
    # archive the blueprint here and skip the entire publish loop.
    #
    # Mirrors the retry_pass._media_files_missing + archive-on-fail
    # logic (`fad1245e` 2026-07-21). Fresh publish path was the missing
    # counterpart — retry_pass caught retries but fresh publishes still
    # burned platform-attempt budget on doomed blueprints.
    from genlab_core.publishing.retry_pass import _media_files_missing

    if _media_files_missing(fields):
        logger.warning(
            "[publish] ARCHIVING %s pre-publish — media files deleted "
            "(no recovery path; skipping N-platform fan-out to save budget)",
            record_id[:8],
        )
        try:
            backlog_client.blueprints.update(
                record_id,
                {
                    "status": "ARCHIVED",
                    "error_message": (
                        (fields.get("error_message") or "")
                        + " | archived:2026-07-21:media_missing_pre_publish"
                    ).strip(" |"),
                },
                typecast=True,
            )
        except Exception as exc:
            logger.warning(
                "[publish] Pre-publish archive failed for %s: %s (continuing)",
                record_id[:8],
                exc,
                exc_info=True,
            )
        return  # Skip publish entirely — blueprint archived.

    # 6. Per-platform parallel publish. The ParallelPublishOutcome carries the
    # per-platform status map, this-run success flag, and the post_ids dict
    # (so step 8 can register PendingFeedback without re-iterating futures).
    # Extracted in refactor-#9 PR 6b/N — see publishing/parallel_publish.py.
    outcome = execute_parallel_publish(
        platforms_to_publish=platforms_to_publish,
        prior_published=prior_published,
        niche_id=niche_id,
        fields=fields,
        record_id=record_id,
        candidate_id=candidate_id,
        backlog_client=backlog_client,
        daily_cap=daily_cap,
    )
    platform_status = outcome.platform_status
    any_success = outcome.any_success

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
        transient_failed = _transient_failed_platforms(platform_status)
        # R-36: publish_partial fires whenever NOT all attempted platforms
        # reached PUBLISHED — including TRANSIENT failures that the retry
        # loop will pick up later. Previously the event only fired on
        # TERMINAL failures, so a "1 of 5 succeeded, 4 timed out" outcome
        # was indistinguishable from a clean 5/5 publish on the dashboard.
        if any_success and (terminal_failed or transient_failed):
            term_part = (
                "permanent: "
                + ", ".join(f"{p}({d.get('error_class', '?')})" for p, d in terminal_failed.items())
                if terminal_failed
                else ""
            )
            trans_part = (
                "queued for retry: "
                + ", ".join(
                    f"{p}({d.get('error_class', 'TRANSIENT')})" for p, d in transient_failed.items()
                )
                if transient_failed
                else ""
            )
            failed_summary = "; ".join(part for part in (term_part, trans_part) if part)
            push_event(
                "publish_partial",
                f"Partial publish: {hook_text}",
                f"OK: {', '.join(success_platforms) or 'none'}; {failed_summary}",
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

    # 2026-06-22 — EVENT_PUBLISH episodic emit. Distinct from
    # dashboard_events above:
    # - dashboard_events: operator-facing Mission Control feed
    # - episodic_events: learning-facing cross-run analytics + RAG +
    #   weekly scratchpad reflection (the Sunday Opus job reads these)
    #
    # Closes one of the 4 dormant EVENT_* types identified by the
    # 2026-06-22 audit (BANDIT_PICK + POST_BOMBED + REWARD_WINDOW_CLOSED
    # + OPERATOR_REVIEW + OPERATOR_EDIT already wired; PUBLISH was the
    # next-most-impactful missing producer). Without this emit, the
    # scratchpad's "what published this week" question gets no answer
    # and the cross-run publishing-velocity signal is lost.
    #
    # Fires only on any_success — failures already have their own
    # dashboard_event (publish_failure) and the metric_collector
    # emits POST_BOMBED downstream when reward at 48h is near zero.
    # An EVENT_PUBLISH_FAILED could be added later if needed; today
    # the gap is the SUCCESSFUL path being un-recorded.
    if any_success:
        try:
            from genlab_core.learning.episodic_memory import EVENT_PUBLISH, record_event

            success_platforms = [p for p, s in platform_status.items() if s == "PUBLISHED"]
            hook_snippet = (
                fields.get("hook_text") or fields.get("hook") or fields.get("title") or ""
            )[:200]
            record_event(
                event_type=EVENT_PUBLISH,
                niche_id=niche_id,
                blueprint_id=record_id,
                payload={
                    "platforms": success_platforms,
                    "platform_count": len(success_platforms),
                    "attempt_count": attempt_count,
                    "hook_snippet": hook_snippet,
                    "had_partial_failures": bool(
                        _terminal_failed_platforms(platform_status)
                        or _transient_failed_platforms(platform_status)
                    ),
                },
            )
        except Exception as exc:  # noqa: BLE001 — episodic emit must never block publish
            # 2026-07-14 audit: elevated DEBUG → WARNING. EVENT_PUBLISH
            # feeds real-time observability + episodic-memory for
            # future RCA queries. Silent failure means the ops
            # dashboard doesn't get real-time publish notifications
            # AND future post-mortems have missing telemetry. WARNING
            # so this stays visible in default log level.
            logger.warning(
                "[publish_all_platforms] EVENT_PUBLISH emit skipped: %s "
                "(publish succeeded but real-time event stream + episodic "
                "memory record was NOT written)",
                exc,
            )

    # 8. Register PendingFeedback, extracted in refactor-#9 PR 6d/N to
    # publishing/feedback_registration.py.
    if any_success:
        register_pending_feedback(
            outcome=outcome,
            fields=fields,
            record_id=record_id,
            candidate_id=candidate_id,
            niche_id=niche_id,
            backlog_client=backlog_client,
        )

    _run_retry_pass(niche_id, backlog_client, daily_cap)
    return EXIT_SUCCESS if any_success else EXIT_ALL_FAILED


# _run_retry_pass extracted to publishing/retry_pass.py in refactor-#9
# PR 6c/N. Re-exported with the underscore-prefixed alias the
# orchestrator's call sites use.


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

    default_platforms = [
        "instagram",
        "youtube",
        "facebook",
        "twitter",
        "threads",
    ]
    # Operator-supplied --platforms overrides the default + the
    # per-niche enabled filter (so they can force a single-platform
    # debug run). When unspecified, ``default_platforms`` gets filtered
    # below by each niche's ``platforms.<name>.enabled: false`` setting.
    operator_explicit_platforms = args.platforms is not None
    enabled = args.platforms or list(default_platforms)

    niches = (
        ["ai_creators", "gaming", "sports", "movies", "anime"]
        if args.niche == "all"
        else [_validate_niche(args.niche)]
    )

    # Reuse a single BacklogClient across all niches to avoid creating
    # multiple connection pools (P21)
    shared_client = BacklogClient()

    total_exit = 0
    # 2026-07-22 anti-fingerprint (item C): inter-niche jitter breaks
    # Meta's "5 Pages publishing in the same clockwork rhythm" detector.
    # Historical: publisher fires at 12:05 IST once daily, processes
    # niches sequentially → Meta sees ai_creators post at ~min:37, anime
    # at ~min:52, sports at ~min:45 EVERY SINGLE DAY for weeks. Adding
    # 30-180s random offset per niche breaks the per-niche minute-slot
    # regularity (Agent 2 finding, 2026-07-22 Meta audit).
    # Skipped on the very first niche of the run — the initial
    # timer-driven fire already establishes t0 variance across days
    # (RandomizedDelaySec can be added on the systemd side in a
    # follow-up if needed).
    # Env-overridable for tests and operator debugging (set to 0 to
    # disable): GENLAB_PUBLISH_INTER_NICHE_JITTER_MIN/MAX seconds.
    jitter_min = int(os.environ.get("GENLAB_PUBLISH_INTER_NICHE_JITTER_MIN", "30"))
    jitter_max = int(os.environ.get("GENLAB_PUBLISH_INTER_NICHE_JITTER_MAX", "180"))
    first_niche = True
    for nid in niches:
        nid = _validate_niche(nid)
        if not first_niche and jitter_max > jitter_min > 0:
            delay = random.uniform(jitter_min, jitter_max)
            logger.info(
                "[publish] Inter-niche jitter: sleeping %.1fs before niche=%s "
                "(anti-fingerprint, 2026-07-22)", delay, nid,
            )
            time.sleep(delay)
        first_niche = False
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

            # 2026-06-18 fix: filter the default platform list by each
            # niche's per-platform ``enabled: false`` setting in
            # ``publishing.yaml``. Pre-fix, the publisher hardcoded
            # ``["instagram", "youtube", "facebook", "twitter",
            # "threads"]`` and ignored that anime/movies/sports all set
            # ``platforms.x.enabled: false`` (no Twitter creds). Every
            # cycle the retry-only pass tried Twitter, hit "No
            # x_twitter credentials", `_on_failure` marked it FAILED,
            # ``any_success`` was False (other platforms hit daily-cap-
            # reached), and ``run_publish`` returned ``EXIT_ALL_FAILED``
            # → systemd marked publisher.service as failed → OnFailure
            # alert spam.
            #
            # Operator --platforms override skips this filter (debug
            # flag); the default-list path filters per niche.
            niche_enabled = (
                enabled if operator_explicit_platforms else _filter_enabled_platforms(nid, enabled)
            )

            exit_code = run_publish(
                niche_id=nid,
                backlog_client=shared_client,
                daily_cap=enforcer,
                enabled_platforms=niche_enabled,
                retry_only=args.retry_only,
            )
            total_exit = max(total_exit, exit_code)
        except Exception as exc:
            logger.error("[publish] Failed for %s: %s", nid, exc)
            # EXIT_UNEXPECTED (5), not 1: an unhandled exception is a
            # real signal that needs operator attention. Using 1 would
            # lump it in with the BENIGN "no blueprints today" exit code
            # and the SuccessExitStatus allowlist in the systemd unit
            # would silence the OnFailure alert.
            total_exit = max(total_exit, EXIT_UNEXPECTED)
        finally:
            lock.release()

    return total_exit


if __name__ == "__main__":
    sys.exit(main())
