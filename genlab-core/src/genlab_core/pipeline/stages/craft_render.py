"""Render the craft treatment BESIDE the legacy reel. Publish nothing.

RENDER-01 Part 11. Everything craft needs already existed — the storyboard
model, the plan gates, the matte worker, the effects, the executor — and nothing
invoked any of it. This is the stage that calls it.

THE ONE INVARIANT
-----------------
While ``craft_publishes`` is False this stage MUST NOT touch ``visual_paths`` or
anything else the publisher reads. Legacy renders and publishes exactly as it
does today; craft's output is written beside it and looked at. Every failure
path here returns the context unchanged with a ``craft_skipped`` reason counted
in ``run_stats['render']`` — craft never blocks a publish, so the worst outcome
is a legacy reel plus a recorded reason.

WHY EACH GATE IS A SEPARATE REASON
----------------------------------
"Craft didn't run" is not a finding; "craft skipped because the worker was
asleep" is, and it is a different action from "skipped because the route came
back STILL". A single boolean would have made the first dual fire unreadable.

THE ROUTE COMES FROM PROVENANCE, NOT THE CLASSIFIER
---------------------------------------------------
``action.router`` decides from the fetcher's own metadata; the content
classifier runs alongside and its verdict is persisted next to the route so its
agreement rate accumulates on live material. It does not route: it tops out at
18/27 on a real corpus.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genlab_core.action.matte_worker import (
    CropPlan,
    CropRow,
    HSVSpec,
    MatteRequest,
    SkipReason,
)
from genlab_core.pipeline.stage_context import StageContext
from genlab_core.rendering.render_engine import craft_publishes, dual_render_enabled

logger = logging.getLogger(__name__)


class CraftSkip:
    """Reasons craft did not produce a reel. Each one implies a different fix."""

    NOT_REQUESTED = "not_requested"  # niche runs neither craft nor dual
    NO_STORYBOARD = "no_storyboard"  # nothing planned this blueprint
    ROUTE_NOT_CRAFTABLE = "route_not_craftable"  # provenance said STILL
    NO_SUBJECT_SPEC = "no_subject_spec"  # colour seed not derivable
    NO_CROP_PLAN = "no_crop_plan"  # shot list carries no geometry
    EXECUTOR_DECLINED = "executor_declined"  # craft.render returned None


class CraftRenderStage:
    """Runs after the legacy render. Reads a storyboard, writes a second reel."""

    name = "CraftRender"

    def execute(self, context: StageContext) -> StageContext:
        niche = context.get("niche_id", "")
        cfg = context.get("niche_config") or {}
        stats = context.setdefault("run_stats", {}).setdefault("render", {})

        if not (dual_render_enabled(niche, cfg) or craft_publishes(niche, cfg)):
            self._skip(stats, CraftSkip.NOT_REQUESTED, niche)
            return context

        blueprints = context.get("blueprints") or []
        if not blueprints:
            logger.info("[craft] no blueprints in this run")
            return context

        for bp in blueprints:
            try:
                self._one(bp, context, stats)
            except Exception as exc:  # noqa: BLE001
                # A craft failure must never propagate: legacy has already
                # rendered and the publisher must not see an exception from an
                # observation-only stage.
                #
                # The identifier is recovered DEFENSIVELY. An earlier version
                # called bp.get() here to name the blueprint — the same call
                # that had just raised — so the handler re-raised and defeated
                # itself. An error path must not depend on the object that
                # failed.
                try:
                    bid = str(bp.get("record_id") or bp.get("id") or "?")
                except Exception:  # noqa: BLE001
                    bid = "<unreadable>"
                logger.warning("[craft] blueprint %s raised: %s", bid, exc, exc_info=True)
                self._skip(stats, "exception", niche, bid)
        return context

    # ── one blueprint ───────────────────────────────────────────────────────

    def _one(self, bp: dict, context: StageContext, stats: dict) -> None:
        niche = context.get("niche_id", "")
        bid = str(bp.get("record_id") or bp.get("id") or "?")

        sb = bp.get("storyboard") or context.get("storyboards", {}).get(bid)
        if not sb:
            self._skip(stats, CraftSkip.NO_STORYBOARD, niche, bid)
            return

        route = (sb.get("routed_treatment") or sb.get("treatment") or "").upper()
        if route not in ("ACTION", "TALK"):
            self._skip(stats, CraftSkip.ROUTE_NOT_CRAFTABLE, niche, bid, detail=route or "none")
            return

        if route == "ACTION":
            self._action(bp, sb, context, stats, bid)
        else:
            self._talk(bp, sb, context, stats, bid)

    def _action(self, bp: dict, sb: dict, context: StageContext, stats: dict, bid: str) -> None:
        niche = context.get("niche_id", "")
        spec = self._subject_spec(sb)
        if spec is None:
            self._skip(stats, CraftSkip.NO_SUBJECT_SPEC, niche, bid)
            return
        plan = self._crop_plan(sb)
        if plan is None:
            self._skip(stats, CraftSkip.NO_CROP_PLAN, niche, bid)
            return

        clip = bp.get("clip_path") or (bp.get("media") or {}).get("clip_path") or ""
        req = MatteRequest(
            clip_path=clip,
            frames_dir=str(Path(context.get("run_dir", ".")) / "frames" / bid),
            subject_spec=spec,
            crop_plan=plan,
            cuts=list(sb.get("cuts") or []),
            niche_id=niche,
            blueprint_id=bid,
        )

        from genlab_core.rendering.craft_router import plan_render

        decision = plan_render(niche, context.get("niche_config") or {}, req)
        if not decision.craft_available:
            reason = decision.craft_skipped or SkipReason.WORKER_UNAVAILABLE
            self._skip(stats, reason, niche, bid)
            return

        result = self._render(sb, decision.matte, context, bid)
        if result is None:
            self._skip(stats, CraftSkip.EXECUTOR_DECLINED, niche, bid)
            return
        self._deliver(bp, sb, result, context, bid, route="ACTION")

    def _talk(self, bp: dict, sb: dict, context: StageContext, stats: dict, bid: str) -> None:
        """TALK needs no mattes — captions, frame and window come from the
        transcript. Same deliverable shape, same craft_publishes guard."""
        result = self._render(sb, None, context, bid)
        if result is None:
            self._skip(stats, CraftSkip.EXECUTOR_DECLINED, context.get("niche_id", ""), bid)
            return
        self._deliver(bp, sb, result, context, bid, route="TALK")

    # ── helpers ─────────────────────────────────────────────────────────────

    def _subject_spec(self, sb: dict) -> HSVSpec | None:
        """The FULL HSV spec or nothing — never a hue with defaulted floors."""
        raw = (sb.get("subject") or {}).get("subject_colour") or sb.get("subject_colour")
        if not isinstance(raw, dict):
            return None
        try:
            return HSVSpec(
                hue_deg=float(raw["hue_deg"]),
                hue_tol=float(raw["hue_tol"]),
                sat_min=float(raw["sat_min"]),
                val_min=float(raw["val_min"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.info("[craft] subject spec unusable: %s", exc)
            return None

    def _crop_plan(self, sb: dict) -> CropPlan | None:
        rows = sb.get("crop_plan") or []
        out: list[CropRow] = []
        for r in rows:
            try:
                out.append(
                    CropRow(
                        out=int(r["out"]),
                        mag=float(r["mag"]),
                        cx=float(r["cx"]),
                        cy=float(r["cy"]),
                        src_h=int(r["src_h"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                return None
        return CropPlan(rows=tuple(out)) if out else None

    def _render(self, sb: dict, matte: Any, context: StageContext, bid: str):
        from genlab_core.rendering import craft

        out_dir = Path(context.get("run_dir", ".")) / "craft"
        out_dir.mkdir(parents=True, exist_ok=True)
        return (
            craft.render_to(sb, matte, out_dir / f"{bid}.mp4")
            if hasattr(craft, "render_to")
            else None
        )

    def _deliver(
        self, bp: dict, sb: dict, result: Any, context: StageContext, bid: str, route: str
    ) -> None:
        """Write the dual package. NEVER touches visual_paths."""
        niche = context.get("niche_id", "")
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        root = Path(context.get("project_root", ".")) / ".deliverables" / "dual" / day / niche
        root.mkdir(parents=True, exist_ok=True)

        legacy = self._legacy_path(bp)
        craft_mp4 = getattr(result, "path", None) or str(result)
        if legacy and Path(legacy).exists():
            shutil.copy2(legacy, root / f"{bid}_legacy.mp4")
        if craft_mp4 and Path(craft_mp4).exists():
            shutil.copy2(craft_mp4, root / f"{bid}_craft.mp4")
            self._side_by_side(
                root / f"{bid}_legacy.mp4",
                root / f"{bid}_craft.mp4",
                root / f"{bid}_sidebyside.mp4",
            )
        (root / f"{bid}_storyboard.json").write_text(json.dumps(sb, indent=1, default=str))

        logger.info(
            "[craft] route=%s blueprint=%s mattes=%s path=%s",
            route,
            bid,
            getattr(result, "frames", "n/a"),
            craft_mp4,
        )

    @staticmethod
    def _legacy_path(bp: dict) -> str:
        paths = bp.get("visual_paths")
        if isinstance(paths, str):
            try:
                paths = json.loads(paths)
            except ValueError:
                return paths
        return paths[0] if isinstance(paths, list) and paths else ""

    @staticmethod
    def _side_by_side(left: Path, right: Path, dst: Path) -> None:
        if not (left.exists() and right.exists()):
            return
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-v",
                "error",
                "-y",
                "-i",
                str(left),
                "-i",
                str(right),
                "-filter_complex",
                "[0:v]scale=540:960,drawtext=text='LEGACY':x=20:y=20:fontsize=36:fontcolor=white[l];"
                "[1:v]scale=540:960,drawtext=text='CRAFT':x=20:y=20:fontsize=36:fontcolor=white[r];"
                "[l][r]hstack",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                "-crf",
                "20",
                str(dst),
            ],
            check=False,
            capture_output=True,
        )

    @staticmethod
    def _skip(stats: dict, reason: str, niche: str, bid: str = "", detail: str = "") -> None:
        """One counter per reason. A single 'craft_skipped' boolean would make
        the first dual fire unreadable — 'the worker was asleep' and 'provenance
        said STILL' need different actions."""
        stats["craft_skipped"] = stats.get("craft_skipped", 0) + 1
        key = f"craft_skipped_{reason}"
        stats[key] = stats.get(key, 0) + 1
        logger.info(
            "[craft] craft_skipped=%s niche=%s blueprint=%s %s", reason, niche, bid or "-", detail
        )
