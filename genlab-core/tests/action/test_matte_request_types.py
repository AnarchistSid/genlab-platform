"""A MatteRequest cannot express a partial spec.

The worker already refuses one at runtime (`seed_spec_incomplete`). These pins
move the refusal earlier: to the type, where it costs nothing and cannot be
skipped by a caller who never reaches the worker.

Why it matters: UFC-05's archive recorded `hue_deg` alone. The worker defaulted
sat/val, and on a DARK navy garment the value floor excluded the subject — the
seed landed on the red cage, eight of nine annotation frames were rejected as
garment, and half the clip came back unmasked. Hue alone is not a colour.
"""

from __future__ import annotations

import pytest
from genlab_core.action.matte_worker import (
    CropPlan,
    CropRow,
    HSVSpec,
    MatteRequest,
    seed_spec_complete,
)

SPEC = HSVSpec(hue_deg=235.0, hue_tol=25.0, sat_min=0.25, val_min=0.10)
PLAN = CropPlan(
    rows=tuple(CropRow(out=i, mag=2.0361, cx=0.65, cy=0.5, src_h=943) for i in range(3))
)


def test_a_request_without_a_subject_spec_is_unconstructable():
    with pytest.raises(TypeError):
        MatteRequest(clip_path="c.mp4", frames_dir="f")


def test_a_request_without_a_crop_plan_is_unconstructable():
    with pytest.raises(TypeError):
        MatteRequest(clip_path="c.mp4", frames_dir="f", subject_spec=SPEC)


@pytest.mark.parametrize("field", ["hue_deg", "hue_tol", "sat_min", "val_min"])
def test_every_hsv_field_is_required(field):
    kwargs = {"hue_deg": 235.0, "hue_tol": 25.0, "sat_min": 0.25, "val_min": 0.10}
    kwargs.pop(field)
    with pytest.raises(TypeError):
        HSVSpec(**kwargs)


@pytest.mark.parametrize("field", ["hue_deg", "hue_tol", "sat_min", "val_min"])
def test_a_None_hsv_field_is_rejected_not_defaulted(field):
    """The exact shape that broke UFC-05: a spec that LOOKS whole and carries a
    hole the callee fills with a default."""
    kwargs = {"hue_deg": 235.0, "hue_tol": 25.0, "sat_min": 0.25, "val_min": 0.10}
    kwargs[field] = None
    with pytest.raises(ValueError):
        HSVSpec(**kwargs)


def test_hue_out_of_range_is_rejected():
    with pytest.raises(ValueError):
        HSVSpec(hue_deg=400.0, hue_tol=25.0, sat_min=0.25, val_min=0.10)


def test_zero_hue_tolerance_is_rejected():
    """tol=0 selects nothing and reads as 'the subject colour is not present'."""
    with pytest.raises(ValueError):
        HSVSpec(hue_deg=235.0, hue_tol=0.0, sat_min=0.25, val_min=0.10)


def test_a_crop_row_requires_src_h():
    with pytest.raises(TypeError):
        CropRow(out=0, mag=2.0, cx=0.5, cy=0.5)


def test_an_empty_crop_plan_is_rejected():
    with pytest.raises(ValueError):
        CropPlan(rows=())


def test_the_job_payload_the_worker_receives_is_complete():
    """End of the chain: what actually goes on the wire passes the worker's own
    completeness check, so the two cannot drift apart."""
    req = MatteRequest(clip_path="c.mp4", frames_dir="f", subject_spec=SPEC, crop_plan=PLAN)
    job = req.as_job("job-1")
    assert seed_spec_complete(job["subject_colour"])
    assert job["n_frames"] == 3
    assert set(job["crop_plan"]) == {"0", "1", "2"}
    assert all("src_h" in row for row in job["crop_plan"].values())


def test_boolean_is_not_a_number_here():
    """`True` is an int in Python and would sail through an isinstance check."""
    with pytest.raises(ValueError):
        HSVSpec(hue_deg=235.0, hue_tol=25.0, sat_min=True, val_min=0.10)
