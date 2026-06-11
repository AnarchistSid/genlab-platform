"""R-70 part 2 — PR 1 of the sequenced extraction plan.

Concrete shared base for the per-channel visual render strategies in
``SpliceReel/sr_strategies/visual_render.py``,
``ClutchWire/cw_strategies/visual_render.py``, and
``FrameDrift/fd_strategies/visual_render.py``.

What this class ships
---------------------
* **One** concrete method — ``_get_whisper_config`` — verified
  byte-identical across the 3 channels at session-#2 time. Every
  other method is left abstract / ``NotImplementedError`` for the
  pilot migrations (PRs 2-5 of the sequence in
  ``docs/r70-part2-design-phase.md``) to fill in once a body-level
  diff confirms what's truly shared vs channel-specific.

* **One** required instance attribute — ``_visuals_config`` — that
  ``_ensure_config()`` (channel-specific) must populate. The base's
  ``_get_whisper_config`` reads from it; without an ``_ensure_config``
  implementation, the AttributeError surfaces at first use.

This is deliberately a thin skeleton. Per the design doc:

  Risk: very low. Doesn't change any channel behavior.

No channel migrates to this base in PR 1. PR 2 (SR pilot) is the
first migration; the gate for that migration is "all SR tests pass
unchanged."

See ``docs/r70-part2-design-phase.md`` for the full multi-PR plan
and the empirical-divergence baseline that grounded these choices.
"""

from __future__ import annotations

from abc import abstractmethod
from pathlib import Path
from typing import Any

from genlab_core.strategies.interfaces import VisualRenderStrategy


class BaseVisualRenderStrategy(VisualRenderStrategy):
    """Shared concrete + abstract scaffold for niche visual render.

    Subclasses MUST set ``self._visuals_config`` (a parsed
    ``visuals.yaml`` dict) inside their ``_ensure_config()``
    implementation before any inherited method runs.
    """

    # Populated by ``_ensure_config`` in subclasses.
    _visuals_config: dict | None

    def __init__(self) -> None:
        # Subclasses override and add their own niche-specific state,
        # but they MUST call ``super().__init__()`` to keep this
        # attribute initialization deterministic.
        self._visuals_config = None

    # ── Abstract: each subclass owns its config-loading shape ──────

    @abstractmethod
    def _ensure_config(self) -> None:
        """Load + cache ``visuals.yaml`` (and any other niche configs).

        Must populate ``self._visuals_config`` so the shared
        ``_get_whisper_config`` and other base methods can read from
        it. SpliceReel also reads ``sources.yaml`` here; CW + FD only
        read ``visuals.yaml``. The channel-specific YAML-loading
        decisions belong to the subclass, not the base.
        """
        ...

    # ── Concrete: the one body that's truly shared (verified) ──────

    def _get_whisper_config(self) -> dict:
        """Get whisper_sync config from visuals.yaml.

        Verified byte-identical across SR/CW/FD at extraction time
        (session-#2, 2026-06-11). If a future channel needs a
        different lookup path or default, OVERRIDE this method in
        the subclass — don't generalize the base.
        """
        self._ensure_config()
        animation = (self._visuals_config or {}).get("animation", {})
        wbw = animation.get("word_by_word", {})
        return wbw.get("whisper_sync", {"enabled": False})

    # ── Abstract: pilot migrations decide the shared shape ─────────
    #
    # Each of the methods below is reserved for the migration PRs.
    # Until PR 2 surfaces what's truly shared in each method's body,
    # the base requires subclasses to provide the implementation —
    # nothing's silently inherited that the design phase didn't sign
    # off on.

    @abstractmethod
    def prepare_whisper_words(self, clip_path: Path, story: dict) -> list:
        """Run Whisper on the clip's audio and return aligned word
        timings. Sport/movie/anime channels all have this method
        with similar shape but distinct log prefixes and slightly
        different fallback paths — the body-level diff in PR 2 will
        decide whether the shared body is extractable."""
        ...

    @abstractmethod
    def _compose_frame(self, clip_path: Path, story: dict, context: dict) -> str:
        """Build the composite frame (logo + overlay + clip). 33 lines
        in each channel today; PR 4 (precondition-gated on body-level
        diff) decides whether to lift into the base."""
        ...

    @abstractmethod
    def _build_pexels_queries(self, story: dict) -> list[str]:
        """Build Pexels B-roll search queries. Substantively divergent
        per channel (sport-specific terms vs cinematic vs anime); the
        design phase deliberately left this as per-channel — the base
        forces subclasses to be explicit."""
        ...

    @abstractmethod
    def _render_story(self, story: dict) -> dict:
        """Orchestrate the per-story render pipeline. Small 21-23 line
        divergence in production today; PR 3 decides whether the
        orchestration shape is lift-able."""
        ...

    @abstractmethod
    def execute(self, context: Any) -> Any:
        """Stage entrypoint — already abstract on the parent
        ``VisualRenderStrategy(ABC)``. Re-declared here as an explicit
        reminder that even the orchestration top-level isn't auto-
        inherited until a pilot migration surfaces what's truly
        shared."""
        ...
