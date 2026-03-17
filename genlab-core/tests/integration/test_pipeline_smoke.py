"""End-to-end pipeline smoke tests — R4.

Parameterized tests that run a mini-pipeline for each of the 5 niches
with all external APIs mocked. Verifies that:

  1. Stages execute in order without exceptions
  2. Stories are fetched (count > 0)
  3. Blueprints are composed (count > 0)
  4. A run_report is generated with expected keys
  5. Stage timings are recorded
  6. Real shared stages (ExpressLane, QCGates, ViralityScoring, RunReport)
     interoperate correctly with mock data

Run with: ``pytest -m integration genlab-core/tests/integration/``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from genlab_core.context import PipelineContext
from genlab_core.pipeline.pipeline_runner import GenericPipelineRunner

# ---------------------------------------------------------------------------
# Ensure the tests directory is importable so importlib can resolve
# mock stage class paths like "tests.integration.mock_stages.MockFetch..."
# ---------------------------------------------------------------------------
_TESTS_ROOT = Path(__file__).resolve().parent.parent  # genlab-core/tests/
_GENLAB_CORE = _TESTS_ROOT.parent  # genlab-core/
for _p in (str(_TESTS_ROOT), str(_GENLAB_CORE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Now we can import mock stages and config builder
from integration.mock_stages import (  # noqa: E402
    MockFetchTrendingVideos,
    MockRenderVisuals,
    MockScoreAndFilter,
    MockVideoGate,
    MockWriteContent,
)

SUPPORTED_NICHE_IDS = ["gaming", "sports", "movies", "anime", "ai_creators"]

# Module path used in niche configs to reference mock stages.
_MOCK_MODULE = "integration.mock_stages"


# ── Config builder (local copy to avoid conftest import issues) ────────────


def _build_mock_niche_config(niche_id: str) -> dict[str, Any]:
    """Build a minimal niche config with mock pipeline stages."""
    return {
        "niche_id": niche_id,
        "display_name": f"Test{niche_id.title()}",
        "accent_color": "#FF0000",
        "video_sourcing": {
            "strategy": "trending_youtube",
            "niche_category": niche_id,
            "video_gate": "require",
            "fallback_to_text_render": False,
        },
        "pipeline": {
            "stages": [
                {"class": f"{_MOCK_MODULE}.MockFetchTrendingVideos", "enabled": True},
                {"class": f"{_MOCK_MODULE}.MockScoreAndFilter", "enabled": True},
                {"class": f"{_MOCK_MODULE}.MockVideoGate", "enabled": True},
                {"class": f"{_MOCK_MODULE}.MockWriteContent", "enabled": True},
                {"class": f"{_MOCK_MODULE}.MockRenderVisuals", "enabled": True},
                # Real shared stages:
                {"class": "genlab_core.pipeline.stages.express_lane.ExpressLane", "enabled": True},
                {"class": "genlab_core.pipeline.stages.qc_gates.QCGates", "enabled": True},
                {"class": "genlab_core.pipeline.stages.virality_scoring.ViralityScoring", "enabled": True},
                # Mock external-dependent stages:
                {"class": f"{_MOCK_MODULE}.MockPushToBacklog", "enabled": True},
                {"class": f"{_MOCK_MODULE}.MockFetchInsights", "enabled": True},
                {"class": f"{_MOCK_MODULE}.MockPerformanceLearner", "enabled": True},
                # Real RunReport:
                {"class": "genlab_core.pipeline.stages.run_report.RunReport", "enabled": True},
            ],
        },
        "feature_flags": {},
    }


# ── Helpers ────────────────────────────────────────────────────────────────


def _run_pipeline(
    niche_id: str,
    tmp_path: Path,
    *,
    config_override: dict[str, Any] | None = None,
) -> PipelineContext:
    """Run the pipeline for a given niche with fully mocked externals.

    Patches niche_loader so GenericPipelineRunner uses our mock config
    instead of reading from the filesystem.
    """
    genlab_root = tmp_path / "genlab"
    genlab_root.mkdir(exist_ok=True)
    (genlab_root / ".tmp").mkdir(exist_ok=True)

    niche_root = tmp_path / "niche" / niche_id
    niche_root.mkdir(parents=True, exist_ok=True)

    config = config_override or _build_mock_niche_config(niche_id)

    runner = GenericPipelineRunner(
        niche_roots={niche_id: niche_root},
        genlab_root=genlab_root,
    )

    # Patch niche_loader functions so they return our mock config
    with (
        patch(
            "genlab_core.pipeline.pipeline_runner.load_niche_config",
            return_value=config,
        ),
        patch(
            "genlab_core.pipeline.pipeline_runner.get_feature_flags",
            return_value=config.get("feature_flags", {}),
        ),
    ):
        ctx = runner.run(niche_id)

    return ctx


def _build_context_after_mock_stages(niche_id: str) -> dict[str, Any]:
    """Build a context dict as it would look after all mock stages run.

    Used for testing real shared stages in isolation.
    """
    ctx: dict[str, Any] = {
        "niche_id": niche_id,
        "niche_config": _build_mock_niche_config(niche_id),
        "stories": [],
        "blueprints": [],
        "run_stats": {},
        "feature_flags": {},
    }
    MockFetchTrendingVideos().execute(ctx)
    MockScoreAndFilter().execute(ctx)
    MockVideoGate().execute(ctx)
    MockWriteContent().execute(ctx)
    MockRenderVisuals().execute(ctx)
    return ctx


# ── Tests: Full pipeline smoke ─────────────────────────────────────────────


@pytest.mark.integration
class TestPipelineSmokeAllNiches:
    """Parameterized smoke tests that run for every supported niche."""

    @pytest.mark.parametrize("niche_id", SUPPORTED_NICHE_IDS)
    def test_pipeline_completes_without_exceptions(
        self, niche_id: str, tmp_path: Path,
    ) -> None:
        """Pipeline runs all stages end-to-end without raising."""
        ctx = _run_pipeline(niche_id, tmp_path)
        assert not ctx.is_aborted, (
            f"Pipeline for '{niche_id}' was aborted. Errors: {ctx.errors}"
        )

    @pytest.mark.parametrize("niche_id", SUPPORTED_NICHE_IDS)
    def test_stories_fetched(self, niche_id: str, tmp_path: Path) -> None:
        """At least one story is fetched from mock data."""
        ctx = _run_pipeline(niche_id, tmp_path)
        assert len(ctx.stories) > 0, (
            f"Expected stories > 0 for '{niche_id}', got {len(ctx.stories)}"
        )

    @pytest.mark.parametrize("niche_id", SUPPORTED_NICHE_IDS)
    def test_blueprints_composed(self, niche_id: str, tmp_path: Path) -> None:
        """At least one blueprint is created from stories."""
        ctx = _run_pipeline(niche_id, tmp_path)
        assert len(ctx.blueprints) > 0, (
            f"Expected blueprints > 0 for '{niche_id}', got {len(ctx.blueprints)}"
        )

    @pytest.mark.parametrize("niche_id", SUPPORTED_NICHE_IDS)
    def test_run_report_generated(self, niche_id: str, tmp_path: Path) -> None:
        """RunReport stage generates a valid report file."""
        ctx = _run_pipeline(niche_id, tmp_path)
        report_path = ctx.run_stats.get("report_path")
        assert report_path, f"No report_path in run_stats for '{niche_id}'"
        assert Path(report_path).exists(), (
            f"Report file does not exist: {report_path}"
        )

        report = json.loads(Path(report_path).read_text())
        assert report["niche_id"] == niche_id
        assert report["status"] in ("success", "partial")
        assert "metrics" in report
        assert "stage_timings" in report
        assert report["metrics"]["stories_count"] > 0

    @pytest.mark.parametrize("niche_id", SUPPORTED_NICHE_IDS)
    def test_stage_timings_recorded(self, niche_id: str, tmp_path: Path) -> None:
        """Every stage records its execution time in run_stats."""
        ctx = _run_pipeline(niche_id, tmp_path)
        timings = ctx.run_stats.get("_stage_timings", {})
        assert len(timings) > 0, (
            f"No stage timings recorded for '{niche_id}'"
        )
        for stage_name, elapsed in timings.items():
            assert isinstance(elapsed, float), (
                f"Timing for {stage_name} is not a float: {type(elapsed)}"
            )
            assert elapsed >= 0, (
                f"Negative timing for {stage_name}: {elapsed}"
            )

    @pytest.mark.parametrize("niche_id", SUPPORTED_NICHE_IDS)
    def test_no_fatal_errors(self, niche_id: str, tmp_path: Path) -> None:
        """Pipeline completes with zero fatal errors."""
        ctx = _run_pipeline(niche_id, tmp_path)
        fatal_errors = [e for e in ctx.errors if e.get("fatal")]
        assert len(fatal_errors) == 0, (
            f"Fatal errors in '{niche_id}': {fatal_errors}"
        )


# ── Tests: Shared stages with mock data ────────────────────────────────────


@pytest.mark.integration
class TestSharedStagesWithMockData:
    """Test that real shared stages work correctly with mock pipeline data."""

    def test_express_lane_classifies_stories(self) -> None:
        """ExpressLane assigns urgency_classification to every story."""
        from genlab_core.pipeline.stages.express_lane import ExpressLane

        ctx = _build_context_after_mock_stages("gaming")
        result = ExpressLane().execute(ctx)

        for story in result["stories"]:
            assert "urgency_classification" in story, (
                f"Story '{story.get('title')}' missing urgency_classification"
            )
            uc = story["urgency_classification"]
            assert uc["urgency"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
            assert isinstance(uc["score"], float)
            assert isinstance(uc["express"], bool)

        assert "express_lane" in result.get("run_stats", {})

    def test_qc_gates_validates_blueprints(self) -> None:
        """QCGates produces validation_status for every blueprint."""
        from genlab_core.pipeline.stages.qc_gates import QCGates

        ctx = _build_context_after_mock_stages("sports")
        result = QCGates().execute(ctx)

        for bp in result["blueprints"]:
            assert "validation_status" in bp, (
                f"Blueprint '{bp.get('candidate_id')}' missing validation_status"
            )
            vs = bp["validation_status"]
            assert "all_passed" in vs

        qc_stats = result.get("run_stats", {}).get("qc", {})
        assert "passed" in qc_stats
        assert "total" in qc_stats

    def test_virality_scoring_scores_blueprints(self) -> None:
        """ViralityScoring assigns virality_score to every blueprint."""
        from genlab_core.pipeline.stages.virality_scoring import ViralityScoring

        ctx = _build_context_after_mock_stages("movies")
        result = ViralityScoring().execute(ctx)

        for bp in result["blueprints"]:
            assert "virality_score" in bp, (
                f"Blueprint '{bp.get('candidate_id')}' missing virality_score"
            )
            assert isinstance(bp["virality_score"], float)
            assert 0.0 <= bp["virality_score"] <= 1.0

        virality_stats = result.get("run_stats", {}).get("virality", {})
        assert "scored" in virality_stats
        assert virality_stats["scored"] > 0

    def test_run_report_writes_json(self, tmp_path: Path) -> None:
        """RunReport writes a valid JSON file with all expected fields."""
        from genlab_core.pipeline.stages.run_report import RunReport

        ctx = _build_context_after_mock_stages("anime")
        run_dir = tmp_path / "run_output"
        run_dir.mkdir()
        ctx["run_dir"] = str(run_dir)
        ctx["run_id"] = "anime_20260317_120000"

        result = RunReport().execute(ctx)

        report_path = result.get("run_stats", {}).get("report_path")
        assert report_path is not None
        assert Path(report_path).exists()

        report = json.loads(Path(report_path).read_text())
        assert report["niche_id"] == "anime"
        assert report["run_id"] == "anime_20260317_120000"
        assert report["status"] in ("success", "partial", "failed")
        assert "metrics" in report
        assert "stage_timings" in report
        assert "slo_violations" in report


# ── Tests: Edge cases ──────────────────────────────────────────────────────


@pytest.mark.integration
class TestPipelineEdgeCases:
    """Edge case tests for the pipeline runner with mock stages."""

    def test_empty_stories_pipeline(self, tmp_path: Path) -> None:
        """Pipeline handles zero stories gracefully (no exceptions)."""
        config = _build_mock_niche_config("gaming")
        config["pipeline"]["stages"][0] = {
            "class": f"{_MOCK_MODULE}.MockEmptyFetch",
            "enabled": True,
        }
        ctx = _run_pipeline("gaming", tmp_path, config_override=config)
        assert not ctx.is_aborted

    def test_pipeline_records_stage_count(self, tmp_path: Path) -> None:
        """Pipeline runs all enabled stages and records timing for each."""
        ctx = _run_pipeline("gaming", tmp_path)
        timings = ctx.run_stats.get("_stage_timings", {})
        # We defined 12 stages in mock config
        assert len(timings) == 12, (
            f"Expected 12 stage timings, got {len(timings)}: "
            f"{list(timings.keys())}"
        )

    def test_disabled_stages_are_skipped(self, tmp_path: Path) -> None:
        """Stages with enabled=false are not executed."""
        config = _build_mock_niche_config("sports")
        config["pipeline"]["stages"][1]["enabled"] = False
        ctx = _run_pipeline("sports", tmp_path, config_override=config)
        timings = ctx.run_stats.get("_stage_timings", {})
        assert "MockScoreAndFilter" not in timings

    def test_niche_id_propagated_to_stories(self, tmp_path: Path) -> None:
        """Every story has the correct niche_id set."""
        ctx = _run_pipeline("anime", tmp_path)
        for story in ctx.stories:
            assert story.get("niche_id") == "anime", (
                f"Story '{story.get('title')}' has wrong niche_id: "
                f"{story.get('niche_id')}"
            )

    def test_blueprints_have_required_fields(self, tmp_path: Path) -> None:
        """Every blueprint has candidate_id, hook, format, and sources."""
        ctx = _run_pipeline("movies", tmp_path)
        for bp in ctx.blueprints:
            assert bp.get("candidate_id"), "Blueprint missing candidate_id"
            assert bp.get("hook"), "Blueprint missing hook"
            assert bp.get("format") == "reel", (
                f"Blueprint format is '{bp.get('format')}', expected 'reel'"
            )
            assert bp.get("sources") or bp.get("source_urls"), (
                "Blueprint missing sources"
            )

    def test_express_lane_sorts_critical_first(self, tmp_path: Path) -> None:
        """After ExpressLane, CRITICAL stories appear before LOW stories."""
        ctx = _run_pipeline("ai_creators", tmp_path)
        urgencies = [
            s.get("urgency_classification", {}).get("urgency", "LOW")
            for s in ctx.stories
        ]
        level_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        orders = [level_order.get(u, 9) for u in urgencies]
        assert orders == sorted(orders), (
            f"Stories not sorted by urgency: {urgencies}"
        )
