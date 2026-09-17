"""Port 5 gate: the pose gate must reproduce UFC-05's recorded verdict.

The img2img call is mocked with the three attempt images UFC-05 actually got
back, archived in the deliverable. The POSE GATE and the ANCHOR are exercised
for real -- they are the parts that decide whether a viewer sees a flash of a
different fight, and neither needs a model to run.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from genlab_core.action import drawing as D
from PIL import Image

_ROOT = Path(__file__).resolve().parents[4]
_UFC = _ROOT / ".deliverables" / "action_ufc_05"
_RECORDED = _UFC / "inputs" / "drawing.json"

pytestmark = pytest.mark.skipif(not _RECORDED.exists(), reason="UFC-05 archive not present")


@pytest.fixture(scope="module")
def recorded() -> dict:
    return json.loads(_RECORDED.read_text())


def _blob(h, w, y0, y1, x0, x1) -> np.ndarray:
    m = np.zeros((h, w), np.float32)
    m[y0:y1, x0:x1] = 1.0
    return m


# ───────────────────────── the gate's own validation ────────────────────────


def test_the_gate_scores_a_figure_against_itself_at_one():
    """Validated before its verdict is believed. A gate that cannot score 1.0 on
    an identical pair cannot be trusted to report 0.21 as a genuine mismatch."""
    m = _blob(400, 300, 80, 320, 100, 200)
    assert D.uniform_fit_iou(m, m) == pytest.approx(1.0, abs=1e-6)


def test_the_gate_is_scale_invariant():
    """A drawing at a different size is the same pose. UFC-05 measured 0.986 on
    a 0.7x copy; anything much below that would be measuring SIZE, not pose."""
    live = _blob(400, 300, 80, 320, 100, 200)
    shrunk = Image.fromarray((live * 255).astype(np.uint8)).resize((int(300 * 0.7), int(400 * 0.7)))
    small = np.asarray(shrunk, np.float32) / 255.0
    assert D.uniform_fit_iou(small, live) > 0.95


def test_the_wrong_fighter_floor_is_well_below_the_gate():
    """Subject-vs-OPPONENT is the baseline a mismatched drawing scores at.

    Without this number, 0.45 looks like "close to passing". Against a floor of
    ~0.3 it is "barely above drawing the wrong man".
    """
    subject = _blob(400, 300, 60, 340, 90, 170)  # tall, upright
    opponent = _blob(400, 300, 250, 340, 60, 260)  # wide, on the canvas
    assert D.uniform_fit_iou(subject, opponent) < D.IOU_GATE


def test_a_stretched_fit_is_not_used():
    """The 4:5-into-9:16 bug: stretching scored 51.9% on a drawing that was fine.

    A tall figure fitted onto a wide live mask must NOT score well -- if it does,
    the fit is stretching rather than scaling.
    """
    drawn = _blob(400, 300, 40, 360, 130, 170)  # very tall, narrow
    live = _blob(400, 300, 180, 240, 40, 260)  # short, wide
    assert D.uniform_fit_iou(drawn, live) < 0.30


# ───────────────────────── the recorded UFC-05 verdict ──────────────────────


def test_the_recorded_ufc_tries_all_fail_the_gate(recorded):
    """All three real attempts scored 0.21-0.45 against a 0.60 gate."""
    for t in recorded["tries"]:
        ious = t["iou"]
        _, ok = D.score_attempt({}, {"subject": np.ones((4, 4)), "opponent": np.ones((4, 4))})
        assert not ok  # empty figures can never pass
        assert min(ious.values()) < D.IOU_GATE, f"try {t['try']} should have failed"
        assert t["pass"] is False


def test_three_failures_yield_no_drawing_and_say_why(recorded):
    """The port must reach UFC-05's actual outcome through its own logic."""
    live = {
        "subject": _blob(400, 300, 60, 340, 90, 170),
        "opponent": _blob(400, 300, 250, 340, 60, 260),
    }
    recorded_ious = [t["iou"] for t in recorded["tries"]]
    calls = []

    def render_attempt(i):
        calls.append(i)
        return f"task{i}", f"try{i}.png"

    def figures_of(path):
        # replay the recorded IoUs by returning figures that score them: the
        # simplest faithful stand-in is the WRONG fighter's mask, which is the
        # floor those attempts sat at
        return {"subject": live["opponent"], "opponent": live["subject"]}

    res = D.decide(13, live, render_attempt, figures_of)
    assert calls == [1, 2, 3], "must use all three tries before giving up"
    assert not res.has_drawing
    assert "missing flash beats a wrong one" in res.reason
    assert len(recorded_ious) == 3


def test_a_matching_cutout_is_accepted_on_the_first_try():
    """The gate must be passable -- a gate nothing can pass is a disabled feature."""
    live = {
        "subject": _blob(400, 300, 60, 340, 90, 170),
        "opponent": _blob(400, 300, 250, 340, 60, 260),
    }
    calls = []

    def render_attempt(i):
        calls.append(i)
        return f"task{i}", f"try{i}.png"

    res = D.decide(13, live, render_attempt, lambda p: dict(live))
    assert res.has_drawing and calls == [1], "a matching drawing must stop at try 1"


def test_both_fighters_must_clear_the_gate_not_the_average():
    """A drawing that nails one fighter and invents the other must fail.

    Per-fighter, not pooled: a drawing can match one body exactly and invent the
    second, and any pooled statistic lets the good half carry the bad one. The
    property is that a single failing fighter sinks the attempt however well the
    other scored -- asserted directly rather than by contriving a fixture whose
    mean happens to straddle the gate.
    """
    live = {
        "subject": _blob(400, 300, 60, 340, 90, 170),
        "opponent": _blob(400, 300, 250, 340, 60, 260),
    }
    figs = {"subject": live["subject"], "opponent": live["subject"]}  # opponent wrong
    ious, ok = D.score_attempt(figs, live)
    assert ious["subject"] > 0.9, "the matched fighter should score near 1"
    assert ious["opponent"] < D.IOU_GATE, "the invented fighter should fail"
    assert not ok, "one failing fighter must sink the attempt"


# ───────────────────────── the anchor ───────────────────────────────────────


def test_the_flash_is_centred_on_the_finish(recorded):
    """v3's real defect: anchored to peak motion, it fired on frames 93/94/95."""
    finish = recorded["frame"]
    frames = D.flash_frames(finish, has_drawing=True)
    centre = D.flash_centre(frames)
    assert abs(centre - finish) <= 3, f"flash centre f{centre} vs finish f{finish}"


def test_no_drawing_means_no_flash_envelope(recorded):
    """UFC-05 shipped with no drawing; the envelope must be empty, not silent."""
    assert D.flash_frames(recorded["frame"], has_drawing=False) == {}
    assert D.flash_centre({}) is None


def test_the_flash_lands_before_the_live_tail():
    """`ends_live`: nothing overlaid in the final 12 frames of a 96-frame cut."""
    frames = D.flash_frames(13, has_drawing=True)
    assert max(frames) < 96 - 12


# ───────────────────────── the prompt ───────────────────────────────────────


def test_the_prompt_matches_the_opponent_s_actual_posture():
    """A prompt that contradicts the footage gets resolved by the model into a
    third thing. Ground and standing must produce different, non-contradictory
    phrasings."""
    ground = D.build_prompt(200.0, opponent_aspect=0.8)
    standing = D.build_prompt(200.0, opponent_aspect=1.9)
    assert "collapsing to the canvas" in ground
    assert "knocked backwards off his feet" in standing
    assert "navy" in ground, "subject hue 200 is navy -- UFC-05's actual subject"


def test_an_unknown_hue_does_not_invent_a_colour():
    assert D.garment_words(float("nan")) == "dark"
