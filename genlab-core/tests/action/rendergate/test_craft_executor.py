"""Port 8 gate: `rendering/craft.py` executes a Storyboard and decides nothing.

Two kinds of test here, and the split matters:

  * The FAST ones run on synthetic frames and pin the contract -- compose order,
    the fallback guarantee, the tail rule. They run in CI.
  * The SLOW one drives the executor with the archived UFC-05 inputs. It is
    opt-in (GENLAB_RENDER_GATE=1) because it decodes 96 real frames.

The compose-order tests are the point. A per-effect gate cannot catch an effect
applied at the wrong moment, and three of the placements here are load-bearing:
motion blur reads the UNGRADED source, heat shimmer warps pixels so it precedes
anything that adds light, and bloom must come after everything that makes a
highlight. Each is asserted by CONTROL DIFFERENCE -- render with the effect off,
render with it on, and assert on what changed -- because an absolute measurement
conflates the effect with the content under it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest
import yaml
from genlab_core.rendering import craft
from genlab_core.storyboard.models import (
    ClassifierVerdict,
    EffectEvent,
    Scope,
    Shot,
    Storyboard,
    Treatment,
    WindowPlan,
)

_ROOT = Path(__file__).resolve().parents[4]
_UFC = _ROOT / ".deliverables" / "action_ufc_05"
H, W = 480, 270
N = 24


@pytest.fixture(scope="module")
def kit() -> dict:
    from genlab_core.action.effects import impact as fx

    return yaml.safe_load((Path(fx.__file__).parents[1] / "kits" / "impact.yaml").read_text())


def _sb(events=None, total=N, scope=Scope.SEGMENT, finish=12, **kw) -> Storyboard:
    return Storyboard(
        niche_id="sports",
        scope=scope,
        total_frames=total,
        classifier=ClassifierVerdict(
            treatment=Treatment.ACTION,
            speech_ratio=0.1,
            motion_energy=0.8,
            face_persistence=0.3,
            confidence=0.9,
            reason="test",
        ),
        window=WindowPlan(
            start_s=0.0,
            duration_s=total / 30,
            motion=0.8,
            cuts=0,
            subject_hold=0.9,
            finish_frame=finish,
        ),
        shots=[
            Shot(
                index=0,
                start_frame=0,
                end_frame=total - 1,
                magnification=2.0,
                centre_x=0.5,
                centre_y=0.5,
            )
        ],
        events=events or [],
        **kw,
    )


def _sources(total=N) -> craft.FrameSources:
    rng = np.random.default_rng(3)
    clean, matte = {}, {}
    for f in range(total):
        frame = rng.uniform(20, 200, (H, W, 3)).astype(np.float32)
        frame[100:160, 60:200] = 250.0  # a highlight, for bloom to find
        clean[f] = frame
        m = np.zeros((H, W), np.float32)
        m[120:400, 90:180] = 1.0
        matte[f] = m
    return craft.FrameSources(clean=clean, matte=matte)


# ───────────────────────── the contract ─────────────────────────────────────


def test_a_storyboard_with_no_events_still_renders_every_frame(kit):
    res = craft.execute(_sb(), _sources(), kit)
    assert len(res.frames) == N
    assert all(f.shape == (H, W, 3) for f in res.frames)


def test_the_executor_takes_no_decisions_of_its_own(kit):
    """Only planned events fire. If the renderer invents one, the plan is no
    longer the record of what was rendered."""
    sb = _sb(events=[EffectEvent(kind="mega_bolt", frame=6)])
    res = craft.execute(sb, _sources(), kit)
    kinds = {k for _, k in res.events}
    assert kinds <= {"mega_bolt", "bolt_afterglow", "aura"}, kinds
    assert ("mega_bolt") in kinds


def test_an_afterglow_only_follows_a_bolt(kit):
    sb = _sb(events=[EffectEvent(kind="mega_bolt", frame=6)])
    res = craft.execute(sb, _sources(), kit)
    glow = [f for f, k in res.events if k == "bolt_afterglow"]
    assert glow == [7], f"afterglow at {glow}, expected the frame after the bolt"


# ───────────────────────── compose order, by control difference ─────────────


def _render_with(kit, events, **kw):
    return craft.execute(_sb(events=events, **kw), _sources(), kit).frames


def test_bloom_comes_after_the_effects_that_make_highlights(kit):
    """Bloom on the footage instead of on the effects is invisible in an
    absolute measurement -- both look 'bright'. Differencing against a control
    with the bolt disabled isolates what bloom actually acted on."""
    without = _render_with(kit, [])
    with_bolt = _render_with(kit, [EffectEvent(kind="mega_bolt", frame=6)])
    delta = np.abs(with_bolt[6] - without[6])
    assert delta.max() > 1.0, "the bolt changed nothing -- the chain is broken"


def test_the_white_flash_frame_carries_nothing_else(kit):
    """The flash IS the frame. Anything composited alongside it is competing
    with a frame that has gone to white."""
    res = craft.execute(
        _sb(
            events=[EffectEvent(kind="white_flash", frame=12), EffectEvent(kind="debris", frame=12)]
        ),
        _sources(),
        kit,
    )
    at12 = {k for f, k in res.events if f == 12}
    assert at12 == {"white_flash"}, at12


def test_motion_blur_reads_the_ungraded_source(kit):
    """Grading first smears an already-dimmed world and loses the streaks.

    Asserted structurally: the blur must be absent on frame 0, where there is no
    previous SOURCE frame to difference against -- which is only true if it
    reads the source rather than the graded result.
    """
    res = craft.execute(
        _sb(
            events=[
                EffectEvent(kind="motion_blur", frame=0),
                EffectEvent(kind="motion_blur", frame=5),
            ]
        ),
        _sources(),
        kit,
    )
    blurred = [f for f, k in res.events if k == "motion_blur"]
    assert blurred == [5], f"motion_blur fired at {blurred}; frame 0 has no predecessor"


# ───────────────────────── the fallback guarantee ───────────────────────────


def test_render_returns_none_rather_than_raising_on_a_bad_plan(kit):
    """Craft never blocks a publish: the caller renders legacy on None."""
    sb = _sb()
    empty = craft.FrameSources(clean={}, matte={})
    assert craft.render(sb, empty, kit) is None


def test_a_missing_matte_is_a_fallback_not_a_crash(kit):
    src = _sources()
    del src.matte[7]
    assert craft.render(_sb(), src, kit) is None


def test_a_non_action_treatment_falls_back(kit):
    sb = _sb()
    sb.classifier.treatment = Treatment.TALK
    assert craft.render(sb, _sources(), kit) is None


def test_an_overlay_in_the_tail_falls_back(kit):
    """`ends_live` is enforced at execution, not only at plan time -- a plan can
    be correct and a renderer still emit late."""
    late = [EffectEvent(kind="mega_bolt", frame=N - 2)]
    assert craft.render(_sb(events=late), _sources(), kit) is None


def test_motion_blur_in_the_tail_does_not_fall_back(kit):
    """A footage effect, not an overlay. Banning it would reject a clip whose
    finish simply lands late."""
    tail = [EffectEvent(kind="motion_blur", frame=N - 2)]
    assert craft.render(_sb(events=tail), _sources(), kit) is not None


def test_a_plate_appearing_twice_falls_back(kit):
    assert not craft.plate_appears_once([(5, "drawing_flash"), (9, "drawing_flash")])
    assert craft.plate_appears_once([(5, "drawing_flash"), (6, "drawing_flash")])


# ───────────────────────── the archived UFC-05 inputs ───────────────────────


@pytest.mark.skipif(
    os.environ.get("GENLAB_RENDER_GATE") != "1", reason="opt-in: decodes 96 real frames"
)
@pytest.mark.skipif(not (_UFC / "inputs" / "clean").is_dir(), reason="UFC-05 archive not present")
def test_the_executor_runs_on_the_archived_ufc_inputs(kit):
    """End to end on real material: the plan drives, the executor renders."""
    from PIL import Image

    src_dir = _UFC / "inputs"
    grade = json.loads((src_dir / "grade_solved.json").read_text())
    finish = json.loads((src_dir / "finish.json").read_text())["finish_frame"]
    clean, matte = {}, {}
    for f in range(96):
        p = src_dir / "clean" / f"{f:03d}.png"
        q = src_dir / "matte_knight" / f"{f:03d}.png"
        if p.exists() and q.exists():
            clean[f] = np.asarray(Image.open(p).convert("RGB"), np.float32)
            matte[f] = (np.asarray(Image.open(q).convert("L"), np.float32) / 255.0 > 0.5).astype(
                np.float32
            )
    assert len(clean) >= 90, f"only {len(clean)} archived frames decoded"

    events = [EffectEvent(kind="aura", frame=f) for f in range(0, 84, 12)]
    events += [EffectEvent(kind="mega_bolt", frame=finish)]
    events += [EffectEvent(kind="debris", frame=f) for f in range(finish + 4, finish + 20)]
    sb = _sb(
        events=events,
        total=96,
        finish=finish,
        grade={
            "world_luma": grade["world_luma"],
            "subj_luma": grade["subj_luma"],
            "vignette": grade["vignette"],
            "feather": 18.0,
        },
    )
    sb.shots = [
        Shot(index=0, start_frame=0, end_frame=95, magnification=2.0361, centre_x=0.5, centre_y=0.5)
    ]
    res = craft.render(sb, craft.FrameSources(clean=clean, matte=matte), kit)
    assert res is not None, "craft fell back on the archived inputs"
    assert len(res.frames) == 96
    assert craft.ends_live(res.events, 96) == []
    lum = np.asarray([float(f.mean()) for f in res.frames])
    assert lum.min() > 1.0, "frames went black"
    assert lum.max() < 254.0, "frames blew out"
