"""Post-publish PendingFeedback registration for the learning loop.

After a successful publish, register one :class:`PendingFeedbackTask`
per platform that succeeded. The metric_collector picks these up at
6h/24h/48h/168h windows and feeds the engagement data back to the
LinUCB contextual bandit + the hook-style arms.

Single public entry point :func:`register_pending_feedback`. Strictly
non-fatal: any exception is logged at WARN and swallowed — the
metric_collector is on a separate cron and a missed registration just
means we lose the learning signal for THIS post, not that we crash the
publish pipeline.

Lives in its own module so the orchestrator's main flow doesn't have
to inline the bandit-context construction. Extracted in the
refactor-#9 decomposition (PR 6d/N).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from genlab_core.publishing.parallel_publish import ParallelPublishOutcome

logger = logging.getLogger(__name__)


def register_pending_feedback(
    *,
    outcome: ParallelPublishOutcome,
    fields: dict[str, Any],
    record_id: str,
    candidate_id: str,
    niche_id: str,
    backlog_client: Any,
) -> None:
    """Register one PendingFeedbackTask per successfully-published platform.

    Args mirror what the orchestrator already has in scope after the
    publish dispatch returns. No-op if no platforms succeeded this run.
    """
    # Defer the imports — keeps the orchestrator's import-time cost down
    # AND avoids a circular import (pending_feedback_store transitively
    # imports BacklogClient which imports things from publishing/...).
    try:
        from genlab_core.learning.pending_feedback_store import PendingFeedbackStore
        from genlab_core.learning.pending_feedback_task import PendingFeedbackTask
    except Exception as e:
        logger.warning("[publish] PendingFeedback imports failed (non-fatal): %s", e)
        return

    fb_store = PendingFeedbackStore(backlog_client)
    try:
        # Lift published_at out of the PendingFeedbackTask constructor
        # call so the SAME timestamp powers both the task.published_at
        # field AND the hour-arm built inside _build_bandit_context.
        # Pre-PR Z this was computed inline as datetime.now(UTC) per
        # platform — same hour in practice but a fresh wall-clock read
        # per platform. Lifting it makes the hour-of-day attribution
        # consistent across all platforms in a single dispatch.
        published_at = datetime.now(UTC)
        for plat, pstatus in outcome.platform_status.items():
            if not _is_published_status(pstatus):
                continue
            # Pull the post_id from the outcome — cleaner than re-iterating
            # futures. For platforms in ``prior_published`` the post_id was
            # lost across the run boundary so we fall back to "" (same as
            # the original code's "no matching future" fall-through).
            #
            # W3.2 (2026-06-17): normalize to ``{platform}:{id}`` shape so
            # the join with publishing_analytics.post_id actually matches.
            # Pre-fix prod data shows IG stored as raw numeric (e.g.,
            # ``18067901951361934``) while analytics consistently uses
            # ``instagram:DWQeTghibLu`` — that mismatch broke the
            # reward-attribution join silently, so the bandit never
            # learned from IG-only feedback. Normalizing at write time
            # is the cleanest fix (downstream consumers don't need to
            # de-dupe two formats). Existing rows can be backfilled
            # with a one-off UPDATE if needed; new writes are correct
            # going forward.
            post_id_for_plat = outcome.successful_post_ids.get(plat, "")
            post_id_for_plat = _normalize_post_id(plat, post_id_for_plat)
            bandit_ctx = _build_bandit_context(
                fields,
                niche_id,
                publish_hour=published_at.hour,
                platform=plat,
            )

            # PR U (2026-06-28): IPS propensity wire-through. The
            # LinUCB picker in push_to_backlog._classify_arm_with_
            # propensity writes ``bandit_propensity`` into fields when
            # it actually runs (opt-in env on + LinUCB arms + warm
            # enough). When fields lacks the key, propensity stays
            # None — downstream IPS replay treats NULL as "not
            # applicable" and excludes the row cleanly.
            #
            # Temperature pairs with propensity: only set when
            # propensity is set, and uses the SAME default that
            # ``linucb_picker._DETERMINISTIC_TEMPERATURE`` writes (0.5
            # — matches LinUCBBandit.DEFAULT_TEMPERATURE so IPS replay
            # reads both producers under one convention). Keeping
            # them paired guarantees IPS can always reconstruct the
            # selection distribution from the persisted propensity.
            arm_propensity = fields.get("bandit_propensity")
            arm_temperature = 0.5 if arm_propensity is not None else None

            # Task #581 (2026-07-08): transformation arm attribution.
            # ``push_to_backlog`` serializes ``story["arm_ids_by_dimension"]``
            # to JSON and stores under this key. Decode here so
            # ``PendingFeedbackTask.arm_ids_by_dimension`` gets the native
            # dict shape ``{dimension: arm_id}``. Reward router at
            # ``metric_collector.py:1077`` reads this at the 48h collection
            # window and routes per-dimension bandit updates.
            arm_ids_by_dim = _parse_arm_ids_by_dimension(fields.get("arm_ids_by_dimension"))

            task = PendingFeedbackTask(
                content_id=candidate_id or record_id[:16],
                platform=plat,
                niche_id=niche_id,
                published_at=published_at,
                platform_post_id=post_id_for_plat,
                content_type="video",
                hook_text=fields.get("hook", "")[:100],
                hook_length=len(fields.get("hook", "")),
                bandit_arm=fields.get("arm_id", ""),
                bandit_context=bandit_ctx,
                propensity=arm_propensity,
                temperature=arm_temperature,
                arm_ids_by_dimension=arm_ids_by_dim,
            )
            fb_store.create(task)
    except Exception as e:
        logger.warning("[publish] PendingFeedback registration failed (non-fatal): %s", e)


def _parse_arm_ids_by_dimension(raw: Any) -> dict[str, str]:
    """Decode ``fields["arm_ids_by_dimension"]`` back to native dict.

    ``push_to_backlog`` writes this as a JSON string; the DB backend
    may return it as either str (Postgres text col) or dict (JSONB
    col after psycopg auto-decoding), so accept both shapes. Empty /
    malformed inputs return ``{}`` — matches the
    ``PendingFeedbackTask.arm_ids_by_dimension`` default, so a missing
    or corrupt value cleanly means "no transformation arms attributed
    for this publish" and the router skips.

    Task #581 (2026-07-08) — see docstring of
    ``register_pending_feedback``.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        # Coerce values to str because bandit_arms.arm_id is a text
        # column and the router does string equality lookups.
        return {str(k): str(v) for k, v in raw.items() if k and v}
    if isinstance(raw, str):
        try:
            import json as _json

            parsed = _json.loads(raw)
        except (ValueError, TypeError):
            # Task #633 (2026-07-09 late evening): elevated DEBUG →
            # WARNING. Silent JSON-decode failure here loses arm
            # attribution for the entire publish — same class of bug
            # as motion_compositor's silent asset-miss (#631). If
            # push_to_backlog serialized a malformed
            # arm_ids_by_dimension JSON, we want to see it at ops
            # level, not have it silently disappear at DEBUG.
            logger.warning(
                "[publish] arm_ids_by_dimension JSON decode failed — "
                "transformation arm attribution will be discarded for "
                "this publish. raw=%r",
                raw[:80],
            )
            return {}
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items() if k and v}
    return {}


def _normalize_post_id(platform: str, post_id: str) -> str:
    """Return the post_id in canonical ``{platform}:{id}`` form.

    publishing_analytics consistently writes the prefixed shape, but
    some platform publishers (notably IG, which returns the bare
    Graph-API numeric id) historically wrote the bare id to
    pending_feedback. That asymmetry broke the
    ``pending_feedback.post_id ↔ analytics.post_id`` join, so the
    bandit + hook classifier silently lost feedback for IG-only
    publishes.

    Task #624 (2026-07-09) — delegates to
    ``genlab_core.cache.post_id_norm.normalize_post_id`` so the
    contract is defined in one place. The re-export here preserves
    the existing internal-caller import path
    (``from feedback_registration import _normalize_post_id``) and
    the existing test-suite pins.
    """
    # Local import avoids a hard dependency cycle risk in the
    # ``publishing`` layer at module-load time. cache/ is a leaf
    # module with no genlab-core imports so this is a cheap call.
    from genlab_core.cache.post_id_norm import normalize_post_id

    return normalize_post_id(platform, post_id)


def _is_published_status(pstatus: Any) -> bool:
    """Accept both the bare-string (``"PUBLISHED"``) and dict
    (``{"status": "PUBLISHED"}``) shapes that platform_publish_status
    can carry — partial-publish recovery paths mix both over a
    blueprint's lifetime."""
    if pstatus == "PUBLISHED":
        return True
    return isinstance(pstatus, dict) and pstatus.get("status") == "PUBLISHED"


def _build_bandit_context(
    fields: dict[str, Any],
    niche_id: str,
    *,
    publish_hour: int | None = None,
    platform: str | None = None,
) -> dict | None:
    """Build the ``bandit_context`` dict the learning-loop's
    metric_collector update expects.

    Carries:

    * ``hook_features`` — text features for the hook classifier.
    * ``linucb_context`` — 6D feature vector for the LinUCB bandit.
    * ``extra_arms`` — additional bandit arms that receive the SAME
      reward as the primary content arm via metric_collector's
      multi-arm credit pass. Two arm shapes get added when present:
        - ``style:{niche}:{name}``    — hook style (Break-11 fix)
        - ``hour:{H}:{platform}:{niche}`` — UTC publish hour (PR Z,
          2026-06-23). Foundation for the optimal-time bandit:
          captures publish-hour data so future runs can sample from
          Thompson Sampling posteriors per (hour, platform, niche)
          tuple. Read side is env-gated in optimal_time_learner.

    Returns ``None`` on any failure — keeps PendingFeedback registration
    from blocking on a learning-loop import error or a malformed field.

    Args:
        fields: blueprint fields (hook, hook_style, etc.)
        niche_id: niche identifier
        publish_hour: UTC hour of publish (0-23). When provided WITH
            ``platform``, an hour-arm is appended to extra_arms so the
            bandit accumulates per-hour posteriors. Optional for
            backward compat with callers that don't have the hour
            handy yet (tests + legacy paths).
        platform: destination platform string (e.g. "youtube",
            "instagram"). Required for the hour-arm to be added —
            without it the bandit can't distinguish "Tuesday 14:00 on
            YouTube" from "Tuesday 14:00 on Instagram" which have
            very different audience dynamics.
    """
    try:
        from genlab_core.learning.hook_features import build_feature_vector
        from genlab_core.learning.linucb import (
            build_content_context,
            build_content_context_v2,
        )

        hook_txt = fields.get("hook", "")
        hook_feats = build_feature_vector(hook_txt) if hook_txt else {}
        linucb_ctx = build_content_context(fields, niche_id).tolist()
        # Intervention 9 (2026-07-01): persist the v2 vector alongside
        # v1 so ancillary consumers (DR estimator, bandit_validation,
        # ensemble) can opt into cyclical time features without
        # blocking on the LinUCB-v2 retrain. Best-effort — a failure
        # here doesn't compromise the (critical) v1 wire. See
        # ``learning/linucb.build_content_context_v2`` for the layout.
        try:
            linucb_ctx_v2 = build_content_context_v2(fields, niche_id).tolist()
        except Exception as exc:
            # 2026-07-14: was silent `except Exception: linucb_ctx_v2 = None`.
            # v2 build can fail if cyclical-time encoding hits an edge case;
            # v1 wire is unaffected (returned above). But silent failure
            # means Intervention 9 rollout can't detect if v2 is silently
            # broken for any niche. Log at DEBUG (not WARNING) because v2
            # is still observation-only; elevating would spam logs.
            logger.debug(
                "[%s] LinUCB v2 context build failed (v1 unaffected): %s",
                niche_id,
                exc,
            )
            linucb_ctx_v2 = None
        ctx: dict = {
            "hook_features": hook_feats,
            "linucb_context": linucb_ctx,
        }
        if linucb_ctx_v2 is not None:
            ctx["linucb_context_v2"] = linucb_ctx_v2
        extra_arms: list[str] = []

        hook_style = fields.get("hook_style", "")
        if hook_style:
            extra_arms.append(f"style:{niche_id}:{hook_style}")

        # PR Z (2026-06-23): publish-hour arm. Guard against missing
        # platform / out-of-range hour so a malformed call doesn't
        # poison the bandit_arms table with garbage arm_ids that the
        # consumer's prefix-match would then have to filter.
        if (
            publish_hour is not None
            and platform
            and isinstance(publish_hour, int)
            and 0 <= publish_hour <= 23
        ):
            extra_arms.append(f"hour:{publish_hour}:{platform}:{niche_id}")

        if extra_arms:
            ctx["extra_arms"] = extra_arms
        return ctx
    except Exception as exc:
        # Task #633 (2026-07-09 late evening): elevated DEBUG →
        # WARNING. Bandit context includes LinUCB feature vector +
        # extra_arms (style, hour, platform). When this build fails,
        # PendingFeedbackTask.bandit_context stays None →
        # IPS replay excludes the row (no reconstruction possible) →
        # counterfactual analysis silently loses samples. Silent-fail
        # here compounds tonight's motion_compositor lesson: any
        # learning-signal-side failure needs operator visibility.
        logger.warning(
            "[publish] bandit_context build failed (%s) — this publish "
            "will have bandit_context=None; IPS replay + counterfactual "
            "analysis will exclude the row.",
            exc,
        )
        return None
