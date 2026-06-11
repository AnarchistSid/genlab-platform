"""Tests for genlab_core.pipeline.pipeline_runner — Track B Step B2.

These tests use mock stages and a minimal niche config to verify the
generic pipeline runner in isolation. No real niche modules are imported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from genlab_core.exceptions import NicheConfigError
from genlab_core.pipeline.pipeline_runner import GenericPipelineRunner

# ── Helpers ──────────────────────────────────────────────────────────────────


class _PassStage:
    """Stage that succeeds and records its name."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        context.setdefault("executed", []).append(self.__class__.__name__)
        return context


class StageA(_PassStage):
    pass


class StageB(_PassStage):
    pass


class StageC(_PassStage):
    pass


class _FailStage:
    """Stage that raises an error."""

    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("boom")


class StageFail(_FailStage):
    pass


class _NoExecuteStage:
    """R-07: a class lacking ``execute(context)`` — formerly accepted by
    ``_load_stages`` and only crashed mid-pipeline. After R-07 it must
    be rejected at load time with a NicheConfigError."""

    def run(self, context: dict[str, Any]) -> dict[str, Any]:  # wrong name
        return context


class StageBadProtocol(_NoExecuteStage):
    pass


# R-27 helpers — must be at module scope so ``_load_stages`` can
# importlib-resolve them via ``cls.__module__``. ``_STAGE_RECORDER_SEEN``
# is the cross-test sink; each test clears it before populating.

_STAGE_RECORDER_SEEN: list = []


class StageRecorder(_PassStage):
    """Exercises both the read- and write-paths the 4 production LLM
    callers use: reads ``get_accumulator()`` and records simulated
    Anthropic usage via ``record_anthropic_usage()``."""

    def execute(self, context):
        from genlab_core.intelligence.cost_accumulator import (
            get_accumulator,
            record_anthropic_usage,
        )

        _STAGE_RECORDER_SEEN.append(("acc_present_during_run", get_accumulator() is not None))
        usage = type("U", (), {"input_tokens": 1000, "output_tokens": 250})()
        resp = type("R", (), {"usage": usage})()
        record_anthropic_usage("claude-haiku-4-5-20251001", resp)
        return context


def _make_config(*stage_classes: type, parallel_groups: dict | None = None) -> dict[str, Any]:
    """Build a minimal niche config with pipeline.stages declarations."""
    groups = parallel_groups or {}
    stages = []
    for cls in stage_classes:
        decl: dict[str, Any] = {
            "class": f"{cls.__module__}.{cls.__name__}",
            "enabled": True,
        }
        if cls.__name__ in groups:
            decl["parallel_group"] = groups[cls.__name__]
        stages.append(decl)
    return {"pipeline": {"stages": stages}}


DUMMY_ROOT = Path("/tmp/genlab-test-runner")
DUMMY_GENLAB = Path("/tmp/genlab-test")


def _make_runner(config: dict[str, Any], niche_id: str = "test") -> GenericPipelineRunner:
    """Create a runner with patched niche_loader returning *config*."""
    return GenericPipelineRunner(
        niche_roots={niche_id: DUMMY_ROOT},
        genlab_root=DUMMY_GENLAB,
    )


# ── Tests ────────────────────────────────────────────────────────────────────


def test_runner_loads_stages_from_config() -> None:
    """Stages declared in config are loaded and returned in order."""
    config = _make_config(StageA, StageB, StageC)
    runner = GenericPipelineRunner(
        niche_roots={"test": DUMMY_ROOT},
        genlab_root=DUMMY_GENLAB,
    )
    stages, decls = runner._load_stages("test", config)
    assert len(stages) == 3
    names = [s.__class__.__name__ for s in stages]
    assert names == ["StageA", "StageB", "StageC"]


def test_runner_skips_disabled_stages() -> None:
    """Stages with enabled=false are excluded from the loaded list."""
    config = _make_config(StageA, StageB)
    # Disable StageB
    config["pipeline"]["stages"][1]["enabled"] = False
    runner = GenericPipelineRunner(
        niche_roots={"test": DUMMY_ROOT},
        genlab_root=DUMMY_GENLAB,
    )
    stages, _ = runner._load_stages("test", config)
    assert len(stages) == 1
    assert stages[0].__class__.__name__ == "StageA"


def test_runner_raises_on_missing_pipeline_config() -> None:
    """Missing pipeline.stages in config raises NicheConfigError."""
    runner = GenericPipelineRunner(
        niche_roots={"test": DUMMY_ROOT},
        genlab_root=DUMMY_GENLAB,
    )
    with pytest.raises(NicheConfigError, match="missing pipeline.stages"):
        runner._load_stages("test", {})


def test_r07_load_stages_rejects_class_missing_execute() -> None:
    """R-07: a niche.yaml entry pointing at a class without
    ``execute(context)`` used to land in the stages list and only crash
    when the runner reached it. After R-07 the Stage Protocol is
    runtime-checked at load time and the runner raises NicheConfigError
    with a helpful message naming the offending class."""
    config = _make_config(StageA, StageBadProtocol)
    runner = GenericPipelineRunner(
        niche_roots={"test": DUMMY_ROOT},
        genlab_root=DUMMY_GENLAB,
    )
    with pytest.raises(NicheConfigError, match="StageBadProtocol.*execute"):
        runner._load_stages("test", config)


def test_runner_raises_on_unknown_niche() -> None:
    """Calling run() with an unsupported niche raises ValueError."""
    runner = GenericPipelineRunner(
        niche_roots={"gaming": DUMMY_ROOT},
        genlab_root=DUMMY_GENLAB,
    )
    with pytest.raises(ValueError, match="Unsupported niche 'bogus'"):
        runner.run("bogus")


def test_run_flushes_metrics_jsonl(tmp_path, monkeypatch) -> None:
    """R-66: a completed run flushes per-stage timing to <run_dir>/metrics.jsonl.

    Previously StageRunnerFactory was built with no metrics= arg, so the
    metrics writer was dead code and metrics.jsonl was never produced.
    """
    from genlab_core.pipeline import pipeline_runner as pr

    config = _make_config(StageA, StageB)
    monkeypatch.setattr(pr, "load_niche_config", lambda niche_id, niche_root: dict(config))
    monkeypatch.setattr(pr, "get_feature_flags", lambda niche_id, niche_root: {})

    runner = GenericPipelineRunner(
        niche_roots={"test": tmp_path / "niche"},
        genlab_root=tmp_path,
    )
    runner.run("test")

    metrics_files = list((tmp_path / ".tmp" / "runs").glob("test_*/metrics.jsonl"))
    assert metrics_files, "metrics.jsonl was not written"
    content = metrics_files[0].read_text()
    assert "StageA" in content
    assert "StageB" in content
    assert "pipeline_summary" in content


def test_r27_runner_installs_cost_accumulator_visible_to_stages(tmp_path, monkeypatch) -> None:
    """R-27: ``pipeline_runner.run()`` must install a
    ``CostAccumulator`` via ``set_accumulator()`` so that every stage's
    ``record_anthropic_usage()`` call lands in a real bucket. Before
    R-27 the contextvar's default was ``None`` and every record
    short-circuited at the ``if acc is None: return`` guard — every
    LLM call site spent budget invisibly.
    """
    from genlab_core.intelligence.cost_accumulator import get_accumulator
    from genlab_core.pipeline import pipeline_runner as pr

    # ``StageRecorder`` is defined at MODULE level (below) because
    # ``_load_stages`` calls ``importlib.import_module(module_path)``;
    # a class defined inside this function isn't importable.
    _STAGE_RECORDER_SEEN.clear()

    config = _make_config(StageRecorder)
    monkeypatch.setattr(pr, "load_niche_config", lambda niche_id, niche_root: dict(config))
    monkeypatch.setattr(pr, "get_feature_flags", lambda niche_id, niche_root: {})

    runner = GenericPipelineRunner(
        niche_roots={"test": tmp_path / "niche"},
        genlab_root=tmp_path,
    )
    runner.run("test")

    # Stage saw an accumulator during execution.
    assert _STAGE_RECORDER_SEEN == [("acc_present_during_run", True)], (
        "R-27 regression: ``get_accumulator()`` returned None inside a "
        "stage. ``pipeline_runner.run()`` is not installing a CostAccumulator."
    )

    # Accumulator is RESET after the run — leaking it would let a later
    # niche's costs pile onto the previous run's bucket.
    assert get_accumulator() is None, (
        "R-27 regression: the cost-accumulator contextvar leaked out of "
        "the run. ``reset_accumulator(...)`` must fire in the finally block."
    )


def test_runner_groups_parallel_stages() -> None:
    """Consecutive stages with the same parallel_group are batched together."""
    config = _make_config(
        StageA,
        StageB,
        StageC,
        parallel_groups={"StageA": "fetch", "StageB": "fetch"},
    )
    runner = GenericPipelineRunner(
        niche_roots={"test": DUMMY_ROOT},
        genlab_root=DUMMY_GENLAB,
    )
    stages, decls = runner._load_stages("test", config)
    batches = runner._group_stages(stages, decls)
    # StageA + StageB in one batch, StageC alone
    assert len(batches) == 2
    assert len(batches[0]) == 2  # parallel
    assert len(batches[1]) == 1  # sequential
