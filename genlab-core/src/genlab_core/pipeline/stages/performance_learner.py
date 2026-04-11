"""Pipeline stage: Update bandit posteriors (Thompson Sampling + LinUCB).

Post-analytics stage that reads engagement metrics from context['stories']
and updates bandit arms stored in SharePoint.

Each "arm" represents a content strategy dimension (hook_formula,
template_type, posting_time_slot, etc.). When engagement data arrives,
we compute a reward signal and update the appropriate model:

  - Arms with >= MIN_OBS_FOR_LINUCB observations: LinUCB contextual update
    (A += x x^T, b += reward * x)
  - Arms below threshold: Thompson Sampling update
    (success: alpha += 1, failure: beta += 1)

Uses genlab_core.learning.arm_loader for SharePoint CRUD,
genlab_core.learning.linucb for contextual bandit updates, and
genlab_core.learning.reward_shaper for reward computation.

Non-fatal: learning failures are logged but never block publishing.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Minimum engagement data points before updating
MIN_STORIES_WITH_DATA = 1


class PerformanceLearner:
    """Log learning loop status to run_stats.

    NOTE: Bandit updates are now handled by metric_collector via the
    _default_bandit_updater callback (Break 5/8/9 fix). This stage
    only logs the current bandit state for observability — it no longer
    directly updates bandit_arms. The metric_collector runs every 60min
    and feeds rewards to bandits when 48h metrics arrive.

    Reads: context['stories'], context['niche_config'], context['run_stats']
    Writes: context['run_stats']['learning']
    """

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        stories = context.get("stories", [])
        niche_config = context.get("niche_config", {})
        niche_id = niche_config.get("niche_id", "unknown")

        # First check current run's stories for engagement data
        with_engagement = [
            s for s in stories
            if s.get("engagement") and any(
                platform_data.get("metrics")
                for platform_data in s["engagement"].values()
                if isinstance(platform_data, dict)
            )
        ]

        # If no engagement in current stories, check Analytics for PREVIOUSLY
        # published posts with engagement data (collected by metric_collector).
        # Join through publishing_analytics → blueprints to recover arm_id,
        # which analytics records don't store directly.
        if len(with_engagement) < MIN_STORIES_WITH_DATA:
            try:
                client = context.get("_backlog_client")
                if client is None:
                    from genlab_core.http.backlog_client import BacklogClient
                    client = BacklogClient()
                    context["_backlog_client"] = client

                # Direct DB query to join analytics → publishing_analytics → blueprints
                # for arm_id recovery. The ORM doesn't support joins, so use raw SQL.
                _pg = getattr(client, "_pg", None)
                if _pg is not None:
                    import psycopg
                    pool = _pg._get_pool()
                    with pool.connection() as conn:
                        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
                            cur.execute("""
                                SELECT a.post_id, a.niche_id, a.platform,
                                       a.extra->>'engagement_rate' as engagement_rate,
                                       a.extra->>'likes' as likes,
                                       a.extra->>'comments' as comments,
                                       a.extra->>'plays' as plays,
                                       a.extra->>'shares' as shares,
                                       b.arm_id, b.candidate_id
                                FROM analytics a
                                JOIN publishing_analytics pa ON pa.post_id = a.post_id
                                JOIN blueprints b ON b.id = pa.blueprint_id
                                WHERE a.niche_id = %s
                                  AND b.arm_id IS NOT NULL
                                  AND a.extra->>'engagement_rate' IS NOT NULL
                                ORDER BY a.created_at DESC
                                LIMIT 50
                            """, (niche_id,))
                            for row in cur.fetchall():
                                er = float(row["engagement_rate"] or 0)
                                with_engagement.append({
                                    "story_id": row["candidate_id"] or "",
                                    "arm_id": row["arm_id"],
                                    "engagement": {
                                        row["platform"]: {
                                            "metrics": {
                                                "engagement_rate": er,
                                                "likes": int(row["likes"] or 0),
                                                "comments": int(row["comments"] or 0),
                                                "views": int(row["plays"] or 0),
                                                "shares": int(row["shares"] or 0),
                                            }
                                        }
                                    },
                                })
                    logger.info(
                        "[PerformanceLearner] Loaded %d analytics records with arm_id for %s",
                        len(with_engagement), niche_id,
                    )
                else:
                    # Fallback: load from analytics without arm_id (original behavior)
                    analytics = client.analytics.all(max_records=50)
                    for a in analytics:
                        f = a.get("fields", a)
                        if (str(f.get("niche_id", "")) == niche_id and
                                f.get("engagement_rate") is not None):
                            with_engagement.append({
                                "story_id": f.get("candidate_id", ""),
                                "hook_formula": f.get("hook_formula", ""),
                                "template_id": f.get("template_id", ""),
                                "engagement": {
                                    f.get("platform", "unknown"): {
                                        "metrics": {"engagement_rate": f.get("engagement_rate", 0)}
                                    }
                                },
                            })
            except Exception as exc:
                logger.warning("[PerformanceLearner] Analytics check failed: %s", exc)

        if len(with_engagement) < MIN_STORIES_WITH_DATA:
            logger.info(
                "[PerformanceLearner] %d stories with engagement (need %d), skipping",
                len(with_engagement), MIN_STORIES_WITH_DATA,
            )
            return context

        # Late imports — these modules need SharePoint/Graph credentials
        try:
            from genlab_core.learning.arm_loader import (
                BANDIT_LIST_NAMES,
                load_all_arms_extended,
                save_arm,
            )
            from genlab_core.learning.reward_shaper import RewardShaper
        except ImportError:
            logger.warning("[PerformanceLearner] Learning modules unavailable, skipping")
            return context

        reward_shaper = RewardShaper()

        # Check if this niche has bandit infrastructure
        list_name = BANDIT_LIST_NAMES.get(niche_id)
        if not list_name:
            logger.info("[PerformanceLearner] No bandit list for niche %s", niche_id)
            return context

        updates_made = 0
        linucb_updates = 0
        thompson_updates = 0
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

            # Load extended arm data (includes LinUCB state)
            arms_extended = load_all_arms_extended(proxy, niche_id)
            if not arms_extended:
                logger.info("[PerformanceLearner] No bandit arms found for %s", niche_id)
                context.setdefault("run_stats", {})["learning"] = {
                    "status": "no_arms",
                }
                return context

            # Try to import LinUCB; if numpy not available, fall back entirely
            linucb_available = False
            try:
                from genlab_core.learning.linucb import (
                    CONTEXT_DIM,
                    MIN_OBS_FOR_LINUCB,
                    LinUCBArm,
                    build_content_context,
                )
                linucb_available = True
            except ImportError:
                logger.info(
                    "[PerformanceLearner] LinUCB unavailable (numpy?), using Thompson only"
                )

            # Compute rewards and update arms
            for story in with_engagement:
                try:
                    # Extract per-platform metrics and compute reward
                    # RewardShaper.compute_reward(platform, metrics)
                    engagement = story.get("engagement", {})
                    _platform = ""
                    _metrics: dict = {}
                    for plat, plat_data in engagement.items():
                        if isinstance(plat_data, dict) and plat_data.get("metrics"):
                            _platform = plat
                            _metrics = plat_data["metrics"]
                            break
                    reward = reward_shaper.compute_reward(_platform, _metrics)
                    arm_ids = self._extract_arm_ids(story)

                    # Build context vector for LinUCB (once per story)
                    context_vector = None
                    if linucb_available:
                        try:
                            context_vector = build_content_context(story, niche_id)
                        except Exception as exc:
                            logger.debug(
                                "[PerformanceLearner] Context build failed for %s",
                                story.get("story_id", "unknown"),
                            )

                    for arm_id in arm_ids:
                        if arm_id not in arms_extended:
                            continue

                        arm_data = arms_extended[arm_id]
                        alpha = arm_data["alpha"]
                        beta = arm_data["beta"]
                        linucb_state = arm_data.get("linucb_state")

                        # Restore or initialize LinUCB arm
                        linucb_arm = None
                        if linucb_available and linucb_state is not None:
                            try:
                                linucb_arm = LinUCBArm.from_dict(linucb_state)
                            except Exception as exc:
                                logger.debug(
                                    "[PerformanceLearner] Failed to restore LinUCB "
                                    "state for %s, using Thompson",
                                    arm_id,
                                )
                        elif linucb_available and context_vector is not None:
                            # No existing LinUCB state — initialize fresh arm
                            linucb_arm = LinUCBArm(d=CONTEXT_DIM, alpha=1.0)

                        # Decide update strategy based on observation count
                        used_linucb = False
                        if (linucb_arm is not None
                                and context_vector is not None
                                and linucb_arm.n_obs >= MIN_OBS_FOR_LINUCB):
                            # LinUCB update: use contextual information
                            linucb_arm.update(context_vector, reward)
                            arm_data["linucb_state"] = linucb_arm.to_dict()
                            linucb_updates += 1
                            used_linucb = True
                            logger.debug(
                                "[PerformanceLearner] LinUCB update: arm=%s "
                                "reward=%.3f n_obs=%d",
                                arm_id, reward, linucb_arm.n_obs,
                            )
                        elif linucb_arm is not None and context_vector is not None:
                            # Below threshold: still accumulate LinUCB observations
                            # for eventual activation, but use Thompson for decisions
                            linucb_arm.update(context_vector, reward)
                            arm_data["linucb_state"] = linucb_arm.to_dict()

                        # Always update Thompson Sampling posteriors (alpha/beta)
                        # as the cold-start fallback
                        if reward >= 0.5:
                            alpha += 1.0
                        else:
                            beta += 1.0
                        arm_data["alpha"] = alpha
                        arm_data["beta"] = beta

                        if used_linucb:
                            thompson_updates -= 0  # counted as linucb
                        else:
                            thompson_updates += 1

                        updates_made += 1

                except Exception as exc:
                    logger.exception(
                        "[PerformanceLearner] Reward computation failed for %s",
                        story.get("story_id", "unknown"),
                    )
                    errors += 1

            # Write updated arms back to SharePoint
            if updates_made > 0:
                self._write_arms_extended(proxy, arms_extended, save_arm)

        except Exception as exc:
            logger.exception("[PerformanceLearner] Failed to update bandits for %s", niche_id)
            errors += 1

        logger.info(
            "[PerformanceLearner] %s: %d arm updates (%d LinUCB, %d Thompson), %d errors",
            niche_id, updates_made, linucb_updates, thompson_updates, errors,
        )

        context.setdefault("run_stats", {})["learning"] = {
            "niche_id": niche_id,
            "stories_with_engagement": len(with_engagement),
            "arm_updates": updates_made,
            "linucb_updates": linucb_updates,
            "thompson_updates": thompson_updates,
            "errors": errors,
        }

        return context

    @staticmethod
    def _get_proxy(niche_id: str, list_name: str):
        """Get a GraphTableProxy for the bandit arms list via BacklogClient."""
        try:
            from genlab_core.http.backlog_client import BacklogClient
            client = BacklogClient()
            proxy = client.bandit_arms
            if proxy is None:
                logger.error(
                    "[PerformanceLearner] BacklogClient has no bandit_arms proxy"
                )
                return None
            return proxy
        except Exception as exc:
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

    @staticmethod
    def _write_arms_extended(
        proxy,
        arms: dict[str, dict[str, Any]],
        save_arm_fn,
    ) -> None:
        """Write updated arm posteriors (with LinUCB state) back to SharePoint."""
        for arm_id, arm_data in arms.items():
            try:
                save_arm_fn(
                    proxy,
                    arm_id=arm_id,
                    alpha=arm_data["alpha"],
                    beta=arm_data["beta"],
                    linucb_state=arm_data.get("linucb_state"),
                )
            except Exception as exc:
                logger.exception(
                    "[PerformanceLearner] Failed to update arm %s", arm_id,
                )

