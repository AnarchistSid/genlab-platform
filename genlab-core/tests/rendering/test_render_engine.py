"""Which engine publishes, and what cannot happen by accident."""

import pytest
from genlab_core.rendering.render_engine import (
    Engine,
    craft_publishes,
    dual_render_enabled,
    resolve_engine,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("GENLAB_RENDER_ENGINE", "GENLAB_RENDER_ENGINE_SPORTS",
              "GENLAB_RENDER_DUAL", "GENLAB_RENDER_DUAL_SPORTS"):
        monkeypatch.delenv(k, raising=False)


def test_default_is_legacy_with_no_config_at_all():
    """FFmpeg publishing standalone is the standing guarantee."""
    assert resolve_engine("sports") is Engine.LEGACY
    assert resolve_engine("sports", {}) is Engine.LEGACY


def test_config_selects_craft():
    assert resolve_engine("sports", {"render": {"engine": "craft"}}) is Engine.CRAFT


def test_per_niche_env_beats_config():
    import os
    os.environ["GENLAB_RENDER_ENGINE_SPORTS"] = "legacy"
    try:
        assert resolve_engine("sports", {"render": {"engine": "craft"}}) is Engine.LEGACY
    finally:
        del os.environ["GENLAB_RENDER_ENGINE_SPORTS"]


def test_global_env_applies_when_no_per_niche_override(monkeypatch):
    monkeypatch.setenv("GENLAB_RENDER_ENGINE", "craft")
    assert resolve_engine("anime") is Engine.CRAFT


def test_an_unknown_engine_name_falls_back_to_legacy_not_craft():
    """A typo must never promote the unproven renderer."""
    assert resolve_engine("sports", {"render": {"engine": "craftt"}}) is Engine.LEGACY
    assert resolve_engine("sports", {"render": {"engine": ""}}) is Engine.LEGACY


def test_a_niche_with_no_render_block_is_legacy():
    assert resolve_engine("gaming", {"video_sourcing": {}}) is Engine.LEGACY


def test_dual_render_is_a_separate_question_from_which_engine_publishes():
    """Conflating them is how a shadow renderer publishes by accident."""
    cfg = {"render": {"engine": "legacy", "dual": True}}
    assert resolve_engine("sports", cfg) is Engine.LEGACY
    assert dual_render_enabled("sports", cfg) is True
    assert craft_publishes("sports", cfg) is False


def test_dual_defaults_off():
    assert dual_render_enabled("sports") is False
    assert dual_render_enabled("sports", {"render": {"engine": "craft"}}) is False


def test_dual_can_be_killed_by_env_without_a_deploy(monkeypatch):
    monkeypatch.setenv("GENLAB_RENDER_DUAL_SPORTS", "0")
    assert dual_render_enabled("sports", {"render": {"dual": True}}) is False


def test_craft_publishes_only_when_engine_is_craft():
    assert craft_publishes("sports", {"render": {"engine": "craft"}}) is True
    assert craft_publishes("sports", {"render": {"engine": "legacy"}}) is False
