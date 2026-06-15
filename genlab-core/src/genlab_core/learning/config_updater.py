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

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

# Roundtrip-preserving YAML loader/dumper. ``typ="rt"`` preserves
# comments, blank lines, and key order in the loaded structure so a
# load → mutate → dump cycle on an existing file keeps the human-
# written commentary intact. PyYAML's yaml.dump strips ALL of this —
# every config_updater run silently rewrote 3 prod templates.yaml
# files losing their comments (audit T#62, fix T#69 2026-06-15).
#
# Module-level singleton: ruamel.yaml stores per-instance defaults
# (indent, width, mapping flow style), and constructing a fresh
# instance per call would discard any tuning we add later.
_RT_YAML = YAML(typ="rt")
_RT_YAML.preserve_quotes = True
_RT_YAML.default_flow_style = False
_RT_YAML.allow_unicode = True
# ruamel.yaml defaults to width=80 which mangles long inline lists;
# 4096 is "effectively never wrap" without going to sys.maxsize
# (some versions reject very large widths).
_RT_YAML.width = 4096


def _rt_load(path: Path) -> Any:
    """Load a YAML file with comments + structure preserved.

    Returns the parsed structure (typically a ``CommentedMap``) or an
    empty ``dict`` for missing/empty files. The returned object can
    be mutated like a regular dict; subsequent ``_rt_dump`` will
    preserve any comments that survived the mutation.
    """
    if not path.exists():
        return {}
    with open(path) as f:
        loaded = _RT_YAML.load(f)
    return loaded if loaded is not None else {}


def _rt_dump(path: Path, data: Any) -> None:
    """Dump ``data`` to ``path`` atomically using roundtrip mode."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        _RT_YAML.dump(data, f)
    tmp_path.replace(path)

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

        schedule = _rt_load(schedule_path)

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

        templates = _rt_load(templates_path)

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
            # MUTATE the existing CommentedMap in place rather than
            # building a new plain dict — ruamel.yaml's comment metadata
            # attaches to the loaded mapping object, and replacing it
            # with `{**current_ratios, ...}` would silently strip every
            # inline comment on the surviving keys (T#69 regression).
            #
            # If hook_type_ratios doesn't exist yet, fall back to
            # creating it; new keys legitimately have no prior comments
            # to preserve.
            if "hook_type_ratios" not in templates:
                templates["hook_type_ratios"] = {}
            ratios = templates["hook_type_ratios"]
            for c in changes:
                key = c["key"].split(".")[-1]
                ratios[key] = c["new_value"]
            _rt_dump(templates_path, templates)
            logger.info(
                "[CONFIG_UPDATE] Wrote %d hook_type_ratios update(s) to %s",
                len(changes),
                templates_path,
            )

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

        Uses atomic tmp+rename to avoid corrupting YAML on crash. The
        dump path goes through ``_rt_dump`` (ruamel.yaml roundtrip
        mode) so existing comments + key order + blank lines survive
        the write — PyYAML's yaml.dump would strip them all (T#69).

        Note: ``data`` must have been loaded via ``_rt_load`` for
        comment preservation to actually work; a plain dict carries
        no comment metadata to round-trip. The callers in this
        module all use ``_rt_load`` so the contract holds.
        """
        keys = key.split(".")
        target = data
        for k in keys[:-1]:
            target = target.setdefault(k, {})
        target[keys[-1]] = value

        _rt_dump(path, data)
        logger.info("[CONFIG_UPDATE] Wrote %s=%r to %s", key, value, path)
