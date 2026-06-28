"""Per-rationale reward multipliers for operator-reject bandit updates.

This is the wire between PR #607 (click-rationale classifier) and the
bandit-update reward path. PR #607 ships a Claude-Haiku classifier
that tags every operator rejection with one of 6 canonical categories
(``weak_hook``, ``too_generic``, ``unsupported_claim``, ``bad_fit``,
``too_long``, ``low_value``). That category lands in
``auto_approval_calibration.feedback_category`` via the calibration
writer.

Today the categorical signal is **logged but not consumed downstream**.
The bandit's reward path (``reward_shaper.compute_reward`` →
``metric_collector._default_bandit_updater``) treats every operator
rejection identically: a flat negative reward applied uniformly to all
arms. That's lost signal — a hook-quality bandit should weight a
``weak_hook`` rejection HEAVIER on the hook arm (the hook arm was
wrong) and weight a ``bad_fit`` rejection LIGHTER on the hook arm
(the hook was fine; the SOURCE was wrong).

This module exposes ``apply_rationale_multiplier(base_reward, rationale,
arm_kind)`` — a pure function the bandit-update path can wrap around
its existing ``base_reward`` value. The default-OFF env flag
``GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED=1`` (strict string ``"1"``,
matching the post_rca + rationale_classifier opt-in pattern) keeps
the wire dormant until the operator confirms the directionality
matches their intent across niches.

Design properties:

* **Pure function.** No side effects, no I/O on the hot path beyond
  the cached YAML load. Drop-in around any existing reward call.
* **Fail-OPEN.** Any error path (missing config, malformed entry,
  unknown category) returns ``base_reward`` unchanged. The bandit
  must NEVER stop learning because the rationale wire failed —
  it should fall back to current behaviour.
* **Opt-in via strict ``"1"``.** ``"true"`` / ``"yes"`` are NOT
  accepted. Matches the ambiguity-free opt-in pattern from
  ``rationale_classifier._is_enabled`` and PR #634
  (``GENLAB_LOG_SELECTION_PROPENSITY``).
* **Positive-reward passthrough.** Multipliers only apply to NEGATIVE
  base rewards (the operator-reject case). Engagement-positive
  rewards from the metric collector path are never amplified or
  dampened by this module — they go through compute_reward
  unchanged.
* **Cached config.** YAML loaded once per process, invalidated by
  ``clear_cache()`` for tests. Matches platform_reward_multipliers
  pattern.

See PR AA for activation + measurement plan.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock
from typing import Any, Final, Literal

import yaml

logger = logging.getLogger(__name__)


# Env flag — strict "1" only. "true" / "yes" / "on" are explicitly
# NOT accepted to keep the activation gesture unambiguous. Matches
# rationale_classifier._is_enabled + PR #634 propensity-logging flag.
_ENV_ENABLED: Final[str] = "GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED"

# Multiplier table key under which per-category dicts live in YAML.
_KEY_RATIONALE_WEIGHTS: Final[str] = "rationale_weights"
_KEY_DEFAULT: Final[str] = "default"

# Suffix that distinguishes hook-arm vs style-arm multiplier within
# each category's dict (e.g. "hook_arm_multiplier", "style_arm_multiplier").
_KEY_HOOK_ARM_MUL: Final[str] = "hook_arm_multiplier"
_KEY_STYLE_ARM_MUL: Final[str] = "style_arm_multiplier"

# Valid arm-kind values. Anything else returns base_reward unchanged
# (defensive — callers passing typos shouldn't get silent miscredit).
ArmKind = Literal["hook", "style"]
_VALID_ARM_KINDS: Final[frozenset[str]] = frozenset({"hook", "style"})


def _config_path() -> Path:
    """Resolve the YAML config path.

    Matches the ``platform_reward_multipliers._config_path`` pattern —
    two ``parents[3]`` hops lands in ``genlab-core/`` (the package's
    parent), so ``config/`` sits as a sibling of ``src/``.
    """
    return Path(__file__).resolve().parents[3] / "config" / "learning_rationale_weights.yaml"


# Process-level cache + lock. Invalidated via ``clear_cache()`` for tests.
_CACHE: dict[str, Any] | None = None
_CACHE_LOCK = Lock()


def clear_cache() -> None:
    """Reset the in-process cache. Called from tests + reload helpers."""
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


def _is_enabled() -> bool:
    """Return True only when the env flag is explicitly set to ``"1"``.

    Matches rationale_classifier opt-in. ``"true"`` / ``"yes"`` are
    NOT accepted — keeps the activation gesture unambiguous and
    parallels the strict-1 pattern shipped in PR #634.
    """
    return os.environ.get(_ENV_ENABLED, "0").strip() == "1"


def load_weights(path: Path | None = None) -> dict[str, Any]:
    """Load the rationale-weights table from YAML, with safe defaults.

    Args:
        path: Override config path (used by tests). Defaults to the
            canonical ``learning_rationale_weights.yaml`` location.

    Returns:
        Dict with keys ``rationale_weights`` (category → multipliers)
        and ``default`` (fallback multipliers). Always returns a non-
        None dict — empty when the config is missing so the caller's
        lookup falls through to a no-op (multiplier = 1.0, behaviour
        preserved).
    """
    cfg = path or _config_path()
    if not cfg.exists():
        return {}
    try:
        with open(cfg, encoding="utf-8") as f:
            data: Any = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning(
            "[rationale_weighted_reward] failed to load %s: %s — using passthrough",
            cfg,
            exc,
        )
        return {}
    if not isinstance(data, dict):
        logger.warning(
            "[rationale_weighted_reward] %s is not a dict at top level — using passthrough",
            cfg,
        )
        return {}
    return data


def _get_cached_weights() -> dict[str, Any]:
    """Return the cached weights dict, loading lazily on first call."""
    global _CACHE
    if _CACHE is None:
        with _CACHE_LOCK:
            if _CACHE is None:
                _CACHE = load_weights()
    return _CACHE


def _multiplier_for(rationale: str | None, arm_kind: str) -> float:
    """Resolve the multiplier for ``(rationale, arm_kind)``.

    Falls through to the ``default`` block when:
      - rationale is None / empty
      - rationale is not in the canonical 6 categories (incl. "uncategorized")
      - the per-category dict is missing the arm_kind multiplier key

    Falls through to ``1.0`` (passthrough) when the YAML is empty or
    the ``default`` block is missing — preserves current behaviour
    when the config file isn't deployed.
    """
    weights = _get_cached_weights()
    arm_key = f"{arm_kind}_arm_multiplier"

    rationale_weights = weights.get(_KEY_RATIONALE_WEIGHTS, {})
    default_block = weights.get(_KEY_DEFAULT, {})

    # Default-block lookup is the fallback for unknown / None rationale.
    default_mul = default_block.get(arm_key, 1.0) if isinstance(default_block, dict) else 1.0
    try:
        default_mul = float(default_mul)
    except (TypeError, ValueError):
        default_mul = 1.0

    if not rationale or not isinstance(rationale_weights, dict):
        return default_mul

    cat = rationale_weights.get(rationale)
    if not isinstance(cat, dict):
        return default_mul

    mul_raw = cat.get(arm_key)
    if mul_raw is None:
        return default_mul

    try:
        return float(mul_raw)
    except (TypeError, ValueError):
        logger.warning(
            "[rationale_weighted_reward] non-numeric multiplier for %s/%s: %r — using default",
            rationale,
            arm_key,
            mul_raw,
        )
        return default_mul


def apply_rationale_multiplier(
    base_reward: float,
    rationale: str | None,
    arm_kind: str,
) -> float:
    """Apply per-rationale multiplier when ``GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED=1``.

    Returns ``base_reward`` unchanged when:
      - the env flag is OFF (default)
      - ``rationale`` is None / empty (no operator-click logged or
        classifier returned "uncategorized")
      - ``arm_kind`` is not one of ``{"hook", "style"}``
      - ``base_reward`` is non-negative (engagement-positive path —
        rationale multipliers only apply to REJECTION rewards)

    Otherwise: looks up the multiplier for ``(rationale, arm_kind)``
    in the YAML config, with fall-through to the ``default`` block
    for unknown categories.

    Args:
        base_reward: The pre-rationale reward (typically negative for
            an operator rejection, e.g. -1.0).
        rationale: One of the 6 canonical categories from
            ``rationale_classifier.CANONICAL_CATEGORIES``, OR None
            when no high-confidence classification exists, OR any
            string (defensive — unknown values fall through to default).
        arm_kind: ``"hook"`` or ``"style"`` — which bandit arm
            dimension this update targets.

    Returns:
        The adjusted reward. Same dtype as ``base_reward`` (float).

    Examples:
        >>> import os
        >>> os.environ["GENLAB_RATIONALE_WEIGHTED_REWARD_ENABLED"] = "1"
        >>> # weak_hook on the hook arm — strong amplification
        >>> apply_rationale_multiplier(-1.0, "weak_hook", "hook")
        2.0  # i.e. -1.0 * -2.0 = +2.0 (multipliers stored as signed)
        >>> # bad_fit on the hook arm — dampened (arm blameless)
        >>> apply_rationale_multiplier(-1.0, "bad_fit", "hook")
        0.2  # i.e. -1.0 * -0.2 = +0.2
    """
    # Fail-OPEN on env: when flag is off (or any read error), return base.
    if not _is_enabled():
        return base_reward

    # Engagement-positive rewards bypass — rationale only applies to
    # rejection signals. A positive reward arriving with a rationale
    # tag (logically inconsistent) returns unchanged rather than
    # being inverted.
    if base_reward >= 0:
        return base_reward

    # Defensive on arm_kind — typos shouldn't silently miscredit.
    if arm_kind not in _VALID_ARM_KINDS:
        logger.debug(
            "[rationale_weighted_reward] unknown arm_kind=%r — returning base",
            arm_kind,
        )
        return base_reward

    multiplier = _multiplier_for(rationale, arm_kind)
    adjusted = base_reward * multiplier
    logger.debug(
        "[rationale_weighted_reward] %s/%s: base=%.3f * mult=%.3f -> %.3f",
        rationale or "(none)",
        arm_kind,
        base_reward,
        multiplier,
        adjusted,
    )
    return adjusted
