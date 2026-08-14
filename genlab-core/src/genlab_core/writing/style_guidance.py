"""Top-hook-style aggregator for writer prompt guidance (Phase 4.C session 1).

Reads bandit_arms for arm_ids matching ``style:{niche}:{style}`` +
pending_feedback for recent reward samples, computes per-niche
posterior means, returns top-3 styles.

## Ranking metric

Beta posterior mean = alpha / (alpha + beta) — the same statistic
the Thompson sampler uses. Matches session 2's writer wire, which
Thompson-samples from these posteriors to pick the style for a
given blueprint. Guidance shows the operator + the LLM what the
sampler is biased toward this week.

Minimum sample size: 3 plays per style. Below that, the posterior
is dominated by the prior (Beta(1, 1) uniform) and the rank is
essentially random.

## Design

Fail-open at every layer — returns empty list on any DB error /
missing data. Session 2 writer wire treats empty guidance as
"no signal, use defaults" so a broken aggregator doesn't regress
writing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)


_STYLE_ARM_PREFIX = "style:"
_MIN_PLAYS_PER_STYLE = 3
_TOP_N_STYLES = 3


@dataclass(frozen=True)
class StyleGuidance:
    """One style's rank + posterior stats."""
    style_name: str
    reward_mean: float
    n_plays: int
    rank: int  # 1 = best

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_style_from_arm_id(arm_id: str, niche_id: str) -> str | None:
    """arm_id format is ``style:{niche}:{style_name}``. Returns the
    style_name portion when the niche matches, else None."""
    if not arm_id.startswith(_STYLE_ARM_PREFIX):
        return None
    # style:{niche}:{name} — split into 3 parts
    parts = arm_id.split(":", 2)
    if len(parts) != 3:
        return None
    _, arm_niche, style_name = parts
    if arm_niche != niche_id:
        return None
    return style_name or None


def compute_top_styles(
    conn, niche_id: str,
    *,
    min_plays: int = _MIN_PLAYS_PER_STYLE,
    top_n: int = _TOP_N_STYLES,
) -> tuple[list[StyleGuidance], int]:
    """Compute top-N hook styles for a niche.

    Returns (styles, total_sample_size) where styles is sorted
    rank-ascending (rank 1 = best). Empty list + 0 on any failure
    or insufficient data.
    """
    try:
        rows = conn.execute(
            """
            SELECT arm_id, alpha, beta, n_plays
            FROM bandit_arms
            WHERE niche_id = %s
              AND arm_id LIKE 'style:%%:%%'
              AND n_plays >= %s
            """,
            (niche_id, min_plays),
        ).fetchall()
    except Exception as exc:
        logger.warning(
            "[style_guidance] bandit_arms query failed niche=%s: %s",
            niche_id, exc,
        )
        try:
            conn.rollback()
        except Exception:
            pass
        return [], 0

    ranked: list[dict] = []
    total_samples = 0
    for r in rows or []:
        arm_id = r.get("arm_id") if hasattr(r, "get") else r[0]
        alpha = float(r.get("alpha") if hasattr(r, "get") else r[1])
        beta = float(r.get("beta") if hasattr(r, "get") else r[2])
        n_plays = int(r.get("n_plays") if hasattr(r, "get") else r[3])

        style_name = _extract_style_from_arm_id(arm_id, niche_id)
        if not style_name:
            continue

        # Beta posterior mean = alpha / (alpha + beta)
        # Skip when alpha+beta somehow zero (shouldn't happen post-schema fix)
        if alpha + beta <= 0:
            continue
        reward_mean = alpha / (alpha + beta)
        total_samples += n_plays
        ranked.append({
            "style_name": style_name,
            "reward_mean": reward_mean,
            "n_plays": n_plays,
        })

    if not ranked:
        return [], 0

    # Sort by reward_mean DESC, tiebreak by n_plays DESC (more
    # evidence wins ties)
    ranked.sort(key=lambda x: (-x["reward_mean"], -x["n_plays"]))
    top = ranked[:top_n]
    return (
        [
            StyleGuidance(
                style_name=s["style_name"],
                reward_mean=s["reward_mean"],
                n_plays=s["n_plays"],
                rank=i + 1,
            )
            for i, s in enumerate(top)
        ],
        total_samples,
    )
