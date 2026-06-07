"""Platform-wide tunable constants — loaded once from ``tuning.yaml``.

The senior-engineer review's risk **D3** flagged the platform-wide tuning
constants I'd been sprinkling at module scope (``_TARGET_LIKE_RATIO``,
``_UPVOTE_VIEW_EQUIVALENCE``, ``_RSS_FALLBACK_BASE_VELOCITY``,
``_VIDEO_PROCESSING_WAIT``, etc.) as a project-rule violation::

    Never put values in code that belong in config YAML.

Every one of these was introduced *during a calibration exercise* with the
explicit note "rough; revisit after observing a few live runs" (see PR #52,
PR #53, the session memory). Ops changes need a code deploy today; they
should be a yaml edit.

Public surface
--------------

* :class:`TuningConfig` — typed Pydantic root model.
* :func:`get_tuning_config` — cached loader. Returns the validated model
  from ``genlab-core/config/tuning.yaml`` (or the field defaults if the
  yaml is missing — keeps the unit-test path zero-config).

What belongs here vs in niche YAMLs
-----------------------------------

* **`tuning.yaml`**: platform-wide calibration values — the things every
  niche should agree on (engagement-floor, timeout limits, API retry
  policies, default view-count floors).
* **`{niche}/config/scoring_weights.yaml`**: per-niche overrides of any
  tuning value, plus the niche-specific weight vectors.
* **`{niche}/config/niche.yaml`**: pipeline stage list, niche identity.

A niche scoring weights file may carry a key with the same name to
override; the *loader* below does the merge if/when a niche-aware caller
needs it. Today's API returns the platform-wide model only —
niche-override threading is a follow-up.

Migration plan
--------------

This PR moves the **composite-scoring** constants
(``DEFAULT_MIN_VIEW_COUNT``, ``_TARGET_LIKE_RATIO``, ``_ENGAGEMENT_FLOOR``
in :mod:`genlab_core.scoring.composite_scorer`) into tuning, demonstrating
the pattern. Subsequent PRs migrate the remaining 6+ call sites
(``_UPVOTE_VIEW_EQUIVALENCE``, ``_RSS_FALLBACK_BASE_VELOCITY``,
``_VIDEO_PROCESSING_WAIT``, ``MIN_DELAY_HOURS``, ``MAX_WARM_DAYS``,
``_DOWNLOAD_TIMEOUT``) one at a time.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, Field

# ── nested schemas ──────────────────────────────────────────────────


class CompositeScoringConfig(BaseModel):
    """Composite-scoring calibration for trending clip ranking.

    Sources of truth for ``_TARGET_LIKE_RATIO``, ``_ENGAGEMENT_FLOOR`` and
    the per-niche ``DEFAULT_MIN_VIEW_COUNT`` introduced in PR #52.
    """

    target_like_ratio: float = Field(
        default=0.03,
        description=(
            "like/view ratio at which the engagement multiplier saturates "
            "to 1.0. ~3% is a healthy viral clip (YT average ~1-2%)."
        ),
    )
    engagement_floor: float = Field(
        default=0.5,
        description=(
            "Floor of the engagement multiplier: a zero-engagement clip "
            "still keeps this fraction of its composite (velocity remains "
            "a real reach signal). So engagement RE-RANKS without hard-"
            "filtering."
        ),
    )
    default_min_view_count: dict[str, int] = Field(
        default_factory=lambda: {
            "gaming": 5000,
            "sports": 5000,
            "movies": 5000,
            "anime": 2000,
            "ai_creators": 2000,
        },
        description=(
            "Absolute view-count floor per niche. A video with KNOWN "
            "view_count below this is rejected regardless of velocity. "
            "0/absent view_count = 'unknown' and is NOT floored."
        ),
    )


class TuningConfig(BaseModel):
    """Root model — every nested config gets its own ``Field`` so future
    additions don't churn the load API."""

    composite_scoring: CompositeScoringConfig = Field(default_factory=CompositeScoringConfig)


# ── loader ──────────────────────────────────────────────────────────


_DEFAULT_YAML_PATH: Final[Path] = Path(__file__).resolve().parents[3] / "config" / "tuning.yaml"


@lru_cache(maxsize=1)
def get_tuning_config() -> TuningConfig:
    """Return the validated platform-wide tuning config.

    Reads ``genlab-core/config/tuning.yaml`` once per process and caches
    the parsed result. Missing file → field defaults (keeps the unit-test
    + scratch-import path zero-config). Malformed yaml → ``yaml.YAMLError``
    propagates — this is opt-in operational state and a bad file should
    fail loudly, not silently fall back.
    """
    if not _DEFAULT_YAML_PATH.exists():
        return TuningConfig()

    with open(_DEFAULT_YAML_PATH) as f:
        data = yaml.safe_load(f) or {}

    return TuningConfig.model_validate(data)


def _reset_cache_for_tests() -> None:
    """Test-only helper — drop the cached load so a follow-up
    :func:`get_tuning_config` re-reads from disk."""
    get_tuning_config.cache_clear()
