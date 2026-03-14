"""Tests for genlab_core.pipeline.pipeline_runner — Track B Step B2.

These tests use mock stages and a minimal niche config to verify the
generic pipeline runner in isolation. No real niche modules are imported.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest

from genlab_core.exceptions import NicheConfigError
from genlab_core.pipeline.pipeline_runner import GenericPipelineRunner


# ── Helpers ──────────────────────────────────────────────────────────────────


class _PassStage:
    """Stage that succeeds and records its name."""

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
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

    def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:
        raise RuntimeError("boom")


class StageFail(_FailStage):
    pass


def _make_config(*stage_classes: type, parallel_groups: dict | None = None) -> Dict[str, Any]:
    """Build a minimal niche config with pipeline.stages declarations."""
    groups = parallel_groups or {}
    stages = []
    for cls in stage_classes:
        decl: Dict[str, Any] = {
            "class": f"{cls.__module__}.{cls.__name__}",
            "enabled": True,
        }
        if cls.__name__ in groups:
            decl["parallel_group"] = groups[cls.__name__]
        stages.append(decl)
    return {"pipeline": {"stages": stages}}


DUMMY_ROOT = Path("/tmp/genlab-test-runner")
DUMMY_GENLAB = Path("/tmp/genlab-test")


def _make_runner(config: Dict[str, Any], niche_id: str = "test") -> GenericPipelineRunner:
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


def test_runner_raises_on_unknown_niche() -> None:
    """Calling run() with an unsupported niche raises ValueError."""
    runner = GenericPipelineRunner(
        niche_roots={"gaming": DUMMY_ROOT},
        genlab_root=DUMMY_GENLAB,
    )
    with pytest.raises(ValueError, match="Unsupported niche 'bogus'"):
        runner.run("bogus")


def test_runner_groups_parallel_stages() -> None:
    """Consecutive stages with the same parallel_group are batched together."""
    config = _make_config(
        StageA, StageB, StageC,
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
