"""Craft is additive. The worst outcome is a legacy reel and a recorded reason."""

import pytest
from genlab_core.action.matte_worker import (
    CropPlan,
    CropRow,
    HSVSpec,
    MatteRequest,
    MatteResult,
    SkipReason,
)
from genlab_core.rendering.craft_router import plan_render, record
from genlab_core.rendering.render_engine import Engine

# The request type now requires the whole subject spec and the crop plan — the
# worker refuses a partial spec, so the type will not build one.
REQ = MatteRequest(
    clip_path="/c.mp4",
    frames_dir="/f",
    subject_spec=HSVSpec(hue_deg=235.0, hue_tol=25.0, sat_min=0.25, val_min=0.10),
    crop_plan=CropPlan(rows=(CropRow(out=0, mag=2.0361, cx=0.65, cy=0.5, src_h=943),)),
    niche_id="sports",
)
LEGACY = {"render": {"engine": "legacy"}}
CRAFT = {"render": {"engine": "craft"}}
DUAL = {"render": {"engine": "legacy", "dual": True}}


def ok_matte(*a, **k):
    return MatteResult(job_id="j", mask_dir="/m", frames=384, seconds=540.0), ""


def skip(reason):
    def f(*a, **k):
        return None, reason

    return f


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in (
        "GENLAB_RENDER_ENGINE",
        "GENLAB_RENDER_ENGINE_SPORTS",
        "GENLAB_RENDER_DUAL",
        "GENLAB_RENDER_DUAL_SPORTS",
    ):
        monkeypatch.delenv(k, raising=False)


def test_a_legacy_niche_never_asks_the_worker_for_anything():
    called = []
    d = plan_render(
        "sports", LEGACY, REQ, request_fn=lambda *a, **k: called.append(1) or (None, "")
    )
    assert d.engine is Engine.LEGACY and called == []
    assert d.craft_attempted is False


def test_craft_publishes_when_the_mattes_arrive():
    d = plan_render("sports", CRAFT, REQ, request_fn=ok_matte)
    assert d.engine is Engine.CRAFT and d.craft_available
    assert d.matte.frames == 384


@pytest.mark.parametrize(
    "reason",
    [
        SkipReason.WORKER_UNAVAILABLE,
        SkipReason.TIMEOUT,
        SkipReason.FAILED,
        SkipReason.QUEUE_UNWRITABLE,
    ],
)
def test_every_worker_failure_falls_back_to_legacy(reason):
    """§2: worker unreachable or timed out -> that fire renders legacy."""
    d = plan_render("sports", CRAFT, REQ, request_fn=skip(reason))
    assert d.engine is Engine.LEGACY
    assert d.craft_skipped == reason and d.craft_attempted is True


def test_a_raising_matte_request_still_yields_a_legacy_reel():
    def boom(*a, **k):
        raise RuntimeError("ssh died mid-poll")

    d = plan_render("sports", CRAFT, REQ, request_fn=boom)
    assert d.engine is Engine.LEGACY and d.craft_skipped == "matte_request_error"


def test_dual_fetches_mattes_while_legacy_still_publishes():
    d = plan_render("sports", DUAL, REQ, request_fn=ok_matte)
    assert d.engine is Engine.LEGACY, "dual must not promote craft to publishing"
    assert d.craft_available, "dual should still produce the craft reel"


def test_dual_with_a_dead_worker_is_just_legacy():
    d = plan_render("sports", DUAL, REQ, request_fn=skip(SkipReason.WORKER_UNAVAILABLE))
    assert d.engine is Engine.LEGACY
    assert d.craft_skipped == SkipReason.WORKER_UNAVAILABLE


def test_craft_requested_without_a_matte_job_is_a_named_skip():
    d = plan_render("sports", CRAFT, None, request_fn=ok_matte)
    assert d.engine is Engine.LEGACY and d.craft_skipped == "no_matte_request"


def test_the_skip_is_counted_in_the_run_report():
    """A counter, not just a log line: three weeks of silent legacy looks the
    same as success in a log tail."""
    stats = {}
    record(plan_render("sports", CRAFT, REQ, request_fn=skip(SkipReason.TIMEOUT)), stats)
    r = stats["render"]
    assert r["engine"] == "legacy"
    assert r["craft_skipped"] == SkipReason.TIMEOUT
    assert r["craft_skipped_counts"][SkipReason.TIMEOUT] == 1


def test_repeated_skips_accumulate_rather_than_overwrite():
    stats = {}
    for _ in range(3):
        record(
            plan_render("sports", CRAFT, REQ, request_fn=skip(SkipReason.WORKER_UNAVAILABLE)), stats
        )
    assert stats["render"]["craft_skipped_counts"][SkipReason.WORKER_UNAVAILABLE] == 3


def test_a_successful_craft_run_records_the_matte_cost():
    stats = {}
    record(plan_render("sports", CRAFT, REQ, request_fn=ok_matte), stats)
    assert stats["render"]["matte_frames"] == 384
    assert stats["render"]["matte_seconds"] == 540.0
    assert "craft_skipped" not in stats["render"]


def test_plan_render_never_raises_whatever_the_config():
    for cfg in (None, {}, {"render": None}, {"render": {"engine": "nonsense"}}):
        d = plan_render("sports", cfg, REQ, request_fn=ok_matte)
        assert d.engine in (Engine.LEGACY, Engine.CRAFT)
