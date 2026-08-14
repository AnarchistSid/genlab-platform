"""Content-quality → bandit-reward multiplier (Phase 4.A session 4).

Consumer wire for the joint_score written by session 3's runner.
Callers wrap their reward computation with
:func:`apply_quality_multiplier` to bias the bandit toward
high-quality renders.

## Multiplier shape

  * joint_score = 1.0 → multiplier = 1.5×  (25% reward boost)
  * joint_score = 0.5 → multiplier = 1.0×  (no change)
  * joint_score = 0.0 → multiplier = 0.5×  (50% reward penalty)

Formula: ``multiplier = 0.5 + joint_score``. Bounded so a broken
scorer producing joint=0.0 can't zero-out reward entirely
(which would collapse the bandit posterior with synthetic zeros —
same class-of-bug that prompted 2026-07-14's
``compute_reward → None`` change per reward_shaper.py:400).

## Fail-open contract

  * Flag off: passes reward through unchanged.
  * No quality row yet for the blueprint: passes through
    (session 3 runner may not have caught up).
  * joint_score is NULL (every extractor failed): passes through.
  * DB error: passes through with WARN log.

Never regresses the reward on infrastructure failure.

## Rollout

Flag: ``GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED``. Default OFF —
same observation-first pattern as the rest of the intelligence
stack. Operator eyeballs the ContentQualityCard for ≥1 week to
validate the score distribution looks sane before flipping.
"""
from __future__ import annotations

import logging
import os
from typing import Final

logger = logging.getLogger(__name__)

_FLAG_ENV_VAR: Final[str] = "GENLAB_QUALITY_REWARD_MULTIPLIER_ENABLED"
_MIN_MULTIPLIER: Final[float] = 0.5
_MAX_MULTIPLIER: Final[float] = 1.5


def is_enabled() -> bool:
    """True if the flag is set to a truthy value. Same
    exact-match pattern as other quality-stack flags."""
    return os.environ.get(_FLAG_ENV_VAR, "").strip().lower() in {
        "1", "true", "yes",
    }


def _fetch_joint_score(conn, blueprint_id: str) -> float | None:
    """Most recent joint_score for a blueprint (across video-hash
    re-scores). Fail-open to None on any DB error → caller falls
    back to unit multiplier."""
    try:
        row = conn.execute(
            """
            SELECT joint_score
            FROM content_quality_scores
            WHERE blueprint_id = %s
              AND joint_score IS NOT NULL
            ORDER BY computed_at DESC
            LIMIT 1
            """,
            (blueprint_id,),
        ).fetchone()
        if row is None:
            return None
        val = row.get("joint_score") if hasattr(row, "get") else row[0]
        return float(val) if val is not None else None
    except Exception as exc:
        logger.warning(
            "[quality_multiplier] fetch failed bp=%s: %s",
            blueprint_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return None


def apply_quality_multiplier(
    reward: float | None, blueprint_id: str, conn,
) -> float | None:
    """Wrap a computed reward with the joint-quality multiplier.

    ``reward`` — the shaper's output; None passes through unchanged
    (a None reward is already fail-open per reward_shaper.py:400).

    ``blueprint_id`` — used to look up
    ``content_quality_scores.joint_score`` (latest by video_hash).

    ``conn`` — psycopg connection. Never mutated on failure.

    Returns the reward with multiplier applied, or the original
    reward when: flag off, no quality row, NULL joint, DB error.
    """
    if reward is None:
        return None
    if not is_enabled():
        return reward
    joint = _fetch_joint_score(conn, blueprint_id)
    if joint is None:
        return reward
    # Clip joint to [0, 1] then map to multiplier range [0.5, 1.5].
    j = max(0.0, min(1.0, joint))
    multiplier = _MIN_MULTIPLIER + j
    multiplier = max(_MIN_MULTIPLIER, min(_MAX_MULTIPLIER, multiplier))
    return reward * multiplier
