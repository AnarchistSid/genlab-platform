"""Pipeline stage: Observability snapshot of bandit posteriors.

This stage is OBSERVATION-ONLY. It reads the current state of
``bandit_arms`` and writes a summary into ``context['run_stats']['learning']``
so the run report shows what the learning loop currently believes.

It does NOT update bandit_arms. That responsibility belongs exclusively to
``metric_collector._default_bandit_updater``, which fires from the
feedback-collector cron when 48h metrics arrive. Having the pipeline stage
also write to bandit_arms caused duplicate updates with mismatched math
(Bugs A + B + G in the 2026-05-16 learning-loop audit).

Reads: context['niche_config']
Writes: context['run_stats']['learning']
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class PerformanceLearner:
    """Observability-only snapshot of bandit posteriors.

    Loads bandit arms for the current niche, computes summary stats
    (arm count, posterior-mean spread, total observations) and writes
    them to run_stats. Never mutates bandit_arms.
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        niche_config = context.get("niche_config", {})
        niche_id = niche_config.get("niche_id", "unknown")

        try:
            from genlab_core.learning.arm_loader import (
                BANDIT_LIST_NAMES,
                load_all_arms_extended,
            )
        except ImportError:
            logger.warning("[PerformanceLearner] arm_loader unavailable, skipping")
            return context

        list_name = BANDIT_LIST_NAMES.get(niche_id)
        if not list_name:
            logger.info("[PerformanceLearner] No bandit list for niche %s", niche_id)
            context.setdefault("run_stats", {})["learning"] = {"status": "no_list"}
            return context

        proxy = self._get_proxy(niche_id, list_name)
        if not proxy:
            context.setdefault("run_stats", {})["learning"] = {"status": "no_proxy"}
            return context

        arms_extended = load_all_arms_extended(proxy, niche_id)
        if not arms_extended:
            context.setdefault("run_stats", {})["learning"] = {"status": "no_arms"}
            return context

        means: list[float] = []
        total_linucb_obs = 0
        for arm_data in arms_extended.values():
            alpha = float(arm_data.get("alpha", 1.0))
            beta = float(arm_data.get("beta", 1.0))
            if (alpha + beta) > 0:
                means.append(alpha / (alpha + beta))
            linucb_state = arm_data.get("linucb_state") or {}
            total_linucb_obs += int(linucb_state.get("n_obs", 0))

        mean_spread = (max(means) - min(means)) if means else 0.0
        learning_snapshot = {
            "status": "observed",
            "niche_id": niche_id,
            "n_arms": len(arms_extended),
            "linucb_total_obs": total_linucb_obs,
            "posterior_mean_max": max(means) if means else 0.0,
            "posterior_mean_min": min(means) if means else 0.0,
            "posterior_mean_spread": mean_spread,
        }

        if mean_spread < 0.05 and total_linucb_obs >= 30:
            logger.warning(
                "[PerformanceLearner] %s: bandit posteriors not discriminating "
                "(spread=%.4f, n_obs=%d) — check reward signal health",
                niche_id,
                mean_spread,
                total_linucb_obs,
            )

        logger.info(
            "[PerformanceLearner] %s snapshot: %d arms, %d linucb_obs, mean_spread=%.4f",
            niche_id,
            len(arms_extended),
            total_linucb_obs,
            mean_spread,
        )

        context.setdefault("run_stats", {})["learning"] = learning_snapshot
        return context

    @staticmethod
    def _get_proxy(niche_id: str, list_name: str):
        """Get a GraphTableProxy for the bandit arms list via BacklogClient."""
        try:
            from genlab_core.http.backlog_client import BacklogClient

            client = BacklogClient()
            proxy = client.bandit_arms
            if proxy is None:
                logger.error("[PerformanceLearner] BacklogClient has no bandit_arms proxy")
                return None
            return proxy
        except Exception:
            logger.exception("[PerformanceLearner] Proxy creation failed")
            return None

    @staticmethod
    def _extract_arm_ids(story: dict[str, Any]) -> list[str]:
        """Extract bandit arm IDs from story metadata.

        Arms correspond to strategy dimensions used when creating
        this story's blueprint (hook formula, template, time slot, etc.).
        The new arm_id field (set by PushToBacklog._classify_arm) is
        the primary source — falls back to legacy hook_formula/template_id.
        """
        arm_ids = []

        # New: direct arm_id from content classification
        arm_id = story.get("arm_id", "")
        if arm_id:
            arm_ids.append(arm_id)

        # Legacy: Hook formula arm
        hook_formula = story.get("hook_formula", "")
        if hook_formula:
            arm_ids.append(f"hook:{hook_formula}")

        # Legacy: Template arm
        template_id = story.get("template_id", "")
        if template_id:
            arm_ids.append(f"template:{template_id}")

        # Legacy: Time slot arm
        time_slot = story.get("scheduled_slot", "")
        if time_slot:
            arm_ids.append(f"slot:{time_slot}")

        return arm_ids
