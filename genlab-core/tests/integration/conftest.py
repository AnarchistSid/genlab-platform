"""Shared fixtures for integration smoke tests.

Provides fixture data loaders and niche config builders used by
test_pipeline_smoke.py. Mock stage classes live in mock_stages.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

# ── Fixture paths ─────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> Dict[str, Any]:
    """Load a JSON fixture file by name."""
    path = FIXTURES_DIR / name
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Fixture data loaders ─────────────────────────────────────────────────


@pytest.fixture()
def youtube_fixture() -> Dict[str, Any]:
    """Mock YouTube trending API response."""
    return _load_fixture("mock_youtube_trending.json")


@pytest.fixture()
def sharepoint_fixture() -> Dict[str, Any]:
    """Mock empty SharePoint lists."""
    return _load_fixture("mock_sharepoint_empty.json")


@pytest.fixture()
def anthropic_fixture() -> Dict[str, Any]:
    """Mock Anthropic LLM response."""
    return _load_fixture("mock_anthropic_response.json")


# ── Temp directories ──────────────────────────────────────────────────────


@pytest.fixture()
def tmp_genlab_root(tmp_path: Path) -> Path:
    """Create a temporary GenLab root directory with required subdirs."""
    tmp_dir = tmp_path / ".tmp"
    tmp_dir.mkdir()
    runs_dir = tmp_dir / "runs"
    runs_dir.mkdir()
    return tmp_path


@pytest.fixture()
def tmp_niche_root(tmp_path: Path) -> Path:
    """Create a temporary niche root directory with config/niche.yaml."""
    niche_dir = tmp_path / "niche_root"
    config_dir = niche_dir / "config"
    config_dir.mkdir(parents=True)
    return niche_dir


# ── Niche config builders ──────────────────────────────────────────────────

# The module path for mock stages — must be importable by importlib.
_MOCK_MODULE = "tests.integration.mock_stages"

SUPPORTED_NICHE_IDS = ["gaming", "sports", "movies", "anime", "ai_creators"]


def build_mock_niche_config(niche_id: str) -> Dict[str, Any]:
    """Build a minimal niche config with mock pipeline stages.

    The stages reference mock classes in mock_stages.py, allowing
    GenericPipelineRunner._load_stages() to import and instantiate them.
    Real shared stages (ExpressLane, QCGates, ViralityScoring, RunReport)
    are included to verify they interoperate with mock data.
    """
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
                # Real shared stages that should work with mock data:
                {"class": "genlab_core.pipeline.stages.express_lane.ExpressLane", "enabled": True},
                {"class": "genlab_core.pipeline.stages.qc_gates.QCGates", "enabled": True},
                {"class": "genlab_core.pipeline.stages.virality_scoring.ViralityScoring", "enabled": True},
                # Mock external-dependent stages:
                {"class": f"{_MOCK_MODULE}.MockPushToBacklog", "enabled": True},
                {"class": f"{_MOCK_MODULE}.MockFetchInsights", "enabled": True},
                {"class": f"{_MOCK_MODULE}.MockPerformanceLearner", "enabled": True},
                # Real RunReport to verify report generation:
                {"class": "genlab_core.pipeline.stages.run_report.RunReport", "enabled": True},
            ],
        },
        "feature_flags": {},
    }


# ── Parameterized niche fixture ──────────────────────────────────────────


@pytest.fixture(params=SUPPORTED_NICHE_IDS)
def niche_id(request: pytest.FixtureRequest) -> str:
    """Parameterized niche_id — runs tests for each of the 5 niches."""
    return request.param


@pytest.fixture()
def mock_niche_config(niche_id: str) -> Dict[str, Any]:
    """Build mock niche config for the current niche."""
    return build_mock_niche_config(niche_id)
