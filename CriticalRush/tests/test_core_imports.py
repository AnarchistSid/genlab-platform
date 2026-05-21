"""Validate that genlab-core is importable and functional from CriticalRush."""

from genlab_core.context import PipelineContext
from genlab_core.settings import settings
from genlab_core.strategies import ContentResearchStrategy


def test_pipeline_context_construction():
    ctx = PipelineContext(niche_id="gaming", run_id="test_001")
    assert ctx.niche_id == "gaming"


def test_feature_flag_default():
    ctx = PipelineContext(niche_id="gaming", run_id="test_001")
    assert not ctx.get_flag("use_gaming_clip_sourcer", default=False)


def test_settings_importable():
    assert settings is not None


def test_strategy_is_abstract():
    assert hasattr(ContentResearchStrategy, "execute")


def test_project_root_resolution():
    from pathlib import Path
    root = settings.get_project_root()
    assert isinstance(root, Path)
    assert root.exists()
    assert root.name != "genlab-core"
