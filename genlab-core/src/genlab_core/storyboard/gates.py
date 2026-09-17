"""Gates that run on the PLAN, before a frame is drawn.

Each of these was learned the expensive way -- by rendering something, measuring
it, and finding it wrong. All of them are properties of the storyboard, so they
can be checked in milliseconds instead of after a twelve-minute render.

The list is deliberately short. A gate belongs here only if the plan fully
determines the answer; anything that depends on pixels (matte area, loudness,
full-bleed photometrics) stays a post-render gate, because checking it here
would mean guessing.
"""

from __future__ import annotations

from dataclasses import dataclass

from genlab_core.storyboard.models import Scope, Storyboard, Treatment

# Measured on the reference VFX edit and on this platform's own renders.
MIN_CUTS_PER_S = 0.33          # below this the reel reads as one held clip
MAX_CUTS_PER_S = 1.50
MAX_QUIET_RUN_S = 0.80         # longest stretch with no effect event
MAX_HOOK_ONSET_S = 1.00        # the hook has to arrive before the scroll does
FLASH_FINISH_TOLERANCE = 3     # frames


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str

    def __str__(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


def _shot_density(sb: Storyboard) -> GateResult:
    if sb.treatment_is(Treatment.STILL):
        return GateResult("shot_density", True, "STILL: not gated on cuts")
    if sb.scope is Scope.SEGMENT:
        return GateResult("shot_density", True,
                          "SEGMENT: one continuous shot by selection (zero cuts)")
    cps = max(len(sb.shots) - 1, 0) / max(sb.duration_s, 1e-6)
    ok = MIN_CUTS_PER_S <= cps <= MAX_CUTS_PER_S
    return GateResult("shot_density", ok,
                      f"{cps:.3f} cuts/s over {sb.duration_s:.2f}s "
                      f"(want {MIN_CUTS_PER_S}-{MAX_CUTS_PER_S})")


def _longest_static(sb: Storyboard) -> GateResult:
    """The method-independent one. A 28-second still frame is a defect under
    any detector, which is why this is gated and raw cut counts are advisory."""
    if not sb.shots:
        return GateResult("longest_static", False, "no shots planned")
    longest = max(s.n_frames for s in sb.shots) / sb.fps
    ok = longest <= MAX_QUIET_RUN_S * 4
    return GateResult("longest_static", ok,
                      f"{longest:.2f}s longest shot (cap {MAX_QUIET_RUN_S * 4:.2f}s)")


def _quiet_run(sb: Storyboard) -> GateResult:
    if sb.treatment_is(Treatment.STILL):
        return GateResult("quiet_run", True, "STILL: not gated on event cadence")
    frames = sorted({e.frame for e in sb.events})
    if not frames:
        return GateResult("quiet_run", False, "no effect events planned")
    gaps = [frames[0]] + [b - a for a, b in zip(frames, frames[1:], strict=False)]
    gaps.append(sb.total_frames - frames[-1])
    longest = max(gaps) / sb.fps
    ok = longest <= MAX_QUIET_RUN_S
    return GateResult("quiet_run", ok,
                      f"{longest:.2f}s longest gap (cap {MAX_QUIET_RUN_S}s)")


def _hook_onset(sb: Storyboard) -> GateResult:
    if sb.scope is Scope.SEGMENT:
        return GateResult("hook_onset", True, "SEGMENT: the reel carries the hook")
    frames = [e.frame for e in sb.events]
    if not frames:
        return GateResult("hook_onset", False, "no events, so nothing arrives")
    onset = min(frames) / sb.fps
    ok = onset <= MAX_HOOK_ONSET_S
    return GateResult("hook_onset", ok,
                      f"first event at {onset:.2f}s (cap {MAX_HOOK_ONSET_S}s)")


def _plate_once(sb: Storyboard) -> GateResult:
    """A drawing/plate is a flash, not a motif. Twice reads as a transition."""
    n = len({e.frame for e in sb.events_of("drawing_flash")})
    runs = 0
    last = None
    for f in sorted({e.frame for e in sb.events_of("drawing_flash")}):
        if last is None or f - last > 2:
            runs += 1
        last = f
    ok = runs <= 1
    return GateResult("plate_once", ok,
                      f"{runs} drawing-flash run(s) across {n} frame(s)")


def _flash_at_finish(sb: Storyboard) -> GateResult:
    """Anchors move with the window. A flash tied to an offset fires on the
    wrong event the moment the window is re-picked -- measured: a drawing flash
    landed on the last three frames of the segment."""
    if sb.window is None or sb.window.finish_frame is None:
        return GateResult("flash_at_finish", True, "no finish frame declared")
    flashes = [e.frame for e in sb.events
               if e.kind in ("white_flash", "drawing_flash")]
    if not flashes:
        return GateResult("flash_at_finish", True, "no flash planned")
    centre = sum(flashes) / len(flashes)
    delta = abs(centre - sb.window.finish_frame)
    ok = delta <= FLASH_FINISH_TOLERANCE
    return GateResult("flash_at_finish", ok,
                      f"centre f{centre:.1f} vs finish f{sb.window.finish_frame} "
                      f"(|delta| {delta:.1f}, tol {FLASH_FINISH_TOLERANCE})")


def _ends_live(sb: Storyboard) -> GateResult:
    """Effects leave the picture; they do not sit on top of it at the end."""
    tail_start = sb.total_frames - 12
    # OVERLAYS only. `motion_blur` is deliberately absent: a directional smear
    # along the frame's own motion vector is the footage filmed differently, not
    # a graphic sitting on top of it, and banning it would reject a clip whose
    # finish simply lands late. Both approved builds emit it as an event, so the
    # omission is a decision and not an oversight.
    banned = {"drawing_flash", "white_flash", "mega_bolt", "bolt_afterglow",
              "debris", "heat_shimmer"}
    bad = [e for e in sb.events if e.frame >= tail_start and e.kind in banned]
    return GateResult("ends_live", not bad,
                      "tail is live" if not bad else
                      f"overlays in the final 12 frames: "
                      f"{[(e.frame, e.kind) for e in bad][:5]}")


def _magnification_floor(sb: Storyboard) -> GateResult:
    """No shot may ask for a magnification the geometry cannot reach."""
    if sb.full_bleed_floor is None:
        return GateResult("magnification_floor", True, "no floor declared")
    under = [s.index for s in sb.shots if s.magnification < sb.full_bleed_floor - 1e-6]
    return GateResult("magnification_floor", not under,
                      f"floor {sb.full_bleed_floor:.4f}x" if not under else
                      f"shots below the floor: {under[:6]}")


def _subject_vote(sb: Storyboard) -> GateResult:
    if sb.subject is None or not sb.subject.vote_frames:
        return GateResult("subject_vote", True, "no subject vote (not an ACTION plan)")
    ok = sb.subject.margin >= 0.8
    return GateResult("subject_vote", ok,
                      f"{sb.subject.votes}/{sb.subject.vote_frames} "
                      f"({sb.subject.margin:.0%}, want >=80%)")


_GATES = (_shot_density, _longest_static, _quiet_run, _hook_onset, _plate_once,
          _flash_at_finish, _ends_live, _magnification_floor, _subject_vote)


def check(sb: Storyboard) -> list[GateResult]:
    """Every plan-time gate, in order. Runs all of them -- an operator wants the
    whole table, not the first failure."""
    return [g(sb) for g in _GATES]


def failures(sb: Storyboard) -> list[GateResult]:
    return [r for r in check(sb) if not r.passed]
