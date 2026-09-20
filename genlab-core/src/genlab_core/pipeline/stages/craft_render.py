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

    #: TWO CONDITIONS MUST NOT SHARE ONE REASON STRING. `worker_unavailable`
    #: meant "the heartbeat is stale" AND "the builder could not construct a
    #: request" -- and for weeks it was the second while the worker was up and
    #: heartbeating. Each of these is produced by exactly one condition.
    PLAN_REQUEST_FAILED = "plan_request_failed"  # we could not describe the job
    PLAN_UNRESOLVED = "plan_unresolved"  # the worker answered, with a refusal


class CraftRenderStage:
    """Runs after the legacy render. Reads a storyboard, writes a second reel."""

    #: The context contract. Checked in tests/pipeline/test_stage_context_contract.py
    context_reads = (
        "blueprints",
        "stories",
        "niche_id",
        "niche_config",
        "storyboards",
        "run_dir",
        "project_root",
    )
    context_writes = ()

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
            # A STAGE'S SUCCESS IS JUDGED AGAINST THE RUN'S INPUT. "no
            # blueprints" on a five-story fire is a false green, and it read as
            # a normal empty queue at INFO for as long as this stage has
            # existed. Upstream count decides the level.
            upstream = len(context.get("stories") or [])
            if upstream:
                logger.warning(
                    "[craft] no blueprints (context key 'blueprints'), but the run "
                    "had %d story/stories upstream — the key is empty, missing, or "
                    "this stage runs before its producer",
                    upstream,
                )
            else:
                logger.info("[craft] no blueprints (stories=0)")
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
            # BUILD one rather than skip. Landing the builder without a caller
            # would repeat the defect this stage was written to fix: a plan with
            # nothing to plan for, one layer below a router with nothing to
            # route. Every refusal inside the builder comes back NAMED.
            built = self._build_storyboard(bp, context, bid)
            if not built.ok:
                # The builder's own reason, or -- when it failed because the
                # plan did -- the plan's, which names WHICH condition: a stale
                # heartbeat, a request we could not describe, or a worker that
                # answered with a refusal. One string, one condition.
                reason = (
                    getattr(self, "_plan_reason", "") or built.reason or CraftSkip.NO_STORYBOARD
                )
                self._skip(stats, reason, niche, bid, detail=built.detail)
                return
            sb = built.storyboard.model_dump()
            bp["storyboard"] = sb

        route = (sb.get("routed_treatment") or sb.get("treatment") or "").upper()
        if route not in ("ACTION", "TALK"):
            self._skip(stats, CraftSkip.ROUTE_NOT_CRAFTABLE, niche, bid, detail=route or "none")
            return

        if route == "ACTION":
            self._action(bp, sb, context, stats, bid)
        else:
            self._talk(bp, sb, context, stats, bid)

    def _action(self, bp: dict, sb: dict, context: StageContext, stats: dict, bid: str) -> None:
        """PLAN -> STORYBOARD -> RENDER.

        The plan already ran: `_build_storyboard` called the worker, and the
        storyboard was assembled FROM its answer. So the subject spec and the
        crop plan here are read off that storyboard, not invented before it --
        which is the ordering that was inverted, and why the request this stage
        built was the only one ever constructed while the one the builder needed
        never existed.
        """
        niche = context.get("niche_id", "")
        spec = self._subject_spec(sb)
        if spec is None:
            self._skip(stats, CraftSkip.NO_SUBJECT_SPEC, niche, bid)
            return
        plan = self._crop_plan(sb)
        if plan is None:
            self._skip(stats, CraftSkip.NO_CROP_PLAN, niche, bid)
            return

        mattes = (sb.get("plan") or {}).get("mask_dir") or sb.get("mask_dir")
        result = self._render(sb, mattes, context, bid)
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

    def _build_storyboard(self, bp: dict, context: StageContext, bid: str):
        """Plan this blueprint. The expensive half runs on the worker.

        `worker_fn` asks for the plan AND the mattes in one job (`plan=True`):
        the silhouette vote needs SAM2, SAM2 does not fit the VPS, and posting a
        second job for the mattes afterwards would double the round trip.
        """
        from genlab_core.action import storyboard_builder as sbuild

        def worker_fn():
            return self._plan_from_worker(bp, context, bid)

        def bed_fn():
            # 150 BPM at 30 fps is exactly 12 frames — an integer-frame grid, so
            # no event has to round onto it. The trim puts beat 0 on frame 0.
            bed = (bp.get("audio") or {}).get("bed") or {}
            return float(bed.get("bpm", 150.0)), float(bed.get("first_beat_s", 0.0))

        return sbuild.build(
            candidate=bp,
            niche_id=context.get("niche_id", ""),
            blueprint_id=bid,
            worker_fn=worker_fn,
            bed_fn=bed_fn,
            source_score=(bp.get("source_score") or {}).get("action_source_score"),
            sport=bp.get("sport"),
        )

    def _plan_from_worker(self, bp: dict, context: StageContext, bid: str):
        """BUILD the plan job, post it, return the plan. Or None, with a reason.

        THE PLAN PRECEDES THE STORYBOARD BY DEFINITION. This used to read
        `bp["_matte_request"]`, which nothing ever wrote -- and could not have:
        that request was constructed in `_action`, which runs AFTER the
        storyboard, which is built by calling this. Producer after consumer, and
        the key had no writer at all, so every ACTION blueprint skipped
        `worker_unavailable` against a live worker.

        Everything the job needs exists before any storyboard: the clip, the
        container's fps, the audio-anchored candidates, and a subject hint
        derived from a candidate's own first frame. The window, the subject spec
        and the finish are what the job RETURNS.
        """
        from genlab_core.action.matte_worker import request_matte

        self._plan_reason = ""
        clip = bp.get("clip_path") or (bp.get("media") or {}).get("clip_path") or ""
        if not clip or not Path(clip).exists():
            self._plan_reason = f"{CraftSkip.PLAN_REQUEST_FAILED}:no_clip"
            logger.info("[craft] %s: no source clip on disk (%r)", bid, clip)
            return None

        try:
            req = self._plan_request(clip, context, bid)
        except Exception as exc:  # noqa: BLE001 — craft never blocks a publish
            self._plan_reason = f"{CraftSkip.PLAN_REQUEST_FAILED}:{type(exc).__name__}"
            logger.warning("[craft] %s: could not describe the plan job: %s", bid, exc)
            return None
        if req is None:
            self._plan_reason = f"{CraftSkip.PLAN_REQUEST_FAILED}:{self._plan_detail}"
            return None

        result, reason = request_matte(req)
        if result is None or not result.ok:
            # The worker's OWN refusal, carried through by name:
            # vote_too_split, finish_unresolved, candidates_unscored...
            self._plan_reason = f"{CraftSkip.PLAN_UNRESOLVED}:{reason or 'no_reason'}"
            logger.info("[craft] worker declined the plan for %s: %s", bid, reason)
            return None
        return getattr(result, "plan", None)

    def _plan_request(self, clip: str, context: StageContext, bid: str):
        """The plan job: clip, anchored candidates, cuts, fps, `plan=True`.

        NO SUBJECT HINT. Deriving the garment colour needs a foreground matte,
        which needs rembg, which lives in the worker's venv and not on the VPS
        (rule #31 -- the project venv must not grow a torch stack). The worker
        derives the seed from the first candidate's own first frame, where the
        model already is. Sending a hue from here would mean either importing
        rembg onto the VPS or inventing a number, and an invented seed is what
        put a dark navy garment under a defaulted value floor once already.
        """
        from genlab_core.action import storyboard_builder as sbuild
        from genlab_core.action.finish import level_shift_anchors
        from genlab_core.action.window import container_fps, cut_times, motion_profile

        self._plan_detail = "unknown"
        # Part 29 §1 — ONE source for the frame rate, and it is the container.
        # This read `prof, fps = motion_profile(clip)`, which bound the clip's
        # DURATION to `fps`. Both are floats, so nothing complained. On
        # sports-c787edca13 (40.658 s, 60 fps) the plan then ran at "fps =
        # 40.66" in three places at once: the audio anchors, the worker's
        # decode rate, and — the one that picked the window — `motion_at`
        # indexing a per-frame profile at 40.658 samples/s instead of 59.91.
        # motion_at(12.65) scored the motion at 8.58 s and reported it as
        # 12.65 s, which is how a post-fight interview won the ranking.
        fps = container_fps(clip)
        profile = motion_profile(clip)
        prof = profile.values
        if not prof:
            self._plan_detail = "no_motion_profile"
            return None

        # The profile emits one sample per decoded frame, so its sample rate
        # IS the container rate. Assert rather than assume: a profile whose
        # length disagrees with duration x fps means one of the two lied, and
        # every index below would be silently wrong again.
        implied = len(prof) / profile.duration_s if profile.duration_s > 0 else 0.0
        if abs(implied - fps) > 1.0:
            self._plan_detail = f"profile_rate_mismatch:{implied:.2f} vs {fps:.2f}"
            logger.warning(
                "[craft] %s: motion profile implies %.2f samples/s but the container "
                "is %.2f fps — refusing to index it",
                bid,
                implied,
                fps,
            )
            return None

        anchors = level_shift_anchors(clip, 0.0, None, fps)
        if not anchors:
            self._plan_detail = "no_audio_anchor"
            logger.info("[craft] %s: no level-shift anchor in the clip's audio", bid)
            return None

        cuts = cut_times(clip)

        def motion_at(start: float) -> float:
            lo, hi = int(start * fps), int((start + sbuild.WINDOW_S) * fps)
            seg = prof[lo:hi]
            return float(sum(seg) / len(seg)) if seg else 0.0

        cands = sbuild.candidates_for_anchors(anchors, cuts, motion_at)
        if not cands:
            self._plan_detail = "no_zero_cut_candidate"
            logger.info("[craft] %s: every anchored window contains a cut", bid)
            return None

        return MatteRequest(
            clip_path=clip,
            frames_dir=str(Path(context.get("run_dir", ".")) / "frames" / bid),
            subject_spec=None,
            crop_plan=None,
            # SECONDS, not frames: the worker converts once it knows the window.
            cuts=[],
            cuts_s=[float(c) for c in cuts],
            niche_id=context.get("niche_id", ""),
            blueprint_id=bid,
            plan=True,
            candidates=cands,
            fps=float(fps),
        )

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
