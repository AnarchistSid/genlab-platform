"""Build the Storyboard the renderer executes. ACTION route, sports first.

RENDER-01 Part 12. `CraftRenderStage` skipped every blueprint at
`no_storyboard` because nothing produced one — a router with no plan to route,
which is a flag with no consumer one layer down.

THE SPLIT, AND WHY IT IS WHERE IT IS
------------------------------------
**VPS half** is everything ffmpeg and arithmetic can answer: source score,
provenance route, chrome band, window candidates by motion-AND-zero-cuts, kit.
Cheap, local, no model.

**Worker half** is everything SAM2 must answer: the silhouette vote for the
finisher, finisher presence across the window, the finish frame, and the mattes
themselves. Twenty SAM2 image calls do not fit a 2-core 4 GB box — it was
measured at 138 minutes for 96 frames, with OOM. The job returns the PLAN and
the MATTES together so the stage never posts a second one.

**Assemble** is VPS again: bed, beat grid, shot list, effect events, gates.

WHY MOTION *AND* ZERO CUTS
--------------------------
Motion alone returns a highlight MONTAGE — maximum motion, no continuity, and
nothing for the tracker to follow. Requiring zero cuts inside the window does
almost all of the selection work: over 754 s of UFC footage exactly two 3.2 s
windows qualified.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from genlab_core.action.kits.registry import family_for_niche
from genlab_core.action.router import route as route_candidate
from genlab_core.storyboard.models import (
    BeatGrid,
    ClassifierVerdict,
    EffectEvent,
    Scope,
    Shot,
    Storyboard,
    SubjectPlan,
    Treatment,
    WindowPlan,
)

logger = logging.getLogger(__name__)

#: A 3.2 s window at 30 fps. 96 frames, and 150 BPM gives a 12-frame beat that
#: divides it exactly — the integer-frame grid the ACTION template wants.
WINDOW_S = 3.2
FPS = 30
WINDOW_FRAMES = int(WINDOW_S * FPS)

#: Candidates handed to the worker. Three is the measured sweet spot: the vote
#: is the expensive part, and a fourth candidate rarely wins.
MAX_CANDIDATES = 3

#: CANDIDATES ARE GENERATED FROM THE INVARIANT SIGNAL AND RANKED BY THE PRECISE
#: ONE. The audio level shift is window-invariant; the strike precedes it by
#: ~0.3 s. Starting windows at these offsets before the anchor means every
#: candidate CONTAINS the finish by construction -- so the invariance gate has
#: n >= 2 on every plan, instead of the one candidate that survived before.
#:
#: Ranking by motion and SELECTING by motion are different jobs. Selecting by
#: motion excluded the archive's own window: measured on UFC-05 it scores 9.36
#: against 11.88 for the top window, because it was framed on the finish rather
#: than on the busiest three seconds.
#:
#: The smallest offset must EXCEED the measured lag, or the nearest window
#: starts after the strike. The lag is +0.317 s (UFC-05, level shift at 25.650
#: against a finish at 25.333), and an offset of 0.3 puts the window start at
#: 25.350 -- 0.017 s too late, which defeats the whole construction.
ANCHOR_OFFSETS_S = (0.4, 0.6, 0.9)

#: The finisher vote must be near-unanimous. 8/10 was the floor that separated a
#: real subject from a coin-flip on the UFC footage; below it the "winner" was a
#: 0.05pp area difference, i.e. noise.
VOTE_FLOOR = 8
VOTE_FRAMES = 10


class BuildSkip:
    ROUTE_NOT_ACTION = "route_not_action"
    NO_WINDOW = "no_window"
    WORKER_UNAVAILABLE = "worker_unavailable"
    VOTE_TOO_SPLIT = "vote_too_split"
    GATES_FAILED = "storyboard_rejected"


@dataclass
class BuildResult:
    storyboard: Storyboard | None = None
    reason: str = ""
    detail: str = ""
    gates: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.storyboard is not None


# ── VPS half ────────────────────────────────────────────────────────────────


def window_candidates(cuts_s, duration_s, motion_at, *, n=MAX_CANDIDATES):
    """Top-n zero-cut windows by motion.

    `motion_at(start)` scores a window; `cuts_s` are scene-change times. A
    window containing a cut is discarded outright rather than penalised — a cut
    inside the window is a different shot, and SAM2 walks from one fighter to
    the other across it.

    Returns ``[{"start_s", "motion_score"}, ...]``, highest motion first. The
    SCORE TRAVELS WITH THE WINDOW. It used to be computed here, used to sort,
    and then discarded -- so the worker, which ranks candidates by motion before
    the vote, had nothing to rank by. Filling that gap by hand is how three
    invented scores excluded the archive's own window from a verification run
    and produced a false area-band failure.
    """
    out = []
    start = 0.0
    while start + WINDOW_S <= duration_s:
        end = start + WINDOW_S
        if not any(start < c < end for c in cuts_s):
            out.append((float(motion_at(start)), round(start, 3)))
        start += 0.4
    out.sort(reverse=True)
    return [{"start_s": s, "motion_score": m} for m, s in out[:n]]


def _candidate(c) -> dict:
    """One candidate for the worker. `motion_score` is REQUIRED downstream — the
    worker refuses a job without it rather than ranking on a default."""
    if isinstance(c, dict):
        row = {
            "start_s": float(c["start_s"]),
            "frames": WINDOW_FRAMES,
            "motion_score": float(c["motion_score"]),
        }
        if c.get("anchor_s") is not None:
            row["anchor_s"] = float(c["anchor_s"])
        return row
    raise TypeError(
        f"candidate must carry its measured motion_score, got {c!r}. "
        "window_candidates() returns these; do not build them by hand."
    )


def audio_anchored_candidates(anchor_s, cuts_s, motion_at, *, offsets=ANCHOR_OFFSETS_S):
    """Windows that all contain the finish, ranked by motion.

    `anchor_s` is the audio level shift in CLIP time. Each offset starts a
    window that far before it; a window containing a cut is discarded outright,
    because a cut is a different shot and the tracker walks from one fighter to
    the other across it.

    Returns ``[{"start_s", "motion_score"}, ...]``, highest motion first.
    """
    out = []
    for off in offsets:
        start = round(anchor_s - off, 3)
        if start < 0:
            continue
        end = start + WINDOW_S
        if any(start < c < end for c in cuts_s):
            logger.info("[builder] candidate at %.2fs contains a cut — discarded", start)
            continue
        out.append({"start_s": start, "motion_score": float(motion_at(start))})
    out.sort(key=lambda c: -c["motion_score"])
    if len(out) < 2:
        logger.warning(
            "[builder] only %d anchored candidate(s) survived the cut filter — "
            "the invariance gate cannot run",
            len(out),
        )
    return out


def candidates_for_anchors(anchors, cuts_s, motion_at, *, offsets=ANCHOR_OFFSETS_S):
    """Candidates across EVERY anchor, tagged with the anchor they came from.

    An argmax anchor assumes one event per clip. Measured on the UFC-05 span
    there are five comparable crowd rises in 60 s and the wanted one ranks
    third, so the anchor is a ranked list and the candidates are the union.

    The vote, presence and the invariance gate choose among them; whichever
    candidate wins names its anchor, so the log says which event was rendered
    rather than only which three seconds.
    """
    out = []
    seen = set()
    for a in anchors:
        t = float(a[0]) if isinstance(a, (tuple, list)) else float(a)
        for c in audio_anchored_candidates(t, cuts_s, motion_at, offsets=offsets):
            if c["start_s"] in seen:
                continue
            seen.add(c["start_s"])
            out.append({**c, "anchor_s": round(t, 3)})
    out.sort(key=lambda c: -c["motion_score"])
    logger.info(
        "[builder] %d candidate(s) across %d anchor(s): %s",
        len(out),
        len(anchors),
        ", ".join(f"{c['start_s']:.2f}<-{c['anchor_s']:.2f}" for c in out),
    )
    return out


def build_plan_request(clip_path, candidates, subject_hint, niche_id, blueprint_id, src_h):
    """The single worker job: vote + window pick + mattes, in one round trip."""
    from genlab_core.action.matte_worker import MatteRequest

    return {
        "kind": "plan+matte",
        "clip_path": clip_path,
        "candidates": [_candidate(c) for c in candidates],
        "subject_hint": subject_hint,
        "vote_frames": VOTE_FRAMES,
        "vote_floor": VOTE_FLOOR,
        "src_h": src_h,
        "niche_id": niche_id,
        "blueprint_id": blueprint_id,
        "plan": True,
        "_request_type": MatteRequest.__name__,
    }


# ── assemble ────────────────────────────────────────────────────────────────


def shot_list(finish_frame: int, total: int, mag_for) -> list[Shot]:
    """CONTENT-11 §6's structure, laid out CONTIGUOUSLY.

    cold open on the finish -> rewind stutter -> build -> ramp -> the hit ->
    aftermath -> stand-over -> out.

    Built from BOUNDARIES rather than fixed spans. A fixed-span table assumes the
    finish sits mid-window; UFC-05's window was re-picked to START at the finish
    (frame 13 of 96), which inverted the ramp span, dropped it, and left a hole
    between "build" and "hit". Shots that do not tile the window are not a
    cosmetic problem — the renderer reads them to decide what each frame shows.

    Magnification is PER SHOT from that shot's own torso, never one global value:
    a single 3.0x taken from the median was right for the median shot and wrong
    for every other one.
    """
    if total <= 0:
        return []
    finish = max(0, min(finish_frame, total - 1))

    # Boundaries in ascending order; duplicates and out-of-range collapse.
    raw = [
        ("cold_open", 0),
        ("rewind", min(20, finish)),
        ("build", min(32, finish)),
        ("ramp", max(finish - 4, 0)),
        ("hit", finish),
        ("aftermath", min(finish + 9, total - 1)),
        ("stand_over", min(finish + 31, total - 1)),
        ("out", max(total - 12, 0)),
    ]
    bounds: list[tuple[str, int]] = []
    for name, f in raw:
        if not bounds or f > bounds[-1][1]:
            bounds.append((name, f))

    #: Which source frame a shot replays, when it is not simply itself. The cold
    #: open shows the finish before the reel has earned it; the rewind steps back
    #: toward it.
    replay = {"cold_open": finish, "rewind": max(finish - 8, 0)}

    shots: list[Shot] = []
    for i, (name, a) in enumerate(bounds):
        b = (bounds[i + 1][1] - 1) if i + 1 < len(bounds) else (total - 1)
        if b < a:
            continue
        shots.append(
            Shot(
                index=len(shots),
                start_frame=a,
                end_frame=b,
                magnification=mag_for(a),
                centre_x=0.5,
                centre_y=0.5,
                source_frame=replay.get(name),
            )
        )
    return shots


def effect_events(finish_frame, total, period_frames=12, has_drawing=False) -> list[EffectEvent]:
    """Events on the grid, and the flash on the FINISH.

    v3 anchored the flash to peak motion; when the window was re-picked to start
    at the finish the anchor stayed put and the flash fired on the
    follow-through, over the live ending. Anchor to the event, not the energy.
    """
    ev: list[EffectEvent] = []
    for f in range(0, total, period_frames):
        ev.append(EffectEvent(kind="aura", frame=f, on_beat=True))
        if f % (period_frames * 4) == 0:
            ev.append(EffectEvent(kind="mega_bolt", frame=f, on_beat=True))
    ev.append(EffectEvent(kind="white_flash", frame=finish_frame, detail="on the finish"))
    for off in range(4, 20):
        ev.append(EffectEvent(kind="debris", frame=finish_frame + off, detail=f"age {off}"))
    if has_drawing:
        for off, op in ((1, 0.85), (2, 0.85), (3, 0.60), (4, 0.30)):
            ev.append(EffectEvent(kind="drawing_flash", frame=finish_frame + off, detail=str(op)))
    # Nothing overlaid in the final 12 frames: the segment ends LIVE.
    tail = total - 12
    return [e for e in ev if not (e.frame >= tail and e.kind != "aura")]


def assemble(
    *, niche_id, blueprint_id, plan, kit, bpm, first_beat_s, classifier=None, scope=Scope.SEGMENT
) -> Storyboard:
    """Worker plan + bed -> the typed Storyboard the renderer executes."""
    total = int(plan["frames"])
    finish = int(plan["finish_frame"])
    period = round(60.0 / bpm * FPS, 4)

    def mag_for(frame: int) -> float:
        return float(plan.get("magnification", {}).get(str(frame), plan.get("mag", 2.0)))

    sb = Storyboard(
        niche_id=niche_id,
        scope=scope,
        blueprint_id=blueprint_id,
        fps=FPS,
        total_frames=total,
        kit=kit or "",
        classifier=classifier
        or ClassifierVerdict(
            treatment=Treatment.ACTION,
            speech_ratio=0.0,
            motion_energy=float(plan.get("motion", 0.0)),
            face_persistence=0.0,
            confidence=1.0,
            reason="routed by provenance",
        ),
        window=WindowPlan(
            start_s=float(plan["start_s"]),
            duration_s=total / FPS,
            motion=float(plan.get("motion", 0.0)),
            cuts=len(plan.get("cuts") or []),
            subject_hold=float(plan.get("finisher_presence", 0.0)),
            source_rect=tuple(plan["video_rect"]) if plan.get("video_rect") else None,
            finish_frame=finish,
        ),
        subject=SubjectPlan(
            hue_deg=float(plan["subject_colour"]["hue_deg"]),
            votes=int(plan.get("votes", 0)),
            vote_frames=int(plan.get("vote_frames", VOTE_FRAMES)),
            unanimous=int(plan.get("votes", 0)) >= int(plan.get("vote_frames", VOTE_FRAMES)),
            reason=plan.get("vote_reason", ""),
        ),
        beats=BeatGrid(
            bpm=float(bpm), first_beat_s=float(first_beat_s), period_frames=period, fps=FPS
        ),
        shots=shot_list(finish, total, mag_for),
        events=effect_events(
            finish, total, int(round(period)), has_drawing=bool(plan.get("drawing"))
        ),
        full_bleed_floor=plan.get("full_bleed_floor"),
        grade=plan.get("grade") or {},
        drawing=plan.get("drawing"),
    )
    return sb


def build(
    *, candidate, niche_id, blueprint_id, worker_fn, bed_fn, source_score=None, sport=None
) -> BuildResult:
    """The whole job. Returns a Storyboard or a NAMED reason it has none."""
    decision = route_candidate(candidate, source_score=source_score, sport=sport)
    if decision.treatment != "ACTION":
        return BuildResult(reason=BuildSkip.ROUTE_NOT_ACTION, detail=decision.reason)

    plan = worker_fn()
    if not plan:
        return BuildResult(reason=BuildSkip.WORKER_UNAVAILABLE)
    votes, vframes = int(plan.get("votes", 0)), int(plan.get("vote_frames", VOTE_FRAMES))
    if votes < VOTE_FLOOR:
        # A split vote means the tracker cannot tell the fighters apart; a reel
        # built on the wrong one is worse than no reel.
        return BuildResult(reason=BuildSkip.VOTE_TOO_SPLIT, detail=f"{votes}/{vframes}")

    bpm, first_beat_s = bed_fn()
    sb = assemble(
        niche_id=niche_id,
        blueprint_id=blueprint_id,
        plan=plan,
        kit=family_for_niche(sport or niche_id) or "",
        bpm=bpm,
        first_beat_s=first_beat_s,
    )

    # The nine plan-time gates. Run them ALL and report the first failure with
    # the rest attached: "rejected" is not a finding, "rejected at ends_live" is.
    from genlab_core.storyboard.gates import check as run_gates

    results = run_gates(sb)
    failed = [g for g in results if not g.passed]
    if failed:
        return BuildResult(
            reason=f"{BuildSkip.GATES_FAILED}:{failed[0].name}",
            detail="; ".join(f"{g.name}: {g.detail}" for g in failed[:3]),
            gates=results,
        )
    return BuildResult(storyboard=sb, gates=results)
