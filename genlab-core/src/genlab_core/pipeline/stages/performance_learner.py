"""Pipeline stage: Update Thompson Sampling bandit posteriors.

Post-analytics stage that reads engagement metrics from context['stories']
and updates the bandit arms (alpha/beta) stored in SharePoint.

Each "arm" represents a content strategy dimension (hook_formula,
template_type, posting_time_slot, etc.). When engagement data arrives,
we compute a reward signal and update the Beta distribution:
  - success: alpha += 1
  - failure: beta += 1

Uses genlab_core.learning.arm_loader for SharePoint CRUD and
genlab_core.learning.reward_shaper for reward computation.

Non-fatal: learning failures are logged but never block publishing.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Minimum engagement data points before updating
MIN_STORIES_WITH_DATA = 1


class PerformanceLearner:
    """Update Thompson Sampling bandit posteriors from engagement data.

    Reads: context['stories'], context['niche_config'], context['run_stats']
    Writes: context['run_stats']['learning']
    """

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        stories = context.get("stories", [])
        niche_config = context.get("niche_config", {})
        niche_id = niche_config.get("niche_id", "unknown")

        # Collect stories with engagement data
        with_engagement = [
            s for s in stories
            if s.get("engagement") and any(
                platform_data.get("metrics")
                for platform_data in s["engagement"].values()
                if isinstance(platform_data, dict)
            )
        ]

        if len(with_engagement) < MIN_STORIES_WITH_DATA:
            logger.info(
                "[PerformanceLearner] %d stories with engagement (need %d), skipping",
                len(with_engagement), MIN_STORIES_WITH_DATA,
            )
            return context

        # Late imports — these modules need SharePoint/Graph credentials
        try:
            from genlab_core.learning.arm_loader import load_all_arms, BANDIT_LIST_NAMES
            from genlab_core.learning.reward_shaper import compute_reward
        except ImportError:
            logger.warning("[PerformanceLearner] Learning modules unavailable, skipping")
            return context

        # Check if this niche has bandit infrastructure
        list_name = BANDIT_LIST_NAMES.get(niche_id)
        if not list_name:
            logger.info("[PerformanceLearner] No bandit list for niche %s", niche_id)
            return context

        updates_made = 0
        errors = 0

        try:
            # Get proxy for this niche's bandit list
            proxy = self._get_proxy(niche_id, list_name)
            if not proxy:
                logger.warning("[PerformanceLearner] Could not connect to SharePoint")
                context.setdefault("run_stats", {})["learning"] = {
                    "status": "no_proxy",
                }
                return context

            arms = load_all_arms(proxy, niche_id)
            if not arms:
                logger.info("[PerformanceLearner] No bandit arms found for %s", niche_id)
                context.setdefault("run_stats", {})["learning"] = {
                    "status": "no_arms",
                }
                return context

            # Compute rewards and update arms
            for story in with_engagement:
                try:
                    reward = compute_reward(story, niche_config)
                    arm_ids = self._extract_arm_ids(story)

                    for arm_id in arm_ids:
                        if arm_id not in arms:
                            continue

                        alpha, beta = arms[arm_id]
                        if reward >= 0.5:
                            alpha += 1.0
                        else:
                            beta += 1.0
                        arms[arm_id] = (alpha, beta)
                        updates_made += 1

                except Exception:
                    logger.exception(
                        "[PerformanceLearner] Reward computation failed for %s",
                        story.get("story_id", "unknown"),
                    )
                    errors += 1

            # Write updated arms back to SharePoint
            if updates_made > 0:
                self._write_arms(proxy, arms)

        except Exception:
            logger.exception("[PerformanceLearner] Failed to update bandits for %s", niche_id)
            errors += 1

        logger.info(
            "[PerformanceLearner] %s: %d arm updates, %d errors",
            niche_id, updates_made, errors,
        )

        context.setdefault("run_stats", {})["learning"] = {
            "niche_id": niche_id,
            "stories_with_engagement": len(with_engagement),
            "arm_updates": updates_made,
            "errors": errors,
        }

        return context

    @staticmethod
    def _get_proxy(niche_id: str, list_name: str):
        """Get a GraphTableProxy for the bandit arms list."""
        try:
            from genlab_core.http.graph_proxy import GraphTableProxy
            return GraphTableProxy(list_name=list_name)
        except Exception:
            logger.exception("[PerformanceLearner] Proxy creation failed")
            return None

    @staticmethod
    def _extract_arm_ids(story: Dict[str, Any]) -> List[str]:
        """Extract bandit arm IDs from story metadata.

        Arms correspond to strategy dimensions used when creating
        this story's blueprint (hook formula, template, time slot, etc.).
        """
        arm_ids = []

        # Hook formula arm
        hook_formula = story.get("hook_formula", "")
        if hook_formula:
            arm_ids.append(f"hook:{hook_formula}")

        # Template arm
        template_id = story.get("template_id", "")
        if template_id:
            arm_ids.append(f"template:{template_id}")

        # Time slot arm
        time_slot = story.get("scheduled_slot", "")
        if time_slot:
            arm_ids.append(f"slot:{time_slot}")

        return arm_ids

    @staticmethod
    def _write_arms(proxy, arms: Dict[str, tuple]) -> None:
        """Write updated arm posteriors back to SharePoint."""
        for arm_id, (alpha, beta) in arms.items():
            try:
                proxy.update(
                    filter_key="Title",
                    filter_value=arm_id,
                    fields={"Alpha": alpha, "Beta": beta},
                )
            except Exception:
                logger.exception(
                    "[PerformanceLearner] Failed to update arm %s", arm_id,
                )
