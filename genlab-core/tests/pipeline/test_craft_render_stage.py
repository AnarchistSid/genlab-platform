"""CraftRenderStage: renders beside legacy, publishes nothing, names every skip.

THE INVARIANT UNDER TEST
While craft_publishes is False, `visual_paths` must be byte-identical before and
after this stage. Craft is an observation running next to a render that has
already succeeded; anything it touches, it can break.

WHY EACH SKIP IS ITS OWN REASON
"Craft didn't run" is not a finding. "The worker was asleep" and "provenance
said STILL" need different actions, and a single boolean would have made the
first dual fire unreadable.
"""

from __future__ import annotations

import copy
import json

import pytest
from genlab_core.pipeline.stages.craft_render import CraftRenderStage, CraftSkip


def ctx(**kw):
    base = {
        "niche_id": "sports",
        "niche_config": {"render": {"dual": True}},
        "run_dir": "/tmp/run",
        "run_stats": {},
        "blueprints": [],
    }
    base.update(kw)
    return base


def bp(**kw):
    b = {"record_id": "bp1", "visual_paths": json.dumps(["/tmp/run/legacy.mp4"])}
    b.update(kw)
    return b


def _skips(c):
    return {k: v for k, v in c["run_stats"].get("render", {}).items() if k.startswith("craft_")}


# ── the invariant ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "blueprint",
    [
        bp(),  # no storyboard
        bp(storyboard={"routed_treatment": "STILL"}),  # not craftable
        bp(storyboard={"routed_treatment": "ACTION"}),  # no subject spec
    ],
)
def test_visual_paths_is_untouched_on_every_skip_path(blueprint):
    c = ctx(blueprints=[blueprint])
    before = copy.deepcopy(blueprint["visual_paths"])
    CraftRenderStage().execute(c)
    assert blueprint["visual_paths"] == before


def test_the_stage_returns_the_same_context_object():
    c = ctx(blueprints=[bp()])
    assert CraftRenderStage().execute(c) is c


# ── each skip is named and counted ──────────────────────────────────────────


def test_a_niche_running_neither_craft_nor_dual_is_not_requested():
    c = ctx(niche_config={"render": {"dual": False}}, blueprints=[bp()])
    CraftRenderStage().execute(c)
    assert _skips(c).get(f"craft_skipped_{CraftSkip.NOT_REQUESTED}") == 1


def test_a_blueprint_with_no_storyboard_says_so():
    c = ctx(blueprints=[bp()])
    CraftRenderStage().execute(c)
    assert _skips(c).get(f"craft_skipped_{CraftSkip.NO_STORYBOARD}") == 1


def test_a_still_route_is_not_craftable():
    c = ctx(blueprints=[bp(storyboard={"routed_treatment": "STILL"})])
    CraftRenderStage().execute(c)
    assert _skips(c).get(f"craft_skipped_{CraftSkip.ROUTE_NOT_CRAFTABLE}") == 1


def test_action_without_a_full_hsv_spec_skips_rather_than_defaulting():
    """Hue alone is not a colour — the UFC-05 failure. The stage must refuse,
    not fill in sat/val and post a job that mattes the cage."""
    sb = {"routed_treatment": "ACTION", "subject_colour": {"hue_deg": 235.0}}
    c = ctx(blueprints=[bp(storyboard=sb)])
    CraftRenderStage().execute(c)
    assert _skips(c).get(f"craft_skipped_{CraftSkip.NO_SUBJECT_SPEC}") == 1


def test_action_with_a_spec_but_no_crop_plan_skips():
    sb = {
        "routed_treatment": "ACTION",
        "subject_colour": {"hue_deg": 235.0, "hue_tol": 25.0, "sat_min": 0.25, "val_min": 0.1},
    }
    c = ctx(blueprints=[bp(storyboard=sb)])
    CraftRenderStage().execute(c)
    assert _skips(c).get(f"craft_skipped_{CraftSkip.NO_CROP_PLAN}") == 1


def test_a_crop_row_missing_src_h_is_not_silently_accepted():
    """src_h is what places the rect. UFC-05 archived a plan without it and the
    matte could not be rebuilt in reel space."""
    sb = {
        "routed_treatment": "ACTION",
        "subject_colour": {"hue_deg": 235.0, "hue_tol": 25.0, "sat_min": 0.25, "val_min": 0.1},
        "crop_plan": [{"out": 0, "mag": 2.0, "cx": 0.5, "cy": 0.5}],
    }
    c = ctx(blueprints=[bp(storyboard=sb)])
    CraftRenderStage().execute(c)
    assert _skips(c).get(f"craft_skipped_{CraftSkip.NO_CROP_PLAN}") == 1


def test_skip_counts_accumulate_across_blueprints():
    c = ctx(blueprints=[bp(record_id="a"), bp(record_id="b")])
    CraftRenderStage().execute(c)
    assert _skips(c)["craft_skipped"] == 2


# ── failures never escape ───────────────────────────────────────────────────


def test_an_exception_in_one_blueprint_does_not_break_the_run():
    """Legacy has already rendered. An observation stage must not raise into a
    pipeline that is about to publish."""

    class Exploding(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    c = ctx(blueprints=[Exploding()])
    CraftRenderStage().execute(c)  # must not raise
    assert c["run_stats"]["render"]["craft_skipped"] == 1


def test_no_blueprints_is_quiet_not_a_skip():
    c = ctx(blueprints=[])
    CraftRenderStage().execute(c)
    assert not _skips(c)
