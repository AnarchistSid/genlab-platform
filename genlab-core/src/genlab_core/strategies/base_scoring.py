"""R-70 part 2 — PR 5a of the sequenced extraction plan.

Concrete shared base for the per-channel scoring strategies in
``SpliceReel/sr_strategies/scoring.py``,
``ClutchWire/cw_strategies/scoring.py``, and
``FrameDrift/fd_strategies/scoring.py``.

Empirical scope (this PR)
-------------------------

Before extraction this session re-ran the design plan's body-level diff
on every method. The numbers told a more honest story than the design
doc's hypothesis:

* ``_score_novelty`` — **byte-identical 1-line body** across all
  three channels: ``return item.get("novelty_score", 0.5)``.
  Extracted concretely here.

* ``_score_magnitude`` — **substantively divergent**: SR scores on
  ``rt_score``/``is_trailer_drop``/``is_box_office_news``; CW scores
  on ``game_type`` via a multiplier table; FD scores on
  ``is_creator_spotlight``/``is_collab``/``is_new_release``/
  ``is_event_coverage``. Same method name, different bodies, different
  config keys, different fields read off ``item``. **NOT extracted.**
  The design doc's "extract magnitude" line was wrong; the data
  surfaced parallel evolution wearing the same name.

* ``__init__`` + ``_ensure_config`` — SR + FD byte-identical;
  CW genuinely divergent (different YAML shape: ``clip_scoring``
  sub-key, ``_weights``/``_multipliers`` instead of ``_scoring``).
  These belong in the **PR 5b second-tier base** that only SR + FD
  inherit from — not here.

* ``score_item`` orchestration — SR + FD share the 4-axis shape
  (``timeliness``, ``magnitude``, ``engagement_potential``, ``novelty``);
  CW uses a completely different axis set (``recency``,
  ``community_signal``, ``magnitude``, ``novelty``). Also PR 5b
  scope.

What this class ships
---------------------

* **One** concrete method — ``_score_novelty`` — verified
  byte-identical across SR/CW/FD at extraction time.
* ``execute`` re-declared as abstract (it already is on the parent
  ``ScoringStrategy(ABC)``) as an explicit reminder for PRs 5b/5c.
* Every other method left abstract / open: the migration PRs
  decide what's truly shared once a body-level diff confirms it.

No channel migration runs against this base in PR 5a. PR 5b adds the
SR+FD second-tier base + migrates SR as the pilot; PR 5c migrates CW
against this narrow base only.

See ``docs/r70-part2-design-phase.md`` for the full multi-PR plan
and the original empirical-divergence baseline. The 70% scoring
divergence number documented there is *correct*; this PR refines
the per-method picture inside that number.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from genlab_core.strategies.interfaces import ScoringStrategy


class BaseScoringStrategy(ScoringStrategy):
    """Shared concrete + abstract scaffold for niche scoring strategies.

    Subclasses are expected to:

    * Implement their own ``_ensure_config`` (the YAML shape differs
      between CW and SR+FD — see the module docstring).
    * Implement their own ``_score_magnitude`` (substantively
      divergent — see the module docstring).
    * Implement their own ``score_item`` + ``execute``.

    What they get for free:

    * ``_score_novelty`` — verified byte-identical across the three
      channels at extraction time. If a future channel needs a
      richer novelty signal, **override** in the subclass — don't
      generalize the base.

    Why this base is so thin: per the empirical body-diff above, the
    audit's "750L copy-paste" was 70% real divergence wearing the
    same method names. The honest shared surface across all three
    channels is small; extracting more than that produces leaky
    abstractions or per-subclass override walls. PR 5b adds a
    second-tier base for the SR + FD shape that CW doesn't share.
    """

    # ── Concrete: the one body that's truly shared (verified) ──────

    def _score_novelty(self, item: dict) -> float:
        """Read the novelty score the upstream dedup/clustering stage
        wrote onto the item, default 0.5 when absent.

        Verified byte-identical across SR / CW / FD at extraction
        time (this session). Body:

            return item.get("novelty_score", 0.5)

        If a future channel needs a richer novelty signal (e.g.
        category-aware decay), OVERRIDE this method in the subclass
        — don't generalize the base.
        """
        return item.get("novelty_score", 0.5)

    # ── Abstract: substantively divergent across channels ─────────

    @abstractmethod
    def _ensure_config(self) -> None:
        """Load + cache the niche's ``scoring_weights.yaml``.

        SR + FD share the byte-identical loading shape (populating
        ``self._scoring`` from ``scoring_dimensions``); CW reads a
        ``clip_scoring`` sub-key and populates ``_weights`` +
        ``_multipliers`` instead. The PR 5b second-tier base will
        lift the SR + FD shape; CW will keep its own. This base
        forces every subclass to be explicit about its config
        loading until PR 5b lands.
        """
        ...

    @abstractmethod
    def _score_magnitude(self, item: dict) -> float:
        """Score the per-item magnitude axis.

        Empirical finding (this session): the three channels'
        ``_score_magnitude`` bodies share a name and a return type;
        nothing else. SR uses RT scores + trailer/box-office flags;
        CW uses a game-type multiplier table; FD uses creator/collab/
        release/event flags. The design plan's "extract magnitude"
        line was an overreach. The base requires every subclass to
        provide its own.
        """
        ...

    @abstractmethod
    def score_item(self, item: dict) -> dict:
        """Compute the per-item composite score and return the item
        annotated with ``scores`` + ``final_score`` + a per-niche
        multiplier breakdown. Substantively divergent: SR + FD share
        the 4-axis shape (timeliness, magnitude, engagement_potential,
        novelty); CW uses (recency, community_signal, magnitude,
        novelty). Lifted into the PR 5b second-tier base for SR + FD
        only; CW keeps its own."""
        ...

    @abstractmethod
    def execute(self, context: Any) -> Any:
        """Stage entrypoint — already abstract on the parent
        ``ScoringStrategy(ABC)``. Re-declared here as an explicit
        reminder that even the orchestration top-level isn't auto-
        inherited until a pilot migration surfaces what's truly
        shared."""
        ...
