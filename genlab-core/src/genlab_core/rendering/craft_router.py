"""Decide which renderer produces this reel, and record why.

The one invariant, from RENDER-01 §2 and the CLAUDE.md render-path rule:

    FFmpeg produces a publishable reel standalone at every stage.

So craft is additive. Nothing here may raise, and nothing may return "no reel":
the worst outcome is a legacy reel plus a recorded reason. A renderer that could
be blocked by a closed laptop would turn that standing guarantee into a
dependency on one.

Every decision is counted into ``run_stats['render']`` so a fire that quietly
produced legacy for three weeks is visible as a number rather than as a
surprise at review time.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from genlab_core.action.matte_worker import MatteRequest, MatteResult, request_matte
from genlab_core.rendering.render_engine import Engine, dual_render_enabled, resolve_engine

logger = logging.getLogger(__name__)


@dataclass
class RenderDecision:
    engine: Engine
    craft_attempted: bool = False
    craft_skipped: str = ""          # a SkipReason, or "" when not skipped
    matte: MatteResult | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def craft_available(self) -> bool:
        return self.matte is not None and self.matte.ok


def plan_render(niche_id: str, niche_config: dict | None,
                matte_request: MatteRequest | None = None,
                *, request_fn: Callable = request_matte,
                **request_kwargs) -> RenderDecision:
    """What should this fire render, and did craft's prerequisites arrive?

    Returns a decision; it never raises. ``engine`` is what should PUBLISH:
    craft only when the niche selects it AND the mattes actually arrived.
    """
    configured = resolve_engine(niche_id, niche_config)
    dual = dual_render_enabled(niche_id, niche_config)
    d = RenderDecision(engine=configured)

    # Craft prerequisites are only fetched when someone will use the result --
    # either craft publishes, or dual asked for it to be rendered alongside.
    if not (configured is Engine.CRAFT or dual):
        d.notes.append("craft not requested for this niche")
        return d
    if matte_request is None:
        d.craft_skipped = "no_matte_request"
        d.engine = Engine.LEGACY
        d.notes.append("craft requested but no matte job was described")
        return d

    d.craft_attempted = True
    try:
        matte, reason = request_fn(matte_request, **request_kwargs)
    except Exception as exc:  # noqa: BLE001 — a publishable reel must survive this
        logger.warning("[craft-router] matte request raised: %s", exc, exc_info=True)
        matte, reason = None, "matte_request_error"

    if matte is None or not matte.ok:
        d.craft_skipped = reason or "worker_failed"
        d.engine = Engine.LEGACY
        logger.warning("[craft-router] niche=%s craft_skipped=%s — rendering legacy",
                       niche_id, d.craft_skipped)
        return d

    d.matte = matte
    if configured is not Engine.CRAFT:
        d.notes.append("dual: craft rendered beside legacy, legacy publishes")
    return d


def record(decision: RenderDecision, run_stats: dict[str, Any]) -> None:
    """Fold the decision into the run report.

    Counted, not just logged: 'craft rendered' and 'craft silently skipped for
    three weeks' look identical in a log tail and completely different in a
    counter.
    """
    r = run_stats.setdefault("render", {})
    r["engine"] = str(decision.engine)
    r["craft_attempted"] = bool(decision.craft_attempted)
    if decision.craft_skipped:
        r["craft_skipped"] = decision.craft_skipped
        counts = r.setdefault("craft_skipped_counts", {})
        counts[decision.craft_skipped] = counts.get(decision.craft_skipped, 0) + 1
    if decision.matte is not None:
        r["matte_frames"] = decision.matte.frames
        r["matte_seconds"] = decision.matte.seconds
    if decision.notes:
        r.setdefault("notes", []).extend(decision.notes)
