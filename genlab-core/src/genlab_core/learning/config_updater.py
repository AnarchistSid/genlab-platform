"""Config updater: translates bandit learning into YAML config changes.

Runs weekly via cron (every Monday at 9am IST / 3:30am UTC).
Reads completed 48h PendingFeedbackTask records from the last 30 days,
groups by hook_type and posting_hour, and updates schedule.yaml and
templates.yaml when the learned optimum differs from the current config
by more than CHANGE_THRESHOLD.

Conservative update rules:
  - Never update if n < MIN_DATA_POINTS for that dimension
  - Never update if the change is < CHANGE_THRESHOLD (10%)
  - Log every change (and every skipped update) at INFO level
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CHANGE_THRESHOLD = 0.10  # 10% — minimum difference to justify a config update
# 2026-06-15 audit T#59: lowered from 20 → 5. At current channel reach
# (1-2 reels/niche/day across 5 niches), gathering 20 records per
# (niche, platform) takes 30+ days. The 20-threshold made the
# config_updater appear "dead" (0 changes/week across all 5 niches) when
# the infrastructure is actually alive — just gated behind an
# unreachable n. Same pattern as PR #201's optimal_time_learner threshold
# rebalance (20 → 5 + cold-start fallback).
#
# Why 5 still meaningful: 5 records at 90% confidence gives the threshold
# room to move only when the signal is real, not noise. Below 5 the
# config_updater wisely no-ops — over-fitting on tiny samples produces
# config-thrash that defeats the learning loop.
MIN_DATA_POINTS = 5
_IST_OFFSET = timedelta(hours=5, minutes=30)  # schedule.yaml uses IST


class ConfigUpdater:
    """Translate bandit learning into conservative YAML config changes."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir

    def run(
        self,
        feedback_records: list,
        dry_run: bool = False,
    ) -> list[dict]:
        """Read completed feedback records, identify better config values,
        and write YAML updates.

        Args:
            feedback_records: PendingFeedbackTask records with
                collection_status == "complete" and reward_48h populated.
            dry_run: If True, log what would change but don't write files.

        Returns:
            List of change dicts: {"file", "key", "old_value", "new_value",
                                   "n", "reason"}
        """
        records_with_reward = [
            r
            for r in feedback_records
            if getattr(r, "reward_48h", None) is not None
            and "48h" in getattr(r, "completed_windows", [])
        ]

        cutoff = datetime.now(tz=UTC) - timedelta(days=30)
        recent = [r for r in records_with_reward if r.published_at >= cutoff]

        if len(recent) < MIN_DATA_POINTS:
            logger.info(
                "[CONFIG_UPDATE] Only %d records in last 30 days (need %d) — skipping",
                len(recent),
                MIN_DATA_POINTS,
            )
            return []

        changes: list[dict] = []
        changes += self._update_posting_schedule(recent, dry_run)
        changes += self._update_hook_type_ratios(recent, dry_run)

        logger.info(
            "[CONFIG_UPDATE] %s %d config change(s)",
            "Would apply" if dry_run else "Applied",
            len(changes),
        )
        return changes

    def _update_posting_schedule(self, records: list, dry_run: bool) -> list[dict]:
        """Compare average reward by posting hour per platform.
        Update schedule.yaml if the best hour differs from current by > 10%.
        """
        schedule_path = self._config_dir / "schedule.yaml"
        if not schedule_path.exists():
            logger.info("[CONFIG_UPDATE] schedule.yaml not found — skipping schedule update")
            return []

        with open(schedule_path) as f:
            schedule = yaml.safe_load(f) or {}

        rewards_by_slot: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        for r in records:
            hour = (r.published_at + _IST_OFFSET).hour
            rewards_by_slot[r.platform][hour].append(r.reward_48h)

        changes: list[dict] = []
        for platform, hour_rewards in rewards_by_slot.items():
            best_hour = max(
                hour_rewards,
                key=lambda h: sum(hour_rewards[h]) / len(hour_rewards[h]),
            )
            n = len(hour_rewards[best_hour])
            if n < MIN_DATA_POINTS:
                logger.info(
                    "[CONFIG_UPDATE] %s best_hour=%02d:00 only has n=%d — skipping (need %d)",
                    platform,
                    best_hour,
                    n,
                    MIN_DATA_POINTS,
                )
                continue

            best_avg = sum(hour_rewards[best_hour]) / n
            current_hour = self._get_current_posting_hour(schedule, platform)
            if current_hour is None:
                continue

            clock_diff = min(abs(best_hour - current_hour), 24 - abs(best_hour - current_hour))
            if clock_diff < CHANGE_THRESHOLD * 24:  # 10% of 24h = 2.4h
                logger.info(
                    "[CONFIG_UPDATE] %s hour change %02d->%02d small (%dh) — skipping",
                    platform,
                    current_hour,
                    best_hour,
                    clock_diff,
                )
                continue

            change = {
                "file": "schedule.yaml",
                "key": f"posting_slot.{platform}",
                "old_value": f"{current_hour:02d}:00",
                "new_value": f"{best_hour:02d}:00",
                "n": n,
                "reason": f"avg_reward={best_avg:.4f} at hour {best_hour:02d}:00",
            }
            logger.info(
                "[CONFIG_UPDATE] schedule.yaml: posting_slot %s %s -> %s (n=%d, avg_reward=%.4f)",
                platform,
                change["old_value"],
                change["new_value"],
                n,
                best_avg,
            )
            changes.append(change)

            if not dry_run:
                self._write_posting_hour(schedule_path, schedule, platform, best_hour)

        return changes

    def _update_hook_type_ratios(self, records: list, dry_run: bool) -> list[dict]:
        """Compare average reward by hook_type.
        Update templates.yaml hook_type_ratios if learned distribution differs.
        """
        templates_path = self._config_dir / "templates.yaml"
        if not templates_path.exists():
            logger.info("[CONFIG_UPDATE] templates.yaml not found — skipping hook ratio update")
            return []

        with open(templates_path) as f:
            templates = yaml.safe_load(f) or {}

        rewards_by_hook: dict[str, list[float]] = defaultdict(list)
        for r in records:
            hook_type = getattr(r, "hook_type", None) or "unknown"
            rewards_by_hook[hook_type].append(r.reward_48h)

        if not rewards_by_hook:
            return []

        mean_rewards = {
            ht: sum(rewards) / len(rewards)
            for ht, rewards in rewards_by_hook.items()
            if len(rewards) >= MIN_DATA_POINTS
        }
        if not mean_rewards:
            logger.info(
                "[CONFIG_UPDATE] No hook type has n>=%d — skipping hook ratio update",
                MIN_DATA_POINTS,
            )
            return []

        total = sum(mean_rewards.values())
        if total < 1e-6:
            logger.info(
                "[CONFIG_UPDATE] All hook types near-zero reward — skipping hook ratio update"
            )
            return []
        optimal_ratios = {ht: v / total for ht, v in mean_rewards.items()}

        current_ratios = templates.get("hook_type_ratios", {})
        changes: list[dict] = []
        for hook_type, optimal_ratio in optimal_ratios.items():
            current_ratio = current_ratios.get(hook_type, 0.0)
            if abs(optimal_ratio - current_ratio) < CHANGE_THRESHOLD:
                continue
            n = len(rewards_by_hook[hook_type])
            change = {
                "file": "templates.yaml",
                "key": f"hook_type_ratios.{hook_type}",
                "old_value": round(current_ratio, 3),
                "new_value": round(optimal_ratio, 3),
                "n": n,
                "reason": f"mean_reward={mean_rewards[hook_type]:.4f}",
            }
            logger.info(
                "[CONFIG_UPDATE] templates.yaml: hook_type_ratios.%s %.3f -> %.3f (n=%d)",
                hook_type,
                current_ratio,
                optimal_ratio,
                n,
            )
            changes.append(change)

        if not dry_run and changes:
            new_ratios = {
                **current_ratios,
                **{c["key"].split(".")[-1]: c["new_value"] for c in changes},
            }
            self._write_yaml_key(templates_path, templates, "hook_type_ratios", new_ratios)

        return changes

    def _get_current_posting_hour(self, schedule: dict, platform: str) -> int | None:
        """Extract the current posting hour for a platform from schedule.yaml."""
        slots = schedule.get("posting_slots", {}).get(platform, [])
        if not slots:
            return None
        first_slot = slots[0] if isinstance(slots, list) else slots
        if isinstance(first_slot, str) and ":" in first_slot:
            return int(first_slot.split(":")[0])
        return None

    def _write_posting_hour(self, path: Path, schedule: dict, platform: str, new_hour: int) -> None:
        """Write the updated posting hour back to schedule.yaml."""
        slots = schedule.get("posting_slots", {}).get(platform, [])
        if slots and isinstance(slots[0], str) and ":" in slots[0]:
            old_minute = slots[0].split(":")[1]
            slots[0] = f"{new_hour:02d}:{old_minute}"
        self._write_yaml_key(path, schedule, f"posting_slots.{platform}", slots)

    def _write_yaml_key(self, path: Path, data: dict, key: str, value: Any) -> None:
        """Write a nested YAML key using dot notation.
        Uses atomic tmp+rename to avoid corrupting YAML on crash.
        """
        keys = key.split(".")
        target = data
        for k in keys[:-1]:
            target = target.setdefault(k, {})
        target[keys[-1]] = value

        tmp_path = path.with_suffix(".yaml.tmp")
        with open(tmp_path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        tmp_path.replace(path)

        logger.info("[CONFIG_UPDATE] Wrote %s=%r to %s", key, value, path)
