"""Auto-config write-back: applies bandit learning to YAML configuration files.

Writes back:
  publishing.yaml → schedule_slots per platform (±2h bounds from current)
  templates.yaml  → content_type ratios IF ratio field exists (10–50% bounds)

Safety rules:
  - Never changes posting times by more than ±2 hours per update cycle
  - Never moves posting times outside 06:00–23:00 local time
  - Requires MIN_OBSERVATIONS_PER_ARM (50) before trusting any arm's posterior
  - Writes atomically: write to .tmp then rename (no partial YAML)
  - Logs every change with before/after values
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safety bounds
# ---------------------------------------------------------------------------

MIN_OBSERVATIONS_PER_ARM = 50
MAX_SHIFT_HOURS = 2
ALLOWED_HOUR_RANGE = (6, 23)  # Never schedule before 06:00 or after 23:00
MIN_RATIO = 0.10  # 10% floor for any content type ratio
MAX_RATIO = 0.50  # 50% ceiling for any content type ratio


# ---------------------------------------------------------------------------
# YAML I/O — atomic writes
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return its contents as a dict."""
    with open(path) as f:
        data = yaml.safe_load(f)
    return data if isinstance(data, dict) else {}


def _save_yaml_atomic(path: Path, data: dict[str, Any]) -> None:
    """Write YAML atomically: write to .tmp then rename.

    This guarantees no partial writes — the file is either fully updated
    or untouched.
    """
    tmp_path = path.with_suffix(".yaml.tmp")
    with open(tmp_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    shutil.move(str(tmp_path), str(path))


# ---------------------------------------------------------------------------
# Schedule updates (publishing.yaml)
# ---------------------------------------------------------------------------

def update_schedule_from_bandit(
    publishing_yaml_path: Path,
    niche_id: str,
    optimal_hours: dict[str, int],
    observation_counts: dict[str, int],
) -> bool:
    """Shift schedule_slots toward bandit-learned optimal hours.

    Args:
        publishing_yaml_path: Path to publishing.yaml.
        niche_id: Niche identifier (for logging).
        optimal_hours: ``{platform: best_hour_local}`` from bandit posterior.
        observation_counts: ``{platform: n_observations}`` for gating.

    Returns:
        True if any slot was changed and the file was written.
    """
    config = _load_yaml(publishing_yaml_path)
    changed = False

    for platform, best_hour in optimal_hours.items():
        n_obs = observation_counts.get(platform, 0)
        if n_obs < MIN_OBSERVATIONS_PER_ARM:
            logger.debug(
                "[config_writer] %s/%s: only %d observations (need %d) — skip",
                niche_id, platform, n_obs, MIN_OBSERVATIONS_PER_ARM,
            )
            continue

        platform_cfg = config.get(platform, {})
        if not isinstance(platform_cfg, dict):
            continue
        slots = platform_cfg.get("schedule_slots", [])
        if not slots:
            continue

        new_slots: list[str] = []
        for slot_str in slots:
            if not isinstance(slot_str, str) or ":" not in slot_str:
                new_slots.append(slot_str)
                continue

            current_h = int(slot_str.split(":")[0])
            raw_shift = best_hour - current_h
            shift = max(-MAX_SHIFT_HOURS, min(MAX_SHIFT_HOURS, raw_shift))
            new_h = current_h + shift
            # Clamp to allowed range
            new_h = max(ALLOWED_HOUR_RANGE[0], min(ALLOWED_HOUR_RANGE[1], new_h))
            new_slot = f"{new_h:02d}:00"

            if new_slot != slot_str:
                logger.info(
                    "[config_writer] %s/%s schedule: %s → %s (optimal=%02d, n=%d)",
                    niche_id, platform, slot_str, new_slot, best_hour, n_obs,
                )
                changed = True
            new_slots.append(new_slot)

        platform_cfg["schedule_slots"] = new_slots

    if changed:
        _save_yaml_atomic(publishing_yaml_path, config)
    return changed


# ---------------------------------------------------------------------------
# Ratio clamping
# ---------------------------------------------------------------------------

def _clamp_and_normalise(ratios: dict[str, float]) -> dict[str, float]:
    """Clamp ratios to [MIN_RATIO, MAX_RATIO] while summing to 1.0.

    Uses iterative redistribution: fix any arms at their bound, then
    redistribute the remaining budget proportionally among free arms.
    Converges in at most len(ratios) iterations.
    """
    n = len(ratios)
    if n == 0:
        return {}
    if n == 1:
        # Single arm always gets 1.0 (clamping is meaningless for solo)
        return {k: 1.0 for k in ratios}

    result = dict(ratios)
    fixed: set[str] = set()

    for _ in range(n + 1):
        changed = False
        free_keys = [k for k in result if k not in fixed]
        if not free_keys:
            break

        # Compute budget available for free arms
        fixed_sum = sum(result[k] for k in fixed)
        remaining = 1.0 - fixed_sum
        free_total = sum(result[k] for k in free_keys)

        if free_total < 1e-9:
            # Distribute equally among free arms
            each = remaining / len(free_keys)
            for k in free_keys:
                result[k] = each
        else:
            # Scale free arms to fill remaining budget
            scale = remaining / free_total
            for k in free_keys:
                result[k] = result[k] * scale

        # Clamp and fix
        for k in free_keys:
            if result[k] < MIN_RATIO:
                result[k] = MIN_RATIO
                fixed.add(k)
                changed = True
            elif result[k] > MAX_RATIO:
                result[k] = MAX_RATIO
                fixed.add(k)
                changed = True

        if not changed:
            break

    return result


# ---------------------------------------------------------------------------
# Template ratio updates (templates.yaml)
# ---------------------------------------------------------------------------

def update_templates_from_bandit(
    templates_yaml_path: Path,
    niche_id: str,
    arm_posteriors: dict[str, tuple[float, float]],
    observation_counts: dict[str, int],
) -> bool:
    """Rebalance content type ratios if ratio fields exist in templates.yaml.

    If no template has a ``ratio`` field, logs a debug message and returns
    False. This makes the function forward-compatible for niches that add
    ratio fields later.

    Args:
        templates_yaml_path: Path to templates.yaml.
        niche_id: Niche identifier (for logging).
        arm_posteriors: ``{content_type: (alpha, beta)}`` from Thompson
            Sampling bandit. The mean of each Beta posterior is
            ``alpha / (alpha + beta)``.
        observation_counts: ``{content_type: n_observations}`` for gating.

    Returns:
        True if any ratio was changed and the file was written.
    """
    config = _load_yaml(templates_yaml_path)
    templates = config.get("templates", [])

    if not isinstance(templates, list):
        logger.debug("[config_writer] %s templates is not a list — skip", niche_id)
        return False

    # Check if any template has a ratio field
    has_ratios = any(
        isinstance(t, dict) and "ratio" in t
        for t in templates
    )
    if not has_ratios:
        logger.debug(
            "[config_writer] %s templates.yaml has no ratio fields — skip", niche_id,
        )
        return False

    # Compute posterior means for arms with enough observations
    eligible_means: dict[str, float] = {}
    for content_type, (alpha, beta) in arm_posteriors.items():
        n_obs = observation_counts.get(content_type, 0)
        if n_obs < MIN_OBSERVATIONS_PER_ARM:
            logger.debug(
                "[config_writer] %s content_type=%s: only %d observations (need %d) — skip",
                niche_id, content_type, n_obs, MIN_OBSERVATIONS_PER_ARM,
            )
            continue
        mean = alpha / (alpha + beta) if (alpha + beta) > 0 else 0.0
        eligible_means[content_type] = mean

    if not eligible_means:
        return False

    # Normalise means into ratios
    total_mean = sum(eligible_means.values())
    if total_mean < 1e-9:
        return False
    raw_ratios = {ct: m / total_mean for ct, m in eligible_means.items()}

    # Iterative clamp-and-redistribute: guarantees all ratios in [MIN, MAX]
    # while summing to 1.0. Fixes arms at their bound once clamped, then
    # redistributes the remainder among free arms. Converges in ≤ N passes.
    final_ratios = _clamp_and_normalise(raw_ratios)
    final_ratios = {ct: round(v, 3) for ct, v in final_ratios.items()}

    # Apply to templates that have a ratio field and matching content_type
    changed = False
    for tmpl in templates:
        if not isinstance(tmpl, dict) or "ratio" not in tmpl:
            continue
        # Match by content_type, format, or template_id
        ct_key = (
            tmpl.get("content_type")
            or tmpl.get("format")
            or tmpl.get("template_id", "")
        )
        if ct_key in final_ratios:
            old_ratio = tmpl["ratio"]
            new_ratio = final_ratios[ct_key]
            if old_ratio != new_ratio:
                logger.info(
                    "[config_writer] %s template %s ratio: %.3f → %.3f",
                    niche_id,
                    tmpl.get("template_id", ct_key),
                    old_ratio,
                    new_ratio,
                )
                tmpl["ratio"] = new_ratio
                changed = True

    if changed:
        _save_yaml_atomic(templates_yaml_path, config)
    return changed
