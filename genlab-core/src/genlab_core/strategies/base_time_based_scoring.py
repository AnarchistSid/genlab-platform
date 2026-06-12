"""R-70 part 2 — PR 5b: SR + FD second-tier scoring base.

The design plan flagged a real product split inside scoring:
SR + FD share a "time-decayed engagement" axis set; CW genuinely
uses different axes (recency / community_signal). PR 5a shipped
the narrow ``BaseScoringStrategy`` with only ``_score_novelty``
extracted (the one byte-identical method across all three channels).
This module adds the second-tier base that **only SR + FD** inherit
from. CW does **not** — it stays on the narrow ``BaseScoringStrategy``
directly, migrated in PR 5c.

Empirical extraction scope (verified body-diff this session)
------------------------------------------------------------

* ``__init__`` — byte-identical between SR + FD except for the log
  prefix string (``[movies]`` / ``[anime]``). Subclass sets
  ``_log_prefix`` as a class attribute (mirrors the PR 4
  visual_render extraction pattern: 2-attribute escape hatch
  rather than abstract getters).

* ``_ensure_config`` — byte-identical except for the path to
  ``scoring_weights.yaml`` (each channel's per-module
  ``NICHE_ROOT``). Subclass sets ``self._scoring_yaml_path`` in
  ``__init__`` so the inherited ``_ensure_config`` body resolves
  the right YAML.

* ``_score_timeliness`` — byte-identical exponential-decay logic
  with one literal-default divergence: SR's docstring + fallback
  is 48h, FD's is 24h. In production this default is rarely hit
  (both ``scoring_weights.yaml`` files set the key explicitly) —
  the safe extraction is a subclass-set
  ``_default_timeliness_half_life_hours`` class attribute. The
  body uses it only when the config key is absent.

NOT extracted (substantively divergent between SR + FD)
-------------------------------------------------------

* ``_score_magnitude`` — different ``item`` fields, different
  config keys, different threshold tables. Stays per-channel.
* ``_score_engagement_potential`` — SR reads ``tmdb_popularity``
  with a 4-bucket threshold table; FD reads
  ``source_mention_count`` with a 3-bucket table. Same axis name,
  different formula. Stays per-channel.
* ``score_item`` — both call the same 4-axis assembly, but the
  post-weight multiplier differs (SR applies lifecycle × franchise;
  FD applies trend_cycle). Stays per-channel; PR 5b leaves the
  abstract requirement from ``BaseScoringStrategy`` in place.

If a future channel needs a different timeliness formula (e.g.
piecewise linear decay), OVERRIDE ``_score_timeliness`` in the
subclass — don't generalize the base. The [[feedback-two-attr-
extraction-escape-hatch]] rule applies: literal/path defaults
become subclass-set attributes; behavior changes need a real
override or a new abstract hook.
"""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime
from pathlib import Path

import yaml

from genlab_core.strategies.base_scoring import BaseScoringStrategy

logger = logging.getLogger(__name__)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


class BaseTimeBasedScoringStrategy(BaseScoringStrategy):
    """Shared base for the SR + FD scoring strategies.

    Subclasses MUST set:

    * ``_log_prefix`` — class attribute, e.g. ``"[movies]"`` /
      ``"[anime]"``. Used in init-time + log messages so each
      channel's stream keeps its tag.

    * ``self._scoring_yaml_path`` — instance attribute pointing
      at the channel's ``scoring_weights.yaml`` (set in
      ``__init__`` BEFORE calling ``super().__init__()``, because
      the base ``__init__`` doesn't read it — but ``_ensure_config``
      does, and a subclass that sets it after ``__init__`` runs
      is fine. Either order works; the gate is "before the first
      ``_ensure_config`` call").

    Subclasses MAY override:

    * ``_default_timeliness_half_life_hours`` — class attribute
      (default 48.0 — SR's value). FD overrides to 24.0. Used only
      as the fallback when ``scoring_weights.yaml`` doesn't carry
      ``scoring_dimensions.timeliness.decay_half_life_hours``.
    """

    # Set by subclass __init__. The base ``_ensure_config`` reads
    # this; an absent-attribute AttributeError is the right surfacing
    # of a misconfigured base consumer.
    _scoring_yaml_path: Path

    # Subclasses override as needed. SR keeps 48.0 (already the
    # default); FD overrides to 24.0.
    _default_timeliness_half_life_hours: float = 48.0

    # Subclasses override. Default visible in logs so a missing
    # override is detectable.
    _log_prefix: str = "[time_based_scoring]"

    def __init__(self) -> None:
        # Mirror the byte-identical SR + FD __init__ shape. Subclass
        # is expected to log its own niche-init line BEFORE calling
        # super().__init__() — or after, doesn't matter functionally.
        self._config: dict | None = None
        self._scoring: dict | None = None
        self._thresholds: dict | None = None

    # ── Concrete: byte-identical between SR + FD ──────────────────

    def _ensure_config(self) -> None:
        """Load + cache ``scoring_weights.yaml``.

        Verified byte-identical between SR + FD at extraction time
        (this session), modulo the per-channel ``NICHE_ROOT`` path.
        Subclasses pass that path via ``self._scoring_yaml_path``.
        """
        if self._config is not None:
            return
        self._config = _load_yaml(self._scoring_yaml_path)
        self._scoring = self._config.get("scoring_dimensions", {})
        self._thresholds = self._config.get("thresholds", {})

    def _score_timeliness(self, item: dict) -> float:
        """Exponential decay on ``fetched_at`` with a configurable
        half-life.

        Verified byte-identical between SR + FD at extraction time
        except for one literal: the default half-life (SR 48h, FD
        24h). The literal-only divergence becomes a class-attribute
        override on the subclass.
        """
        scoring = self._scoring or {}
        half_life = scoring.get("timeliness", {}).get(
            "decay_half_life_hours",
            self._default_timeliness_half_life_hours,
        )
        fetched_at_str = item.get("fetched_at", "")
        if not fetched_at_str:
            return 0.0
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
            now = datetime.now(UTC)
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=UTC)
            hours_elapsed = (now - fetched_at).total_seconds() / 3600
            if hours_elapsed < 0:
                return 1.0
            return math.pow(0.5, hours_elapsed / half_life)
        except (ValueError, TypeError):
            return 0.0
