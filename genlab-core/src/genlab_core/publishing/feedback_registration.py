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
            bandit_ctx = _build_bandit_context(fields, niche_id)

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


def _normalize_post_id(platform: str, post_id: str) -> str:
    """Return the post_id in canonical ``{platform}:{id}`` form.

    publishing_analytics consistently writes the prefixed shape, but
    some platform publishers (notably IG, which returns the bare
    Graph-API numeric id) historically wrote the bare id to
    pending_feedback. That asymmetry broke the
    ``pending_feedback.post_id ↔ analytics.post_id`` join, so the
    bandit + hook classifier silently lost feedback for IG-only
    publishes.

    Rules:
    - Empty / falsy post_id passes through unchanged (caller's
      existing fallback handling stays the same).
    - Already-prefixed (contains ``:``) passes through — don't
      double-prefix even if the prefix doesn't match ``platform``.
      Trusting the existing prefix preserves the (rare) case where
      a caller intentionally tagged it differently.
    - Bare id → ``{platform}:{id}``.
    """
    if not post_id:
        return post_id
    if ":" in post_id:
        return post_id
    return f"{platform}:{post_id}"


def _is_published_status(pstatus: Any) -> bool:
    """Accept both the bare-string (``"PUBLISHED"``) and dict
    (``{"status": "PUBLISHED"}``) shapes that platform_publish_status
    can carry — partial-publish recovery paths mix both over a
    blueprint's lifetime."""
    if pstatus == "PUBLISHED":
        return True
    return isinstance(pstatus, dict) and pstatus.get("status") == "PUBLISHED"


def _build_bandit_context(fields: dict[str, Any], niche_id: str) -> dict | None:
    """Build the ``bandit_context`` dict the learning-loop's
    metric_collector update expects.

    Carries:

    * ``hook_features`` — text features for the hook classifier.
    * ``linucb_context`` — 6D feature vector for the LinUCB bandit.
    * ``extra_arms`` — the hook_style arm the picker chose, so the 48h
      reward update can credit the style arm too (Break-11 fix that
      closes the loop opened in commit 84b7801).

    Returns ``None`` on any failure — keeps PendingFeedback registration
    from blocking on a learning-loop import error or a malformed field.
    """
    try:
        from genlab_core.learning.hook_features import build_feature_vector
        from genlab_core.learning.linucb import build_content_context

        hook_txt = fields.get("hook", "")
        hook_feats = build_feature_vector(hook_txt) if hook_txt else {}
        linucb_ctx = build_content_context(fields, niche_id).tolist()
        ctx: dict = {
            "hook_features": hook_feats,
            "linucb_context": linucb_ctx,
        }
        hook_style = fields.get("hook_style", "")
        if hook_style:
            ctx["extra_arms"] = [f"style:{niche_id}:{hook_style}"]
        return ctx
    except Exception as exc:
        logger.debug("[publish] bandit_context build failed: %s", exc)
        return None
