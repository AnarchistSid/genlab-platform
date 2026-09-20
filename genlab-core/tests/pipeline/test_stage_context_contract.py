"""A stage may not read a context key that nothing earlier writes.

THE DEFECT. `CraftRenderStage` read `context["blueprints"]`. `PushToBacklog`
creates the blueprints and ran FIVE STAGES LATER, and did not publish them under
that name anyway. The read returned nothing, the stage took its empty-input
branch, logged at INFO and reported success -- on every fire since it shipped,
including a five-story run. Both sides are `dict[str, Any]`, so no type caught
it; the unit test built the context by hand and put the key in, so no test did.

These pins are the class fix. The symptom fix lives in the stages: no input is a
WARNING when upstream had input.
"""

from __future__ import annotations

import pytest
import yaml
from genlab_core.pipeline.stage_contract import OpaqueStage, check_order, coverage

TEMPLATE = "genlab-core/config/pipeline_template.yaml"


def _template_classes() -> list[str]:
    """Class paths in template order, injects left as markers."""
    doc = yaml.safe_load(open(TEMPLATE))
    out: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if "class" in node:
                out.append(node["class"])
            elif "inject" in node:
                out.append(f"<inject:{node['inject']}>")
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    return out


def _load(path: str):
    mod, _, cls = path.rpartition(".")
    import importlib

    return getattr(importlib.import_module(mod), cls)


@pytest.fixture(scope="module")
def ordered_stages():
    """Real stage classes in template order. Injects are niche-specific and
    contribute no declarations, so they are skipped rather than guessed at."""
    out = []
    for path in _template_classes():
        if path.startswith("<inject:"):
            # Kept, as an OPAQUE stage. Dropping injects silently would make
            # later reads look unwritten; pretending to know what they write
            # would make the check unsound the other way.
            out.append(OpaqueStage)
            continue
        try:
            out.append(_load(path))
        except Exception:  # noqa: BLE001 — a stage that will not import is another test's problem
            continue
    return out


def test_the_template_order_satisfies_every_declared_read(ordered_stages):
    violations = check_order(ordered_stages)
    assert not violations, "\n".join(str(v) for v in violations)


def test_craft_runs_after_push_to_backlog(ordered_stages):
    """The ordering this packet fixed, pinned by position rather than by the
    absence of a violation — so a future reorder is a failure, not a silence."""
    names = [s.__name__ for s in ordered_stages]
    assert "PushToBacklog" in names and "CraftRenderStage" in names
    assert names.index("PushToBacklog") < names.index("CraftRenderStage"), names


def test_the_pre_part_21_order_is_rejected(ordered_stages):
    """Craft before push — exactly what shipped — must be a violation."""
    names = [s.__name__ for s in ordered_stages]
    craft = ordered_stages[names.index("CraftRenderStage")]
    push = ordered_stages[names.index("PushToBacklog")]
    broken = [s for s in ordered_stages if s not in (craft, push)]
    broken = [craft, push] + broken  # craft first, as it was
    violations = check_order(broken)
    assert any(v.stage == "CraftRenderStage" and v.key == "blueprints" for v in violations), (
        violations
    )
    assert any("runs LATER" in v.detail for v in violations), violations


def test_removing_the_producers_write_is_rejected(ordered_stages):
    """If PushToBacklog stops publishing `blueprints`, craft silently no-ops
    again. Counting them was never the same as publishing them."""

    class PushWithoutTheJoin:
        context_reads = ("stories",)
        context_writes = ()

    names = [s.__name__ for s in ordered_stages]
    craft = ordered_stages[names.index("CraftRenderStage")]
    violations = check_order([PushWithoutTheJoin, craft])
    assert any(v.key == "blueprints" for v in violations), violations


def test_a_seeded_key_needs_no_writer(ordered_stages):
    """`niche_id` is put in by the runner. A check that demanded a writer for it
    would be noise, and noise is how a real finding gets ignored."""

    class ReadsSeeded:
        context_reads = ("niche_id", "run_dir")
        context_writes = ()

    assert check_order([ReadsSeeded]) == []


def test_the_check_covers_something(ordered_stages):
    """A contract check whose coverage is zero passes everything. This asserts
    it is actually looking at the stages in the failure, and prints what is
    still undeclared so the backfill is visible rather than assumed."""
    declared, undeclared = coverage(ordered_stages)
    assert "PushToBacklog" in declared, declared
    assert "CraftRenderStage" in declared, declared
    print(f"\n  declared: {len(declared)}  undeclared: {len(undeclared)}")
    print("  still undeclared: " + ", ".join(sorted(undeclared)))


# ── the blueprint must carry the footage the router asks for ────────────────


def test_the_blueprint_carries_the_local_clip_path():
    """MEASURED on prod: a sports blueprint had video_url='https://v.redd.it/…'
    and clip_path=None, on a run where DownloadTopVideos reported 3/3
    downloaded. `router.route()` asks `candidate.get("clip_path")`, saw None,
    and returned "no footage" for all three — a supply conclusion from a wiring
    fault.

    Pinned at the PRODUCER. The consumers were always reading the right name;
    nothing wrote it.
    """
    import inspect

    from genlab_core.pipeline.stages import push_to_backlog

    src = inspect.getsource(push_to_backlog)
    assert '"clip_path": (' in src, "PushToBacklog no longer writes clip_path"
    assert '"clip_index"' in src, "clip_path must come from the clip index"


def test_the_router_and_the_producer_agree_on_the_name():
    """The alias that never existed. If either side is renamed, this fails
    rather than routing every blueprint to STILL."""
    import inspect

    from genlab_core.action import router
    from genlab_core.pipeline.stages import push_to_backlog

    assert 'candidate.get("clip_path")' in inspect.getsource(router)
    assert '"clip_path"' in inspect.getsource(push_to_backlog)


def test_every_field_the_action_chain_reads_is_written_by_the_producer():
    """Derived by introspection, so it cannot drift.

    Fixing `clip_path` alone moved the verdict from "no footage" to "no
    highlight flag" — one gate of progress, because a propagator is a CHAIN and
    each link fails the same silent way. This asserts the whole set at once.

    `source_score` and `footage_free` are exempt and named: nothing computes
    them per story, and route() fails open on an unscored source with a reason.
    """
    import inspect
    import re

    from genlab_core.action import router
    from genlab_core.pipeline.stages import craft_render, push_to_backlog

    reads = set(re.findall(r'candidate\.get\("([a-z_]+)"\)', inspect.getsource(router)))
    reads |= set(re.findall(r'bp\.get\("([a-z_]+)"\)', inspect.getsource(craft_render)))
    # A leading underscore means the stage set it on the blueprint itself, in
    # this run — intra-stage state, not something a producer owes it.
    reads = {f for f in reads if not f.startswith("_")}
    # Written by other stages onto the blueprint, or fail-open by design.
    exempt = {
        "source_score",
        "footage_free",
        "download_url",
        "media",
        "audio",
        "storyboard",
        "record_id",
        "id",
        "sport",
    }
    produced = inspect.getsource(push_to_backlog)
    missing = [f for f in sorted(reads - exempt) if f'"{f}"' not in produced]
    assert not missing, (
        f"the ACTION chain reads {missing} off the blueprint and PushToBacklog "
        f"writes none of them — every one is a silent route to STILL"
    )
